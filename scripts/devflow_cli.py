#!/usr/bin/env python3
import json
import sys
from pathlib import Path

# Add project root to sys.path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from devflow.migration import migrate_roles_to_agent_routing
from devflow.doctor import run_doctor

def main():
    if len(sys.argv) < 2:
        print("Uso: devflow <command> [args...]")
        print("Comandos disponíveis:")
        print("  migrate-routing-config [src_roles.json] [dst_agent_routing.json]")
        print("  doctor [--config <path>] [--db <path>]")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == 'migrate-routing-config':
        src = sys.argv[2] if len(sys.argv) > 2 else str(ROOT / 'config/roles.json')
        dst = sys.argv[3] if len(sys.argv) > 3 else str(ROOT / 'config/agent-routing.json')
        print(f"Migrando {src} -> {dst}...")
        try:
            res = migrate_roles_to_agent_routing(src, dst)
            print(json.dumps(res, indent=2))
        except Exception as e:
            print(f"Erro na migração: {e}", file=sys.stderr)
            sys.exit(1)
    elif cmd == 'doctor':
        cfg_path = None
        db_path = None
        for i in range(2, len(sys.argv)):
            if sys.argv[i] == '--config' and i + 1 < len(sys.argv):
                cfg_path = sys.argv[i + 1]
            elif sys.argv[i] == '--db' and i + 1 < len(sys.argv):
                db_path = sys.argv[i + 1]
        doc = run_doctor(db_path=db_path, config_path=cfg_path)
        print(json.dumps(doc, indent=2, ensure_ascii=False))
        if doc['status'] == 'BLOCKED':
            sys.exit(1)
    else:
        print(f"Comando desconhecido: {cmd}", file=sys.stderr)
        sys.exit(1)

if __name__ == '__main__':
    main()
