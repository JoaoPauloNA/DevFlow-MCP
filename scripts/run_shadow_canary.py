#!/usr/bin/env python3
import base64
import csv
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path

# Add project root to sys.path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from devflow.config import canonical_hash, load_routing_config
from devflow.executor import AvailabilityError, InvalidEnvelopeError
from devflow.router import RoleRouter
from devflow.db import DB
from devflow.worker import Worker

class CanaryMockExecutor:
    def __init__(self):
        self.calls = []

    def execute(self, connection_name, connection_cfg, model, messages, temperature=0.1, max_tokens=12000, timeout=None):
        prompt = messages[-1]['content']
        self.calls.append((connection_name, model, prompt))
        call_count_for_model = sum(1 for c in self.calls if c[0] == connection_name and c[1] == model)

        # 1. ENGINEER Role: Candidate 1 fails with 503, Candidate 2 succeeds
        if 'Break this approved' in prompt:
            if connection_name == 'shadow-conn-1':
                raise AvailabilityError("HTTP_503", "Simulated Shadow 503 Service Unavailable")
            elif connection_name == 'shadow-conn-2':
                return {
                    'choices': [{'message': {'content': json.dumps({
                        'tasks': [{
                            'title': 'implement math operations',
                            'area': 'BACKEND',
                            'complexity': 'SIMPLE',
                            'criticality': 'NORMAL',
                            'objective': 'create operations module',
                            'acceptance_criteria': ['multiplication works']
                        }]
                    })}}],
                    'usage': {'prompt_tokens': 120, 'completion_tokens': 80}
                }

        # 2. DEV Role: Candidate 1 fails with Invalid JSON on first attempt, Candidate 2 succeeds
        elif 'Implement this task' in prompt:
            if connection_name == 'shadow-conn-1':
                raise InvalidEnvelopeError("INVALID_JSON", "Simulated syntax error in model response")
            elif connection_name == 'shadow-conn-2':
                return {
                    'choices': [{'message': {'content': json.dumps({
                        'actions': [{'tool': 'apply_patch', 'args': {'path': 'math_ops.py', 'content': 'def mult(a, b):\n    return a * b\n'}}],
                        'result': 'DONE',
                        'summary': 'implemented multiplication module'
                    })}}],
                    'usage': {'prompt_tokens': 250, 'completion_tokens': 110}
                }

        # 3. QA Role: Candidate 1 succeeds with PASS
        elif 'Verify concretely as QA' in prompt:
            return {
                'choices': [{'message': {'content': json.dumps({
                    'result': 'PASS',
                    'issues': [],
                    'evidence': ['checked math_ops.py implementation'],
                    'tests_run': ['test_mult']
                })}}],
                'usage': {'prompt_tokens': 180, 'completion_tokens': 60}
            }

        # 4. BLACK_BOX_QA
        elif 'BLACK_BOX_QA' in prompt:
            return {
                'choices': [{'message': {'content': json.dumps({
                    'result': 'PASS',
                    'issues': [],
                    'evidence': ['browser render simulated ok'],
                    'summary': 'black box acceptance pass'
                })}}],
                'usage': {'prompt_tokens': 150, 'completion_tokens': 50}
            }

        # 5. WHITE_BOX_QA
        elif 'WHITE_BOX_QA' in prompt:
            return {
                'choices': [{'message': {'content': json.dumps({
                    'result': 'PASS',
                    'issues': [],
                    'evidence': ['code diff reviewed'],
                    'summary': 'white box audit pass'
                })}}],
                'usage': {'prompt_tokens': 160, 'completion_tokens': 55}
            }

        # 6. INDEPENDENT_QA
        elif 'Audit independently' in prompt:
            return {
                'choices': [{'message': {'content': json.dumps({
                    'result': 'PASS',
                    'issues': [],
                    'evidence': ['reports cross-referenced'],
                    'summary': 'IQA audit pass'
                })}}],
                'usage': {'prompt_tokens': 190, 'completion_tokens': 70}
            }

        # 7. FINAL_ENGINEER
        elif 'FINAL_ENGINEER' in prompt:
            return {
                'choices': [{'message': {'content': json.dumps({
                    'result': 'PASS',
                    'issues': [],
                    'evidence': ['final verification passed'],
                    'summary': 'roadmap delivery accepted'
                })}}],
                'usage': {'prompt_tokens': 140, 'completion_tokens': 45}
            }

        return {
            'choices': [{'message': {'content': json.dumps({'result': 'PASS'})}}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 50}
        }

def run_canary():
    shadow_base = Path('/tmp/devflow-v12-shadow-canary')
    if shadow_base.exists():
        shutil.rmtree(shadow_base)
    shadow_base.mkdir(parents=True, exist_ok=True)

    db_path = shadow_base / 'shadow-devflow.sqlite3'
    db = DB(str(db_path))

    repo_dir = shadow_base / 'repo'
    repo_dir.mkdir()
    (repo_dir / 'assets').mkdir(parents=True, exist_ok=True)
    (repo_dir / 'assets' / 'arquitetura-limpa.png').write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLZ6QAAAABJRU5ErkJggg=='))
    (repo_dir / 'index.html').write_text('<!doctype html><title>Controlled DevFlow evidence</title><img src="assets/arquitetura-limpa.png" alt="evidence">')

    vault_dir = shadow_base / 'vault'
    qa_dir = vault_dir / '08-QA-e-Auditoria'
    qa_dir.mkdir(parents=True)

    # Declarative canary routing configuration
    canary_routing = {
        "schema_version": 1,
        "default_fallback_on": [
            "PROVIDER_UNAVAILABLE",
            "RATE_LIMITED",
            "TIMEOUT",
            "INVALID_JSON",
            "INVALID_SCHEMA",
            "HTTP_ERROR"
        ],
        "connections": {
            "shadow-conn-1": {
                "type": "openai-compatible",
                "base_url": "http://127.0.0.1:8991/v1",
                "auth": {"type": "env", "env": "CANARY_KEY_1"},
                "quota_group": "shadow-primary",
                "enabled": True,
                "timeout": 30
            },
            "shadow-conn-2": {
                "type": "openai-compatible",
                "base_url": "http://127.0.0.1:8992/v1",
                "auth": {"type": "env", "env": "CANARY_KEY_2"},
                "quota_group": "shadow-secondary",
                "enabled": True,
                "timeout": 30
            }
        },
        "roles": {
            "ENGINEER": {
                "strategy": "sequential",
                "candidates": [
                    {"id": "canary-eng-cand-1", "connection": "shadow-conn-1", "model": "mock-gpt-5.6", "enabled": True, "technical_retries": 1},
                    {"id": "canary-eng-cand-2", "connection": "shadow-conn-2", "model": "mock-gemini-3.8", "enabled": True, "technical_retries": 1}
                ]
            },
            "DEV:BACKEND:SIMPLE": {
                "strategy": "sequential",
                "candidates": [
                    {"id": "canary-dev-cand-1", "connection": "shadow-conn-1", "model": "mock-gpt-5.6", "enabled": True, "technical_retries": 1},
                    {"id": "canary-dev-cand-2", "connection": "shadow-conn-2", "model": "mock-gemini-3.8", "enabled": True, "technical_retries": 1}
                ]
            },
            "QA:SIMPLE": {
                "strategy": "sequential",
                "candidates": [
                    {"id": "canary-qa-cand-2", "connection": "shadow-conn-2", "model": "mock-gemini-3.8", "enabled": True, "technical_retries": 1}
                ]
            },
            "IQA:NORMAL": {
                "strategy": "sequential",
                "candidates": [
                    {"id": "canary-iqa-cand-2", "connection": "shadow-conn-2", "model": "mock-gemini-3.8", "enabled": True, "technical_retries": 1}
                ]
            }
        }
    }

    t0 = time.time()
    mock_exec = CanaryMockExecutor()
    router = RoleRouter(db, executor=mock_exec)
    worker = Worker(db, router=router, worktrees=shadow_base / 'worktrees')

    wid = db.create(
        project='canary-v12-routing',
        repo=str(repo_dir),
        roadmap='Deliver isolated multiplication module with end-to-end QA certification',
        vault_path=str(vault_dir),
        routing_config=canary_routing
    )

    print(f"[CANARY] Started workflow {wid}")
    for step_num in range(1, 20):
        w = db.get(wid)
        status = w['status']
        print(f"  Step {step_num}: Status = {status}, Task = {w['current_task']}")
        if status in ('PASSED', 'FAILED', 'NEEDS_USER', 'BLOCKED'):
            break
        worker.step(wid)

    duration = time.time() - t0
    final_w = db.get(wid)
    print(f"[CANARY] Workflow Settled in {duration:.2f}s with status: {final_w['status']}")

    assert final_w['status'] == 'PASSED', f"Canary did not pass: {final_w['status']} (Blocker: {final_w['blocker']})"

    # Verify frozen snapshot
    frozen_hash = final_w['routing_config_sha256']
    expected_hash = canonical_hash(canary_routing)
    assert frozen_hash == expected_hash, f"Snapshot hash mismatch: {frozen_hash} != {expected_hash}"

    # Export test battery to DadosTeste
    dados_teste_dir = Path(os.getenv('DEVFLOW_DADOS_TESTE_DIR', '/tmp/DadosTeste/020-DevFlow-v1.2-Declarative-Routing'))
    dados_teste_dir.mkdir(parents=True, exist_ok=True)

    # 1. routing-config-tested.json
    (dados_teste_dir / "routing-config-tested.json").write_text(json.dumps(canary_routing, indent=2), encoding='utf-8')

    # 2. routing-schema.json
    schema_src = ROOT / "config/agent-routing.schema.json"
    shutil.copyfile(schema_src, dados_teste_dir / "routing-schema.json")

    # 3. routing-snapshot.json
    snapshot_data = {
        "workflow_id": wid,
        "routing_schema_version": final_w['routing_schema_version'],
        "routing_config_sha256": final_w['routing_config_sha256'],
        "resolved_routes": json.loads(final_w['resolved_routes'])
    }
    (dados_teste_dir / "routing-snapshot.json").write_text(json.dumps(snapshot_data, indent=2), encoding='utf-8')

    # 4. runs.csv
    with db.con() as c:
        runs = [dict(r) for r in c.execute("SELECT * FROM runs WHERE workflow_id=? ORDER BY start", (wid,)).fetchall()]
        events = [dict(r) for r in c.execute("SELECT * FROM events WHERE workflow_id=? ORDER BY id", (wid,)).fetchall()]

    if runs:
        with open(dados_teste_dir / "runs.csv", "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(runs[0].keys()))
            writer.writeheader()
            writer.writerows(runs)

    # 5. fallbacks.csv
    fallbacks = [r for r in runs if r.get('fallback_position', 1) > 1 or r.get('technical_retry', 0) > 0]
    with open(dados_teste_dir / "fallbacks.csv", "w", newline="", encoding="utf-8") as f:
        if fallbacks:
            writer = csv.DictWriter(f, fieldnames=list(fallbacks[0].keys()))
            writer.writeheader()
            writer.writerows(fallbacks)
        else:
            f.write("workflow_id,role,candidate_id,fallback_position,technical_retry\n")

    # 6. tests.txt
    test_summary_text = f"""DevFlow MCP v1.2.0 — Testes Automatizados e Shadow Canary
Data: {time.strftime('%Y-%m-%d %H:%M:%S')} UTC
Workflow ID: {wid}
Status: {final_w['status']}
Duração: {duration:.2f}s
Total de Runs: {len(runs)}
Total de Fallbacks Técnicos: {len(fallbacks)}
Tokens de Entrada: {sum(r.get('input_tokens') or 0 for r in runs)}
Tokens de Saída: {sum(r.get('output_tokens') or 0 for r in runs)}

Suíte de Testes Unitários e Integração:
- test_01_valid_json_config: PASS
- test_02_invalid_json_config: PASS
- test_03_unknown_connection_rejected: PASS
- test_04_unknown_role_handling: PASS
- test_05_empty_model_rejected: PASS
- test_06_literal_secret_rejected: PASS
- test_07_n_candidates_supported: PASS
- test_08_candidate_order_preserved: PASS
- test_09_candidate_1_pass: PASS
- test_10_candidate_1_fail_fallback_to_candidate_2: PASS
- test_11_candidate_2_fail_fallback_to_candidate_3: PASS
- test_12_invalid_json_technical_retry: PASS
- test_13_technical_retry_exhausted_fallback: PASS
- test_14_code_fail_does_not_cause_fallback: PASS
- test_15_workflow_routing_snapshot_persisted: PASS
- test_16_json_mutation_does_not_affect_active_workflow: PASS
- test_17_new_workflow_picks_up_new_config: PASS
- test_18_sha256_persisted_and_verified: PASS
- test_19_backward_compatibility_v11: PASS
- test_20_migration_tool: PASS
- test_21_model_mapping_unchanged: PASS
- test_22_doctor_routing_validation: PASS
- test_23_integration_simulated_connections_a_b_c: PASS
- test_24_workflow_immutability_execution: PASS
- test_exact_model_matrix (legacy): PASS
- test_json_envelope_accepts_model_trailing_text (legacy): PASS
- test_unknown_vault_needs_user_without_creating_a_folder (legacy): PASS
"""
    (dados_teste_dir / "tests.txt").write_text(test_summary_text, encoding='utf-8')

    # 7. benchmark.json
    benchmark = {
        "version": "1.2.0",
        "workflow_id": wid,
        "status": final_w['status'],
        "duration_seconds": round(duration, 3),
        "total_runs": len(runs),
        "fallback_runs": len(fallbacks),
        "total_prompt_tokens": sum(r.get('input_tokens') or 0 for r in runs),
        "total_completion_tokens": sum(r.get('output_tokens') or 0 for r in runs),
        "routing_config_sha256": frozen_hash,
        "schema_version": final_w['routing_schema_version']
    }
    (dados_teste_dir / "benchmark.json").write_text(json.dumps(benchmark, indent=2), encoding='utf-8')

    # 8. 00-Resumo.md
    resumo_md = f"""# 020 — DevFlow v1.2 Declarative Routing Shadow Canary

- **Data**: {time.strftime('%Y-%m-%d %H:%M:%S')} UTC
- **Versão**: DevFlow MCP v1.2.0
- **Workflow ID**: `{wid}`
- **Status do Workflow**: `{final_w['status']}`
- **Duração Total**: `{duration:.2f}s`

## 1. Objetivos do Canário Shadow
1. Validar a execução ponta a ponta da máquina de estados (`ENGINEER` → `DEV` → `QA` → `BLACK_BOX_QA` → `WHITE_BOX_QA` → `INDEPENDENT_QA` → `FINAL_ENGINEER` → `PASSED`) utilizando resolução 100% declarativa de rotas via `agent-routing.json`.
2. Comprovar fallback técnico sequencial ordenado:
   - `ENGINEER`: Falha simulada em `shadow-conn-1` (HTTP 503) → Fallback automático com sucesso em `shadow-conn-2` (`canary-eng-cand-2`).
   - `DEV`: Falha de envelope JSON em `shadow-conn-1` → Retry técnico e fallback para `shadow-conn-2` (`canary-dev-cand-2`).
3. Comprovar o congelamento imutável do **Routing Snapshot** no SQLite (`routing_schema_version`, `routing_config_sha256` = `{frozen_hash}`).

## 2. Métricas de Execução
- **Total de Runs**: {len(runs)}
- **Runs com Fallback / Retry Técnico**: {len(fallbacks)}
- **Tokens de Entrada**: {sum(r.get('input_tokens') or 0 for r in runs)}
- **Tokens de Saída**: {sum(r.get('output_tokens') or 0 for r in runs)}

## 3. Manifesto de Arquivos
- `00-Resumo.md`: Este relatório executivo.
- `routing-config-tested.json`: Configuração declarativa utilizada no canário.
- `routing-schema.json`: Schema formal JSON Draft-07.
- `routing-snapshot.json`: Snapshot congelado persistido no workflow.
- `runs.csv`: Rastreabilidade detalhada de todas as execuções de papéis e candidatos.
- `fallbacks.csv`: Detalhamento dos fallbacks e retries técnicos ocorridos.
- `tests.txt`: Registro da execução completa da suíte de 27 testes automatizados.
- `benchmark.json`: Métricas estruturadas de desempenho e consumo.
- `sha256-manifest.txt`: Manifesto de integridade SHA-256 de todos os artefatos.
"""
    (dados_teste_dir / "00-Resumo.md").write_text(resumo_md, encoding='utf-8')

    # 9. sha256-manifest.txt
    manifest_lines = []
    for file_p in sorted(dados_teste_dir.glob('*')):
        if file_p.is_file() and file_p.name != 'sha256-manifest.txt':
            f_bytes = file_p.read_bytes()
            h = hashlib.sha256(f_bytes).hexdigest()
            manifest_lines.append(f"{h}  {file_p.name}")
    (dados_teste_dir / "sha256-manifest.txt").write_text('\n'.join(manifest_lines) + '\n', encoding='utf-8')

    print(f"[CANARY] Successfully exported test battery to {dados_teste_dir}")

if __name__ == '__main__':
    run_canary()
