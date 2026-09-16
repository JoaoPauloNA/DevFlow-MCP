import json
import shutil
from pathlib import Path

def migrate_v1_to_v2(cfg):
    """Convert v1 routing config to v2 format."""
    if cfg.get('schema_version', 1) >= 2:
        return cfg
        
    new_cfg = {
        "schema_version": 2,
        "default_fallback_on": cfg.get('default_fallback_on', []),
        "connections": {},
        "roles": {}
    }
    
    # Migrate Connections: map legacy 'openai-compatible' to transport 'proxy'
    for cid, conn in cfg.get('connections', {}).items():
        new_cfg['connections'][cid] = {
            "transport": "proxy",
            "provider": "cliproxy",
            "protocol": "openai-chat",
            "type": conn.get('type'), 
            "base_url": conn.get('base_url'),
            "auth": conn.get('auth'),
            "quota_group": conn.get('quota_group', cid),
            "enabled": conn.get('enabled', True),
            "timeout": conn.get('timeout', 180)
        }
        
    # Migrate Roles
    for rid, rdef in cfg.get('roles', {}).items():
        new_cfg['roles'][rid] = {
            "strategy": rdef.get('strategy', 'sequential'),
            "fallback_on": rdef.get('fallback_on', []),
            "candidates": rdef.get('candidates', [])
        }
    return new_cfg

def migrate_and_save(input_path, output_path):
    input_path = Path(input_path)
    if not input_path.exists():
        raise FileNotFoundError(f"Config not found: {input_path}")
        
    # Backup
    backup_path = input_path.with_suffix('.json.bak')
    shutil.copyfile(input_path, backup_path)
    
    with open(input_path, 'r') as f:
        v1_data = json.load(f)
        
    v2_data = migrate_v1_to_v2(v1_data)
    
    with open(output_path, 'w') as f:
        json.dump(v2_data, f, indent=2)
    return backup_path
