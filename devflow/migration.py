import json
from pathlib import Path
from .config import convert_legacy_roles_to_v12, validate_routing_config, canonical_hash

def migrate_roles_to_agent_routing(roles_path, output_path=None):
    """Migrate legacy roles.json to agent-routing.json, validating schema and confirming model mapping."""
    roles_p = Path(roles_path)
    if not roles_p.exists():
        raise FileNotFoundError(f"Source roles file not found: {roles_p}")
    
    legacy_data = json.loads(roles_p.read_text(encoding='utf-8'))
    new_config = convert_legacy_roles_to_v12(legacy_data)
    
    # Validate converted config
    validate_routing_config(new_config)
    
    # Verify exact model mapping preservation
    legacy_routes = legacy_data.get('routes', {})
    for route_key, expected_pairs in legacy_routes.items():
        role_def = new_config['roles'].get(route_key)
        if not role_def:
            raise ValueError(f"Migration verification failed: missing role '{route_key}'")
        candidates = role_def['candidates']
        actual_pairs = [[c['connection'], c['model']] for c in candidates]
        if actual_pairs != expected_pairs:
            raise ValueError(f"Migration mismatch on {route_key}: expected {expected_pairs}, got {actual_pairs}")
    
    if output_path:
        out_p = Path(output_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(json.dumps(new_config, indent=2), encoding='utf-8')
        
    return {
        "status": "MIGRATED",
        "model_mapping_unchanged": True,
        "connections_count": len(new_config['connections']),
        "roles_count": len(new_config['roles']),
        "sha256": canonical_hash(new_config)
    }

if __name__ == '__main__':
    import sys
    src = sys.argv[1] if len(sys.argv) > 1 else 'config/roles.json'
    dst = sys.argv[2] if len(sys.argv) > 2 else 'config/agent-routing.json'
    res = migrate_roles_to_agent_routing(src, dst)
    print(json.dumps(res, indent=2))
