import hashlib
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class RoutingConfigError(ValueError):
    """Raised when routing configuration is invalid."""
    pass

def canonical_hash(config_dict):
    """Compute deterministic SHA-256 of configuration dictionary."""
    canonical_bytes = json.dumps(config_dict, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(canonical_bytes).hexdigest()

def validate_no_raw_secrets(obj, path=""):
    """Recursively ensure no literal api keys or raw secret fields exist."""
    sensitive_keys = {'api_key', 'secret_key', 'token', 'secret', 'password'}
    if isinstance(obj, dict):
        for k, v in obj.items():
            sub_path = f"{path}.{k}" if path else k
            if k in sensitive_keys and isinstance(v, str) and not v.startswith(('env:', 'file:')):
                # Only allow auth descriptors, not raw secret literals in config
                raise RoutingConfigError(f"ROUTING_CONFIG_INVALID: literal secret found at '{sub_path}'. Use auth.env reference.")
            if k == 'auth' and isinstance(v, dict):
                auth_type = v.get('type')
                if auth_type not in ('env', 'none', 'bearer'):
                    raise RoutingConfigError(f"ROUTING_CONFIG_INVALID: unknown auth type '{auth_type}' at '{sub_path}'")
                if auth_type in ('env', 'bearer') and not v.get('env'):
                    raise RoutingConfigError(f"ROUTING_CONFIG_INVALID: missing env reference in auth at '{sub_path}'")
            validate_no_raw_secrets(v, sub_path)
    elif isinstance(obj, list):
        for idx, item in enumerate(obj):
            validate_no_raw_secrets(item, f"{path}[{idx}]")

def validate_routing_config(cfg):
    """Validate structure, connections and roles of routing config."""
    if not isinstance(cfg, dict):
        raise RoutingConfigError("ROUTING_CONFIG_INVALID: config must be a JSON object")
    
    version = cfg.get('schema_version')
    if not isinstance(version, int) or version < 1:
        raise RoutingConfigError("ROUTING_CONFIG_INVALID: schema_version must be integer >= 1")
    
    connections = cfg.get('connections')
    if not isinstance(connections, dict) or not connections:
        raise RoutingConfigError("ROUTING_CONFIG_INVALID: 'connections' must be a non-empty object")
    
    for conn_id, conn in connections.items():
        if not isinstance(conn, dict):
            raise RoutingConfigError(f"ROUTING_CONFIG_INVALID: connection '{conn_id}' must be an object")
        if not conn.get('base_url'):
            raise RoutingConfigError(f"ROUTING_CONFIG_INVALID: connection '{conn_id}' missing base_url")
        if not isinstance(conn.get('auth'), dict):
            raise RoutingConfigError(f"ROUTING_CONFIG_INVALID: connection '{conn_id}' missing auth object")
        
    roles = cfg.get('roles')
    if not isinstance(roles, dict) or not roles:
        raise RoutingConfigError("ROUTING_CONFIG_INVALID: 'roles' must be a non-empty object")
    
    for role_name, role_def in roles.items():
        if not isinstance(role_def, dict):
            raise RoutingConfigError(f"ROUTING_CONFIG_INVALID: role '{role_name}' must be an object")
        candidates = role_def.get('candidates')
        if not isinstance(candidates, list) or not candidates:
            raise RoutingConfigError(f"ROUTING_CONFIG_INVALID: role '{role_name}' must have non-empty 'candidates' list")
        
        seen_candidate_ids = set()
        for idx, cand in enumerate(candidates):
            if not isinstance(cand, dict):
                raise RoutingConfigError(f"ROUTING_CONFIG_INVALID: candidate {idx} in role '{role_name}' must be an object")
            cid = cand.get('id')
            if not cid or not isinstance(cid, str):
                raise RoutingConfigError(f"ROUTING_CONFIG_INVALID: candidate {idx} in role '{role_name}' missing id")
            if cid in seen_candidate_ids:
                raise RoutingConfigError(f"ROUTING_CONFIG_INVALID: duplicate candidate id '{cid}' in role '{role_name}'")
            seen_candidate_ids.add(cid)
            
            conn_ref = cand.get('connection')
            if not conn_ref or conn_ref not in connections:
                raise RoutingConfigError(f"ROUTING_CONFIG_INVALID: candidate '{cid}' references unknown connection '{conn_ref}'")
            
            model = cand.get('model')
            if not model or not isinstance(model, str) or not model.strip():
                raise RoutingConfigError(f"ROUTING_CONFIG_INVALID: candidate '{cid}' has empty model")

    validate_no_raw_secrets(cfg)
    return True

def convert_legacy_roles_to_v12(legacy_cfg):
    """Convert legacy roles.json v1.1 structure to v1.2 agent-routing format in-memory."""
    providers = legacy_cfg.get('providers', {})
    routes = legacy_cfg.get('routes', {})
    
    connections = {}
    for p_name, p_info in providers.items():
        connections[p_name] = {
            "type": "openai-compatible",
            "base_url": p_info.get('base_url', ''),
            "auth": {
                "type": "env",
                "env": f"CLIPROXY_{p_name.upper()}_API_KEY"
            },
            "quota_group": p_info.get('quota_group', p_name),
            "enabled": True,
            "timeout": 180
        }
    
    roles = {}
    for r_name, r_candidates in routes.items():
        candidates = []
        for idx, item in enumerate(r_candidates):
            p, m = item[0], item[1]
            cand_id = f"{r_name.lower().replace(':', '-')}-{p}-{idx+1}"
            candidates.append({
                "id": cand_id,
                "connection": p,
                "model": m,
                "enabled": True,
                "technical_retries": 1
            })
        roles[r_name] = {
            "strategy": "sequential",
            "candidates": candidates
        }
    
    return {
        "schema_version": 1,
        "default_fallback_on": [
            "PROVIDER_UNAVAILABLE",
            "RATE_LIMITED",
            "TIMEOUT",
            "AUTH_UNAVAILABLE",
            "CLI_UNAVAILABLE",
            "MODEL_UNAVAILABLE",
            "INVALID_JSON",
            "INVALID_SCHEMA",
            "CONNECTION_REFUSED",
            "HTTP_ERROR"
        ],
        "connections": connections,
        "roles": roles
    }

def populate_legacy_keys_in_config(cfg):
    """Ensure legacy 'routes' and 'providers' views exist for full backward compatibility."""
    if 'routes' not in cfg and 'roles' in cfg:
        routes = {}
        for rname, rdef in cfg['roles'].items():
            routes[rname] = [[c['connection'], c['model']] for c in rdef.get('candidates', [])]
        cfg['routes'] = routes
    if 'providers' not in cfg and 'connections' in cfg:
        providers = {}
        for cname, cdef in cfg['connections'].items():
            providers[cname] = {
                'base_url': cdef.get('base_url', ''),
                'secret_key': cdef.get('auth', {}).get('env', f'cliproxy{cname.capitalize()}ApiKey'),
                'quota_group': cdef.get('quota_group', cname)
            }
        cfg['providers'] = providers
    return cfg

def load_routing_config(config_path=None):
    """Load, validate and return routing config, supporting v1.2 and backward-compatible v1.1."""
    if config_path:
        p = Path(config_path)
    else:
        v12_path = ROOT / 'config/agent-routing.json'
        v11_path = ROOT / 'config/roles.json'
        p = v12_path if v12_path.exists() else v11_path

    if not p.exists():
        raise FileNotFoundError(f"Routing configuration not found at {p}")

    text = p.read_text(encoding='utf-8')
    data = json.loads(text)

    if 'schema_version' in data and 'connections' in data:
        # v1.2 declarative format
        validate_routing_config(data)
        populate_legacy_keys_in_config(data)
        return data
    elif 'providers' in data and 'routes' in data:
        # v1.1 legacy format -> adapt in memory with deprecation notice
        sys.stderr.write(f"[WARN] Deprecation: loaded legacy roles.json from {p}. Migrate to agent-routing.json\n")
        converted = convert_legacy_roles_to_v12(data)
        validate_routing_config(converted)
        populate_legacy_keys_in_config(converted)
        return converted
    else:
        raise RoutingConfigError("ROUTING_CONFIG_INVALID: unrecognized routing config schema")

# Load default config for package import
try:
    CONFIG = load_routing_config()
except Exception as _err:
    # Safe fallback if loaded in partial tests
    CONFIG = {"schema_version": 1, "connections": {}, "roles": {}}

def secret_values():
    """Resolve secrets securely from environment or local secret files without exposing raw tokens."""
    bundled = ROOT / 'secrets.env'
    custom = os.getenv('DEVFLOW_SECRETS_FILE')
    path = Path(custom) if custom else (bundled if bundled.exists() else Path('/Users/joaopaulo/Documents/secret.key'))
    
    values = {}
    if path.exists():
        for line in path.read_text(encoding='utf-8', errors='ignore').splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                k, v = line.split('=', 1)
                values[k.strip()] = v.strip().strip('"').strip("'")
    
    # Environment variables take precedence
    for k, v in os.environ.items():
        if 'API_KEY' in k or 'SECRET' in k or 'TOKEN' in k or 'CLIPROXY' in k:
            values[k] = v
            
    return values

def resolve_auth_header(auth_def, connection_name, secrets_map):
    """Resolve Authorization header value based on declarative auth spec."""
    if not auth_def or auth_def.get('type') == 'none':
        return None
    
    auth_type = auth_def.get('type')
    env_var = auth_def.get('env', '')
    
    token = None
    if env_var:
        token = os.getenv(env_var) or secrets_map.get(env_var)
    
    # Fallbacks for env mapping if not directly populated
    if not token:
        legacy_keys = [
            f"CLIPROXY_{connection_name.upper()}_KEY",
            f"CLIPROXY_{connection_name.upper()}_API_KEY",
            "CLIPROXY_API_KEY"
        ]
        for lk in legacy_keys:
            if lk in secrets_map:
                token = secrets_map[lk]
                break
            if lk in os.environ:
                token = os.environ[lk]
                break
                
    if token:
        return f"Bearer {token}"
    return None
