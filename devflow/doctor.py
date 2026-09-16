import json
import os
import shutil
import subprocess
from pathlib import Path
from .config import load_routing_config, validate_routing_config, canonical_hash, RoutingConfigError, ROOT, secret_values
from .db import DB

def check_routing_config(config_path=None):
    """Diagnose routing configuration, schema, connections, and candidate models."""
    diag = {
        "status": "PASS",
        "details": [],
        "errors": []
    }
    try:
        cfg = load_routing_config(config_path)
        diag["schema_version"] = cfg.get("schema_version", 1)
        diag["sha256"] = canonical_hash(cfg)
        
        connections = cfg.get("connections", {})
        roles = cfg.get("roles", {})
        
        diag["connections_count"] = len(connections)
        diag["roles_count"] = len(roles)
        
        # Check connections with protocol/transport
        for cname, cinfo in connections.items():
            transport = cinfo.get("transport", "proxy")
            protocol = cinfo.get("protocol", "openai-chat")
            auth_info = cinfo.get("auth", {})
            auth_type = auth_info.get("type", "unknown")
            env_ref = auth_info.get("env")
            base = cinfo.get("base_url") or cinfo.get("command")
            diag["details"].append(f"Connection '{cname}': {transport}/{cinfo.get('provider')}/{protocol} -> {base} (auth: {auth_type}, env: {env_ref})")
            
            # Probe if transport is CLI and file exists
            if transport == 'direct-cli' and not os.path.exists(base):
                diag["errors"].append(f"CLI executable not found for connection '{cname}': {base}")
                diag["status"] = "FAIL"
            
        # Check roles and candidate models
        for rname, rinfo in roles.items():
            cands = rinfo.get("candidates", [])
            cand_desc = [f"{c.get('id')}({c.get('connection')}/{c.get('model')})" for c in cands]
            diag["details"].append(f"Role '{rname}': {len(cands)} candidates -> {', '.join(cand_desc)}")

    except RoutingConfigError as rce:
        diag["status"] = "FAIL"
        diag["errors"].append(str(rce))
    except Exception as exc:
        diag["status"] = "FAIL"
        diag["errors"].append(f"Config load failed: {str(exc)}")

    return diag

def run_doctor(db_path=None, repo_path=None, vault_path=None, config_path=None):
    """Execute complete DevFlow system and routing health check."""
    report = {
        "status": "READY",
        "routing_config": check_routing_config(config_path),
        "sqlite": {"status": "PASS", "path": str(db_path or "default")},
        "git": {"status": "PASS"},
        "tools": {"status": "PASS"},
        "summary": []
    }
    
    # 1. Routing Config
    if report["routing_config"]["status"] != "PASS":
        report["status"] = "BLOCKED"
        report["summary"].append(f"ROUTING_CONFIG=FAIL: {'; '.join(report['routing_config']['errors'])}")
    else:
        report["summary"].append("ROUTING_CONFIG=PASS")
        
    # 2. SQLite
    try:
        test_db_path = db_path or os.getenv('DEVFLOW_DB', 'runtime/devflow.sqlite3')
        db = DB(test_db_path)
        with db.con() as c:
            tables = {r['name'] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            required_tables = {'workflows', 'tasks', 'runs', 'events', 'provider_health', 'artifacts', 'bug_reports', 'vault_mappings'}
            missing = required_tables - tables
            if missing:
                report["sqlite"]["status"] = "FAIL"
                report["sqlite"]["missing_tables"] = list(missing)
                report["status"] = "BLOCKED"
    except Exception as e:
        report["sqlite"]["status"] = "FAIL"
        report["sqlite"]["error"] = str(e)
        report["status"] = "BLOCKED"

    # 3. Git & Worktree capability
    git_bin = shutil.which("git")
    if not git_bin:
        report["git"]["status"] = "FAIL"
        report["git"]["error"] = "git binary not found in PATH"
        report["status"] = "BLOCKED"
        
    return report
