import json
import time
from .config import CONFIG, secret_values, load_routing_config
from .executor import ProviderExecutor, TechnicalError, AvailabilityError, RateLimitError, AuthError, NetworkTimeoutError, InvalidEnvelopeError

DEFAULT_FALLBACK_ON = [
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
]

class RoleRouter:
    """Declarative Router that resolves roles into candidate sequences based on configuration snapshots."""
    def __init__(self, db, executor=None):
        self.db = db
        self.executor = executor or ProviderExecutor(secrets_resolver=secret_values)

    def resolve_role_def(self, role, area=None, complexity=None, criticality=None, routing_config=None):
        """Resolve role configuration definition and candidate list."""
        cfg = routing_config or CONFIG
        roles_map = cfg.get('roles', {})

        # Route key candidates in order of specificity
        keys_to_try = []
        if role in ('DEV',):
            if area and complexity:
                keys_to_try.append(f"DEV:{area}:{complexity}")
            if area:
                keys_to_try.append(f"DEV:{area}")
            keys_to_try.append("DEV")
        elif role in ('QA', 'BLACK_BOX_QA'):
            if complexity:
                keys_to_try.append(f"QA:{complexity}")
                keys_to_try.append(f"{role}:{complexity}")
            keys_to_try.append(role)
            keys_to_try.append("QA")
        elif role in ('IQA', 'INDEPENDENT_QA', 'WHITE_BOX_QA'):
            if criticality:
                keys_to_try.append(f"IQA:{criticality}")
                keys_to_try.append(f"INDEPENDENT_QA:{criticality}")
                keys_to_try.append(f"{role}:{criticality}")
            keys_to_try.append(role)
            keys_to_try.append("INDEPENDENT_QA")
            keys_to_try.append("IQA")
        elif role in ('ENGINEER', 'FINAL_ENGINEER'):
            keys_to_try.append(role)
            keys_to_try.append("ENGINEER")
        else:
            keys_to_try.append(role)

        for k in keys_to_try:
            if k in roles_map:
                return k, roles_map[k]

        raise KeyError(f"No routing definition found for role '{role}' (tried {keys_to_try})")

    def primary_codex_allowed(self):
        """Keep primary Codex as a quota-only fallback when codex2 and codex3 are exhausted."""
        now = time.time()
        with self.db.con() as c:
            exhausted = {row['quota_group'] for row in c.execute(
                'SELECT quota_group FROM provider_health WHERE status="QUOTA_EXHAUSTED" AND until>?',
                (now,)
            )}
        return {'codex2', 'codex3'} <= exhausted

    def candidates(self, role, area=None, complexity=None, criticality=None, routing_config=None):
        """Return ordered list of available candidates for the given role, checking circuit breaker."""
        cfg = routing_config or CONFIG
        connections = cfg.get('connections', {})
        role_key, role_def = self.resolve_role_def(role, area, complexity, criticality, cfg)
        
        now = time.time()
        out = []
        raw_candidates = role_def.get('candidates', [])
        
        for pos, cand in enumerate(raw_candidates, 1):
            if not cand.get('enabled', True):
                continue
            
            conn_name = cand.get('connection')
            model = cand.get('model')
            conn_cfg = connections.get(conn_name)
            if not conn_cfg or not conn_cfg.get('enabled', True):
                continue

            quota_group = conn_cfg.get('quota_group', conn_name)

            # Circuit breaker check in SQLite provider_health
            with self.db.con() as c:
                h = c.execute(
                    'SELECT status, until FROM provider_health WHERE provider=? AND model=?',
                    (conn_name, model)
                ).fetchone()
                group = c.execute(
                    'SELECT MAX(until) u FROM provider_health WHERE quota_group=? AND status="QUOTA_EXHAUSTED"',
                    (quota_group,)
                ).fetchone()

            if (h and h['until'] > now) or (group and group['u'] and group['u'] > now):
                continue

            out.append((pos, cand, conn_name, model, conn_cfg))
            
        return out

    def unavailable(self, connection_name, model, conn_cfg, reason, quota=False):
        """Record connection / model unavailability or quota exhaustion cooldown in SQLite."""
        quota_group = conn_cfg.get('quota_group', connection_name)
        until = time.time() + (900 if quota else 120)
        with self.db.con() as c:
            c.execute(
                'INSERT OR REPLACE INTO provider_health VALUES(?,?,?,?,?,?,?)',
                (connection_name, model, quota_group, 'QUOTA_EXHAUSTED' if quota else 'UNAVAILABLE', until, str(reason)[:200], time.time())
            )

    def execute_chat(self, candidate, connection_name, model, conn_cfg, messages):
        """Execute chat for a candidate with technical retry support."""
        technical_retries = candidate.get('technical_retries', 1)
        temperature = candidate.get('temperature', 0.1)
        max_tokens = candidate.get('max_tokens', 12000)
        timeout = conn_cfg.get('timeout', 180)

        last_error = None
        for attempt in range(1 + technical_retries):
            try:
                response = self.executor.execute(
                    connection_name=connection_name,
                    connection_cfg=conn_cfg,
                    model=model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout=timeout
                )
                return response, attempt
            except TechnicalError as tech_err:
                last_error = tech_err
                if attempt < technical_retries:
                    # Retry within candidate
                    time.sleep(0.1)
                    continue
                else:
                    # Exhausted candidate retries, re-raise for fallback
                    raise last_error

# Backward-compatibility alias
Router = RoleRouter
