import base64
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from devflow.config import (
    load_routing_config,
    validate_routing_config,
    canonical_hash,
    convert_legacy_roles_to_v12,
    RoutingConfigError,
    CONFIG
)
from devflow.executor import (
    ProviderExecutor,
    TechnicalError,
    AvailabilityError,
    RateLimitError,
    AuthError,
    NetworkTimeoutError,
    InvalidEnvelopeError
)
from devflow.router import RoleRouter
from devflow.db import DB
from devflow.worker import Worker, json_object
from devflow.migration import migrate_roles_to_agent_routing
from devflow.doctor import run_doctor, check_routing_config

def create_mock_config(overrides=None):
    base = {
        "schema_version": 1,
        "default_fallback_on": ["PROVIDER_UNAVAILABLE", "RATE_LIMITED", "TIMEOUT", "INVALID_JSON"],
        "connections": {
            "conn-a": {
                "type": "openai-compatible",
                "base_url": "http://127.0.0.1:9001/v1",
                "auth": {"type": "env", "env": "MOCK_KEY_A"},
                "quota_group": "group-a",
                "enabled": True,
                "timeout": 30
            },
            "conn-b": {
                "type": "openai-compatible",
                "base_url": "http://127.0.0.1:9002/v1",
                "auth": {"type": "env", "env": "MOCK_KEY_B"},
                "quota_group": "group-b",
                "enabled": True,
                "timeout": 30
            },
            "conn-c": {
                "type": "openai-compatible",
                "base_url": "http://127.0.0.1:9003/v1",
                "auth": {"type": "env", "env": "MOCK_KEY_C"},
                "quota_group": "group-c",
                "enabled": True,
                "timeout": 30
            }
        },
        "roles": {
            "ENGINEER": {
                "strategy": "sequential",
                "candidates": [
                    {"id": "eng-1", "connection": "conn-a", "model": "model-a1", "enabled": True, "technical_retries": 1},
                    {"id": "eng-2", "connection": "conn-b", "model": "model-b1", "enabled": True, "technical_retries": 1},
                    {"id": "eng-3", "connection": "conn-c", "model": "model-c1", "enabled": True, "technical_retries": 1}
                ]
            },
            "DEV:BACKEND:SIMPLE": {
                "strategy": "sequential",
                "candidates": [
                    {"id": "dev-1", "connection": "conn-a", "model": "model-a1", "enabled": True, "technical_retries": 1},
                    {"id": "dev-2", "connection": "conn-b", "model": "model-b1", "enabled": True, "technical_retries": 1}
                ]
            }
        }
    }
    if overrides:
        base.update(overrides)
    return base

class MockExecutor:
    def __init__(self, outcomes=None):
        # outcomes: dict mapping (connection_name, model) or call index to lambda/exception/result
        self.outcomes = outcomes or {}
        self.calls = []

    def execute(self, connection_name, connection_cfg, model, messages, temperature=0.1, max_tokens=12000, timeout=None):
        self.calls.append((connection_name, model, messages))
        key = (connection_name, model)
        
        if key in self.outcomes:
            handler = self.outcomes[key]
            if callable(handler):
                return handler(len(self.calls))
            elif isinstance(handler, Exception):
                raise handler
            elif isinstance(handler, type) and issubclass(handler, Exception):
                raise handler("MOCK_ERROR", "Simulated error")
            return handler
            
        # Default mock response
        return {
            'choices': [{'message': {'content': json.dumps({'result': 'DONE', 'summary': 'mocked ok'})}}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 50}
        }

class TestDeclarativeRouting(unittest.TestCase):

    def test_01_valid_json_config(self):
        cfg = create_mock_config()
        self.assertTrue(validate_routing_config(cfg))
        
        # Test real agent-routing.json
        real_cfg = load_routing_config()
        self.assertIn("connections", real_cfg)
        self.assertIn("roles", real_cfg)

    def test_02_invalid_json_config(self):
        with self.assertRaises(RoutingConfigError):
            validate_routing_config({"invalid": True})
        with self.assertRaises(RoutingConfigError):
            validate_routing_config({"schema_version": 0, "connections": {}, "roles": {}})

    def test_03_unknown_connection_rejected(self):
        cfg = create_mock_config()
        cfg["roles"]["ENGINEER"]["candidates"].append({
            "id": "bad-cand", "connection": "non-existent-conn", "model": "some-model"
        })
        with self.assertRaises(RoutingConfigError) as ctx:
            validate_routing_config(cfg)
        self.assertIn("references unknown connection", str(ctx.exception))

    def test_04_unknown_role_handling(self):
        with tempfile.TemporaryDirectory() as td:
            db = DB(Path(td) / "test.sqlite3")
            router = RoleRouter(db)
            cfg = create_mock_config()
            with self.assertRaises(KeyError):
                router.resolve_role_def("NON_EXISTENT_ROLE", routing_config=cfg)

    def test_05_empty_model_rejected(self):
        cfg = create_mock_config()
        cfg["roles"]["ENGINEER"]["candidates"][0]["model"] = "   "
        with self.assertRaises(RoutingConfigError) as ctx:
            validate_routing_config(cfg)
        self.assertIn("empty model", str(ctx.exception))

    def test_06_literal_secret_rejected(self):
        cfg = create_mock_config()
        cfg["connections"]["conn-a"]["api_key"] = "sk-literal-secret-12345"
        with self.assertRaises(RoutingConfigError) as ctx:
            validate_routing_config(cfg)
        self.assertIn("literal secret found", str(ctx.exception))

    def test_07_n_candidates_supported(self):
        cfg = create_mock_config()
        # Add 10 candidates
        cands = []
        for i in range(10):
            cands.append({
                "id": f"cand-{i+1}",
                "connection": "conn-a",
                "model": f"model-var-{i+1}",
                "enabled": True,
                "technical_retries": 1
            })
        cfg["roles"]["MANY_CANDS"] = {"strategy": "sequential", "candidates": cands}
        self.assertTrue(validate_routing_config(cfg))
        
        with tempfile.TemporaryDirectory() as td:
            db = DB(Path(td) / "test.sqlite3")
            router = RoleRouter(db)
            resolved_cands = router.candidates("MANY_CANDS", routing_config=cfg)
            self.assertEqual(len(resolved_cands), 10)

    def test_08_candidate_order_preserved(self):
        cfg = create_mock_config()
        with tempfile.TemporaryDirectory() as td:
            db = DB(Path(td) / "test.sqlite3")
            router = RoleRouter(db)
            cands = router.candidates("ENGINEER", routing_config=cfg)
            self.assertEqual([c[1]['id'] for c in cands], ["eng-1", "eng-2", "eng-3"])
            self.assertEqual([c[2] for c in cands], ["conn-a", "conn-b", "conn-c"])

    def test_09_candidate_1_pass(self):
        with tempfile.TemporaryDirectory() as td:
            db = DB(Path(td) / "test.sqlite3")
            cfg = create_mock_config()
            mock_exec = MockExecutor({
                ("conn-a", "model-a1"): {
                    'choices': [{'message': {'content': json.dumps({'tasks': [{'title': 'sum', 'area': 'BACKEND', 'complexity': 'SIMPLE', 'criticality': 'NORMAL', 'objective': 'obj', 'acceptance_criteria': ['ok']}]})}}],
                    'usage': {'prompt_tokens': 10, 'completion_tokens': 20}
                }
            })
            router = RoleRouter(db, executor=mock_exec)
            worker = Worker(db, router=router, worktrees=Path(td)/'wt')
            
            # Setup vault
            vault = Path(td) / 'vault'
            (vault / '08-QA-e-Auditoria').mkdir(parents=True)
            repo = Path(td) / 'repo'
            repo.mkdir()
            
            wid = db.create('proj', str(repo), 'test roadmap', vault_path=str(vault), routing_config=cfg)
            w = db.get(wid)
            worker.step(wid)
            
            # Verify only conn-a was called
            self.assertEqual(len(mock_exec.calls), 1)
            self.assertEqual(mock_exec.calls[0][0], "conn-a")
            self.assertEqual(mock_exec.calls[0][1], "model-a1")
            
            with db.con() as c:
                runs = c.execute("SELECT * FROM runs WHERE workflow_id=?", (wid,)).fetchall()
                self.assertEqual(len(runs), 1)
                self.assertEqual(runs[0]['candidate_id'], "eng-1")
                self.assertEqual(runs[0]['fallback_position'], 1)

    def test_10_candidate_1_fail_fallback_to_candidate_2(self):
        with tempfile.TemporaryDirectory() as td:
            db = DB(Path(td) / "test.sqlite3")
            cfg = create_mock_config()
            mock_exec = MockExecutor({
                ("conn-a", "model-a1"): AvailabilityError("HTTP_503", "Service Unavailable"),
                ("conn-b", "model-b1"): {
                    'choices': [{'message': {'content': json.dumps({'tasks': [{'title': 'sum', 'area': 'BACKEND', 'complexity': 'SIMPLE', 'criticality': 'NORMAL', 'objective': 'obj', 'acceptance_criteria': ['ok']}]})}}],
                    'usage': {'prompt_tokens': 15, 'completion_tokens': 25}
                }
            })
            router = RoleRouter(db, executor=mock_exec)
            worker = Worker(db, router=router, worktrees=Path(td)/'wt')
            
            vault = Path(td) / 'vault'
            (vault / '08-QA-e-Auditoria').mkdir(parents=True)
            repo = Path(td) / 'repo'
            repo.mkdir()
            
            wid = db.create('proj', str(repo), 'test roadmap', vault_path=str(vault), routing_config=cfg)
            worker.step(wid)
            
            # Should have called conn-a (with retries) and then fallen back to conn-b
            called_conns = [c[0] for c in mock_exec.calls]
            self.assertIn("conn-a", called_conns)
            self.assertIn("conn-b", called_conns)
            
            with db.con() as c:
                run = c.execute("SELECT * FROM runs WHERE workflow_id=? AND result='OK'", (wid,)).fetchone()
                self.assertEqual(run['candidate_id'], "eng-2")
                self.assertEqual(run['connection'], "conn-b")
                self.assertEqual(run['fallback_position'], 2)

    def test_11_candidate_2_fail_fallback_to_candidate_3(self):
        with tempfile.TemporaryDirectory() as td:
            db = DB(Path(td) / "test.sqlite3")
            cfg = create_mock_config()
            mock_exec = MockExecutor({
                ("conn-a", "model-a1"): AvailabilityError("HTTP_503", "Service Unavailable"),
                ("conn-b", "model-b1"): NetworkTimeoutError("TIMEOUT", "Read timed out"),
                ("conn-c", "model-c1"): {
                    'choices': [{'message': {'content': json.dumps({'tasks': [{'title': 'sum', 'area': 'BACKEND', 'complexity': 'SIMPLE', 'criticality': 'NORMAL', 'objective': 'obj', 'acceptance_criteria': ['ok']}]})}}],
                    'usage': {'prompt_tokens': 15, 'completion_tokens': 25}
                }
            })
            router = RoleRouter(db, executor=mock_exec)
            worker = Worker(db, router=router, worktrees=Path(td)/'wt')
            
            vault = Path(td) / 'vault'
            (vault / '08-QA-e-Auditoria').mkdir(parents=True)
            repo = Path(td) / 'repo'
            repo.mkdir()
            
            wid = db.create('proj', str(repo), 'test roadmap', vault_path=str(vault), routing_config=cfg)
            worker.step(wid)
            
            with db.con() as c:
                run = c.execute("SELECT * FROM runs WHERE workflow_id=? AND result='OK'", (wid,)).fetchone()
                self.assertEqual(run['candidate_id'], "eng-3")
                self.assertEqual(run['connection'], "conn-c")
                self.assertEqual(run['fallback_position'], 3)

    def test_12_invalid_json_technical_retry(self):
        with tempfile.TemporaryDirectory() as td:
            db = DB(Path(td) / "test.sqlite3")
            cfg = create_mock_config()
            
            call_count = [0]
            def handler_with_first_fail(call_idx):
                call_count[0] += 1
                if call_count[0] == 1:
                    raise InvalidEnvelopeError("INVALID_JSON", "Syntax error at char 1")
                return {
                    'choices': [{'message': {'content': json.dumps({'tasks': [{'title': 'sum', 'area': 'BACKEND', 'complexity': 'SIMPLE', 'criticality': 'NORMAL', 'objective': 'obj', 'acceptance_criteria': ['ok']}]})}}],
                    'usage': {'prompt_tokens': 10, 'completion_tokens': 20}
                }
                
            mock_exec = MockExecutor({
                ("conn-a", "model-a1"): handler_with_first_fail
            })
            router = RoleRouter(db, executor=mock_exec)
            worker = Worker(db, router=router, worktrees=Path(td)/'wt')
            
            vault = Path(td) / 'vault'
            (vault / '08-QA-e-Auditoria').mkdir(parents=True)
            repo = Path(td) / 'repo'
            repo.mkdir()
            
            wid = db.create('proj', str(repo), 'test roadmap', vault_path=str(vault), routing_config=cfg)
            worker.step(wid)
            
            # Verify conn-a succeeded on retry without fallback to conn-b
            self.assertEqual(call_count[0], 2)
            with db.con() as c:
                run = c.execute("SELECT * FROM runs WHERE workflow_id=?", (wid,)).fetchone()
                self.assertEqual(run['candidate_id'], "eng-1")
                self.assertEqual(run['technical_retry'], 1)

    def test_13_technical_retry_exhausted_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            db = DB(Path(td) / "test.sqlite3")
            cfg = create_mock_config()
            
            mock_exec = MockExecutor({
                ("conn-a", "model-a1"): InvalidEnvelopeError("INVALID_JSON", "Broken format continuously"),
                ("conn-b", "model-b1"): {
                    'choices': [{'message': {'content': json.dumps({'tasks': [{'title': 'sum', 'area': 'BACKEND', 'complexity': 'SIMPLE', 'criticality': 'NORMAL', 'objective': 'obj', 'acceptance_criteria': ['ok']}]})}}],
                    'usage': {'prompt_tokens': 10, 'completion_tokens': 20}
                }
            })
            router = RoleRouter(db, executor=mock_exec)
            worker = Worker(db, router=router, worktrees=Path(td)/'wt')
            
            vault = Path(td) / 'vault'
            (vault / '08-QA-e-Auditoria').mkdir(parents=True)
            repo = Path(td) / 'repo'
            repo.mkdir()
            
            wid = db.create('proj', str(repo), 'test roadmap', vault_path=str(vault), routing_config=cfg)
            worker.step(wid)
            
            with db.con() as c:
                run = c.execute("SELECT * FROM runs WHERE workflow_id=? AND result='OK'", (wid,)).fetchone()
                self.assertEqual(run['candidate_id'], "eng-2")
                self.assertEqual(run['fallback_position'], 2)

    def test_14_code_fail_does_not_cause_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            db = DB(Path(td) / "test.sqlite3")
            cfg = create_mock_config()
            cfg["roles"]["QA:SIMPLE"] = {
                "strategy": "sequential",
                "candidates": [
                    {"id": "qa-1", "connection": "conn-a", "model": "model-a1", "enabled": True, "technical_retries": 1},
                    {"id": "qa-2", "connection": "conn-b", "model": "model-b1", "enabled": True, "technical_retries": 1}
                ]
            }
            
            # QA model returns a valid JSON envelope reporting FAIL
            mock_exec = MockExecutor({
                ("conn-a", "model-a1"): {
                    'choices': [{'message': {'content': json.dumps({
                        'result': 'FAIL',
                        'issues': ['Unit test assertions failed'],
                        'evidence': ['checked logs'],
                        'tests_run': ['test_calc']
                    })}}],
                    'usage': {'prompt_tokens': 50, 'completion_tokens': 30}
                }
            })
            router = RoleRouter(db, executor=mock_exec)
            worker = Worker(db, router=router, worktrees=Path(td)/'wt')
            
            vault = Path(td) / 'vault'
            (vault / '08-QA-e-Auditoria').mkdir(parents=True)
            repo = Path(td) / 'repo'
            repo.mkdir()
            
            wid = db.create('proj', str(repo), 'test roadmap', vault_path=str(vault), routing_config=cfg)
            worker.prepare(db.get(wid))
            tid = db.addtask(wid, {'title': 'task1', 'area': 'BACKEND', 'complexity': 'SIMPLE', 'criticality': 'NORMAL', 'objective': 'obj', 'acceptance_criteria': []})
            
            # Call QA directly
            worker.qa(db.get(wid), db.task(tid), role='QA')
            
            # Check that QA used candidate 1 and created a bug report (DEV correction loop), NEVER fallback to candidate 2
            with db.con() as c:
                runs = c.execute("SELECT * FROM runs WHERE workflow_id=?", (wid,)).fetchall()
                self.assertEqual(len(runs), 1)
                self.assertEqual(runs[0]['candidate_id'], "qa-1")
                self.assertEqual(runs[0]['fallback_position'], 1)
                
                bugs = c.execute("SELECT * FROM bug_reports WHERE workflow_id=?", (wid,)).fetchall()
                self.assertEqual(len(bugs), 1)
                self.assertEqual(bugs[0]['source'], "QA")
                
                # Task moved back to DEV
                t = db.task(tid)
                self.assertEqual(t['status'], 'DEV')
                self.assertEqual(t['corrections'], 1)

    def test_15_workflow_routing_snapshot_persisted(self):
        with tempfile.TemporaryDirectory() as td:
            db = DB(Path(td) / "test.sqlite3")
            cfg = create_mock_config()
            wid = db.create('proj', '/tmp/repo', 'roadmap', vault_path='/tmp/vault', routing_config=cfg)
            
            wf = db.get(wid)
            self.assertEqual(wf['routing_schema_version'], 1)
            self.assertEqual(wf['routing_config_sha256'], canonical_hash(cfg))
            saved_cfg = json.loads(wf['resolved_routes'])
            self.assertEqual(saved_cfg['connections']['conn-a']['base_url'], "http://127.0.0.1:9001/v1")

    def test_16_json_mutation_does_not_affect_active_workflow(self):
        with tempfile.TemporaryDirectory() as td:
            db = DB(Path(td) / "test.sqlite3")
            cfg1 = create_mock_config()
            wid1 = db.create('proj', '/tmp/repo', 'roadmap', vault_path='/tmp/vault', routing_config=cfg1)
            
            # Now simulate configuration change for cfg2
            cfg2 = create_mock_config()
            cfg2["roles"]["ENGINEER"]["candidates"] = [
                {"id": "eng-mutated", "connection": "conn-c", "model": "model-c-new", "enabled": True}
            ]
            
            # Workflow 1 frozen snapshot remains cfg1
            wf1 = db.get(wid1)
            snapshot1 = json.loads(wf1['resolved_routes'])
            self.assertEqual(snapshot1['roles']['ENGINEER']['candidates'][0]['id'], "eng-1")
            self.assertEqual(wf1['routing_config_sha256'], canonical_hash(cfg1))

    def test_17_new_workflow_picks_up_new_config(self):
        with tempfile.TemporaryDirectory() as td:
            db = DB(Path(td) / "test.sqlite3")
            cfg1 = create_mock_config()
            wid1 = db.create('proj', '/tmp/repo', 'roadmap', vault_path='/tmp/vault', routing_config=cfg1)
            
            cfg2 = create_mock_config()
            cfg2["roles"]["ENGINEER"]["candidates"] = [
                {"id": "eng-v2", "connection": "conn-b", "model": "model-b-v2", "enabled": True}
            ]
            wid2 = db.create('proj', '/tmp/repo', 'roadmap', vault_path='/tmp/vault', routing_config=cfg2)
            
            wf1 = db.get(wid1)
            wf2 = db.get(wid2)
            
            self.assertNotEqual(wf1['routing_config_sha256'], wf2['routing_config_sha256'])
            self.assertEqual(json.loads(wf2['resolved_routes'])['roles']['ENGINEER']['candidates'][0]['id'], "eng-v2")

    def test_18_sha256_persisted_and_verified(self):
        cfg = create_mock_config()
        expected_hash = canonical_hash(cfg)
        self.assertEqual(len(expected_hash), 64)
        
        with tempfile.TemporaryDirectory() as td:
            db = DB(Path(td) / "test.sqlite3")
            wid = db.create('proj', '/tmp/repo', 'roadmap', vault_path='/tmp/vault', routing_config=cfg)
            wf = db.get(wid)
            self.assertEqual(wf['routing_config_sha256'], expected_hash)

    def test_19_backward_compatibility_v11(self):
        legacy_v11 = {
            "providers": {
                "primary-gw": {"base_url": "http://127.0.0.1:8321/v1", "secret_key": "CLIPROXY_PRIMARY_KEY", "quota_group": "primary"},
                "backup-gw": {"base_url": "http://127.0.0.1:8311/v1", "secret_key": "CLIPROXY_BACKUP_KEY", "quota_group": "backup"}
            },
            "routes": {
                "ENGINEER": [["primary-gw", "gpt-5.6-terra-medium"]],
                "DEV:FRONTEND:SIMPLE": [["backup-gw", "gemini-3.7-flash-high"], ["primary-gw", "gpt-5.6-luna"]]
            }
        }
        converted = convert_legacy_roles_to_v12(legacy_v11)
        self.assertTrue(validate_routing_config(converted))
        self.assertIn("primary-gw", converted["connections"])
        self.assertIn("backup-gw", converted["connections"])
        self.assertEqual(len(converted["roles"]["DEV:FRONTEND:SIMPLE"]["candidates"]), 2)

    def test_20_migration_tool(self):
        with tempfile.TemporaryDirectory() as td:
            legacy_file = Path(td) / "roles.json"
            out_file = Path(td) / "agent-routing.json"
            
            legacy_content = {
                "providers": {
                    "primary-gw": {"base_url": "http://127.0.0.1:8321/v1", "secret_key": "CLIPROXY_PRIMARY_KEY", "quota_group": "primary"}
                },
                "routes": {
                    "ENGINEER": [["primary-gw", "gpt-5.6-terra-medium"]]
                }
            }
            legacy_file.write_text(json.dumps(legacy_content), encoding='utf-8')
            
            res = migrate_roles_to_agent_routing(legacy_file, out_file)
            self.assertEqual(res["status"], "MIGRATED")
            self.assertTrue(res["model_mapping_unchanged"])
            self.assertTrue(out_file.exists())

    def test_21_model_mapping_unchanged(self):
        # Compare canonical agent-routing.json with legacy roles.json
        v11_file = Path(__file__).resolve().parents[1] / "config/roles.json"
        v12_file = Path(__file__).resolve().parents[1] / "config/agent-routing.json"
        
        legacy_data = json.loads(v11_file.read_text(encoding='utf-8'))
        v12_data = json.loads(v12_file.read_text(encoding='utf-8'))
        
        for route_key, expected_pairs in legacy_data.get('routes', {}).items():
            self.assertIn(route_key, v12_data['roles'], f"Missing role {route_key} in v1.2")
            candidates = v12_data['roles'][route_key]['candidates']
            actual_pairs = [[c['connection'], c['model']] for c in candidates]
            self.assertEqual(actual_pairs, expected_pairs, f"Route mismatch for {route_key}")

    def test_22_doctor_routing_validation(self):
        doc = check_routing_config()
        self.assertEqual(doc["status"], "PASS")
        self.assertGreaterEqual(doc["connections_count"], 1)
        self.assertGreaterEqual(doc["roles_count"], 1)
        
        # Test full doctor
        full_doc = run_doctor()
        self.assertIn(full_doc["status"], ("READY", "DEGRADED", "PASS"))
        self.assertEqual(full_doc["routing_config"]["status"], "PASS")

    def test_23_integration_simulated_connections_a_b_c(self):
        """Simulate A -> 503, B -> INVALID_JSON (retry exhausted), C -> PASS"""
        with tempfile.TemporaryDirectory() as td:
            db = DB(Path(td) / "test.sqlite3")
            cfg = create_mock_config()
            
            mock_exec = MockExecutor({
                ("conn-a", "model-a1"): AvailabilityError("HTTP_503", "Conn A Unavailable"),
                ("conn-b", "model-b1"): InvalidEnvelopeError("INVALID_JSON", "Conn B Invalid Envelope"),
                ("conn-c", "model-c1"): {
                    'choices': [{'message': {'content': json.dumps({'tasks': [{'title': 'sum', 'area': 'BACKEND', 'complexity': 'SIMPLE', 'criticality': 'NORMAL', 'objective': 'obj', 'acceptance_criteria': ['ok']}]})}}],
                    'usage': {'prompt_tokens': 30, 'completion_tokens': 40}
                }
            })
            router = RoleRouter(db, executor=mock_exec)
            worker = Worker(db, router=router, worktrees=Path(td)/'wt')
            
            vault = Path(td) / 'vault'
            (vault / '08-QA-e-Auditoria').mkdir(parents=True)
            repo = Path(td) / 'repo'
            repo.mkdir()
            
            wid = db.create('proj', str(repo), 'test roadmap', vault_path=str(vault), routing_config=cfg)
            worker.step(wid)
            
            # Verify sequence A -> B -> C was executed
            conns_called = [c[0] for c in mock_exec.calls]
            self.assertEqual(conns_called, ["conn-a", "conn-a", "conn-b", "conn-b", "conn-c"])
            
            with db.con() as c:
                run = c.execute("SELECT * FROM runs WHERE workflow_id=? AND result='OK'", (wid,)).fetchone()
                self.assertEqual(run['candidate_id'], "eng-3")
                self.assertEqual(run['connection'], "conn-c")
                self.assertEqual(run['fallback_position'], 3)

    def test_24_workflow_immutability_execution(self):
        """Workflow 1 starts with A/B. Config changes to C/D. W1 executes with A/B, W2 executes with C/D."""
        with tempfile.TemporaryDirectory() as td:
            db = DB(Path(td) / "test.sqlite3")
            
            cfg1 = create_mock_config({
                "roles": {
                    "ENGINEER": {
                        "strategy": "sequential",
                        "candidates": [
                            {"id": "eng-a", "connection": "conn-a", "model": "model-a1", "enabled": True, "technical_retries": 0}
                        ]
                    }
                }
            })
            
            cfg2 = create_mock_config({
                "roles": {
                    "ENGINEER": {
                        "strategy": "sequential",
                        "candidates": [
                            {"id": "eng-b", "connection": "conn-b", "model": "model-b1", "enabled": True, "technical_retries": 0}
                        ]
                    }
                }
            })
            
            mock_exec = MockExecutor({
                ("conn-a", "model-a1"): {
                    'choices': [{'message': {'content': json.dumps({'tasks': [{'title': 'task-a', 'area': 'BACKEND', 'complexity': 'SIMPLE', 'criticality': 'NORMAL', 'objective': 'obj', 'acceptance_criteria': ['ok']}]})}}],
                    'usage': {'prompt_tokens': 10, 'completion_tokens': 10}
                },
                ("conn-b", "model-b1"): {
                    'choices': [{'message': {'content': json.dumps({'tasks': [{'title': 'task-b', 'area': 'BACKEND', 'complexity': 'SIMPLE', 'criticality': 'NORMAL', 'objective': 'obj', 'acceptance_criteria': ['ok']}]})}}],
                    'usage': {'prompt_tokens': 10, 'completion_tokens': 10}
                }
            })
            
            router = RoleRouter(db, executor=mock_exec)
            worker = Worker(db, router=router, worktrees=Path(td)/'wt')
            
            vault = Path(td) / 'vault'
            (vault / '08-QA-e-Auditoria').mkdir(parents=True)
            repo = Path(td) / 'repo'
            repo.mkdir()
            
            wid1 = db.create('proj1', str(repo), 'roadmap1', vault_path=str(vault), routing_config=cfg1)
            wid2 = db.create('proj2', str(repo), 'roadmap2', vault_path=str(vault), routing_config=cfg2)
            
            # Execute step on W1
            worker.step(wid1)
            # Execute step on W2
            worker.step(wid2)
            
            with db.con() as c:
                r1 = c.execute("SELECT * FROM runs WHERE workflow_id=?", (wid1,)).fetchone()
                r2 = c.execute("SELECT * FROM runs WHERE workflow_id=?", (wid2,)).fetchone()
                
                self.assertEqual(r1['candidate_id'], "eng-a")
                self.assertEqual(r1['connection'], "conn-a")
                
                self.assertEqual(r2['candidate_id'], "eng-b")
                self.assertEqual(r2['connection'], "conn-b")

if __name__ == '__main__':
    unittest.main()
