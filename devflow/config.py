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

def validate_routing_config(cfg):
    """Validate structure, connections and roles of routing config v2."""
    if not isinstance(cfg, dict):
        raise RoutingConfigError("ROUTING_CONFIG_INVALID: config must be a JSON object")
    
    version = cfg.get('schema_version')
    if not isinstance(version, int) or version < 2:
        raise RoutingConfigError("ROUTING_CONFIG_INVALID: schema_version must be integer >= 2")
    
    connections = cfg.get('connections')
    if not isinstance(connections, dict) or not connections:
        raise RoutingConfigError("ROUTING_CONFIG_INVALID: 'connections' must be a non-empty object")
    
    for conn_id, conn in connections.items():
        if not isinstance(conn, dict):
            raise RoutingConfigError(f"ROUTING_CONFIG_INVALID: connection '{conn_id}' must be an object")
        if 'transport' not in conn:
            raise RoutingConfigError(f"ROUTING_CONFIG_INVALID: connection '{conn_id}' missing transport")
        if 'provider' not in conn:
            raise RoutingConfigError(f"ROUTING_CONFIG_INVALID: connection '{conn_id}' missing provider")
        if 'protocol' not in conn:
            raise RoutingConfigError(f"ROUTING_CONFIG_INVALID: connection '{conn_id}' missing protocol")
        
    roles = cfg.get('roles')
    if not isinstance(roles, dict) or not roles:
        raise RoutingConfigError("ROUTING_CONFIG_INVALID: 'roles' must be a non-empty object")
    
    for role_name, role_def in roles.items():
        if not isinstance(role_def, dict):
            raise RoutingConfigError(f"ROUTING_CONFIG_INVALID: role '{role_name}' must be an object")
        candidates = role_def.get('candidates')
        if not isinstance(candidates, list) or not candidates:
            raise RoutingConfigError(f"ROUTING_CONFIG_INVALID: role '{role_name}' must have non-empty 'candidates' list")
            
        for cand in candidates:
            if 'connection' not in cand or cand['connection'] not in connections:
                raise RoutingConfigError(f"ROUTING_CONFIG_INVALID: candidate references unknown connection '{cand.get('connection')}'")
    return True

def populate_legacy_keys_in_config(cfg):
    """Ensure legacy 'routes' and 'providers' views exist for full backward compatibility."""
    # V2 -> V1 legacy compatibility layer (if needed by old code)
    if 'routes' not in cfg and 'roles' in cfg:
        routes = {}
        for rname, rdef in cfg['roles'].items():
            routes[rname] = [[c['connection'], c['model']] for c in rdef.get('candidates', [])]
        cfg['routes'] = routes
    return cfg

def load_routing_config(config_path=None):
    """Load, validate and return routing config, supporting v2."""
    if config_path:
        p = Path(config_path)
    else:
        v12_path = ROOT / 'config/agent-routing.json'
        p = v12_path

    if not p.exists():
        raise FileNotFoundError(f"Routing configuration not found at {p}")

    text = p.read_text(encoding='utf-8')
    data = json.loads(text)
    
    validate_routing_config(data)
    populate_legacy_keys_in_config(data)
    return data

# Load default config for package import
try:
    CONFIG = load_routing_config()
except Exception as _err:
    CONFIG = {"schema_version": 2, "connections": {}, "roles": {}}

def secret_values():
    """Resolve secrets securely from environment or local secret files without exposing raw tokens."""
    bundled = ROOT / 'secrets.env'
    custom = os.getenv('DEVFLOW_SECRETS_FILE')
    path = Path(custom) if custom else bundled
    
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
    if auth_type == 'cli-session':
        return 'cli-session'

    env_var = auth_def.get('env', '')
    token = None
    if env_var:
        token = os.getenv(env_var) or secrets_map.get(env_var)
                
    if token:
        return f"Bearer {token}"
    return None
