import json
import time
import urllib.request
import urllib.error
from .config import resolve_auth_header

class TechnicalError(Exception):
    """Base class for all technical failures eligible for retry/fallback."""
    def __init__(self, code, message, quota=False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.quota = quota

class AvailabilityError(TechnicalError): pass
class RateLimitError(TechnicalError): pass
class AuthError(TechnicalError): pass
class NetworkTimeoutError(TechnicalError): pass
class InvalidEnvelopeError(TechnicalError): pass

class ProviderExecutor:
    """Decoupled executor for AI model API requests."""
    def __init__(self, secrets_resolver=None):
        self.secrets_resolver = secrets_resolver or (lambda: {})

    def execute(self, connection_name, connection_cfg, model, messages, temperature=0.1, max_tokens=12000, timeout=None):
        """Execute a single model request over an OpenAI-compatible connection."""
        base_url = connection_cfg.get('base_url', '').rstrip('/')
        if not base_url:
            raise AvailabilityError('NO_BASE_URL', f"Connection '{connection_name}' has no base_url configured")

        effective_timeout = timeout or connection_cfg.get('timeout', 180)
        secrets = self.secrets_resolver()
        auth_header = resolve_auth_header(connection_cfg.get('auth'), connection_name, secrets)

        endpoint = f"{base_url}/chat/completions"
        payload = {
            'model': model,
            'messages': messages,
            'temperature': temperature,
            'max_tokens': max_tokens
        }
        body = json.dumps(payload).encode('utf-8')
        headers = {'Content-Type': 'application/json'}
        if auth_header:
            headers['Authorization'] = auth_header

        req = urllib.request.Request(endpoint, data=body, headers=headers)
        
        try:
            with urllib.request.urlopen(req, timeout=effective_timeout) as resp:
                resp_bytes = resp.read()
                try:
                    data = json.loads(resp_bytes.decode('utf-8'))
                    return data
                except Exception as json_err:
                    raise InvalidEnvelopeError('INVALID_JSON', f"Malformed JSON from {connection_name}/{model}: {str(json_err)[:100]}")
        except urllib.error.HTTPError as http_err:
            raw = http_err.read(400).decode('utf-8', errors='replace').lower()
            status_code = http_err.code
            is_quota = status_code in (401, 403, 429) or 'quota' in raw or 'rate' in raw
            if status_code in (401, 403):
                raise AuthError(f'HTTP_{status_code}', f"Auth error {status_code} on {connection_name}: {raw[:80]}", quota=is_quota)
            elif status_code == 429 or is_quota:
                raise RateLimitError(f'HTTP_{status_code}', f"Rate limit / Quota exceeded on {connection_name}: {raw[:80]}", quota=True)
            elif status_code >= 500:
                raise AvailabilityError(f'HTTP_{status_code}', f"Server error {status_code} on {connection_name}: {raw[:80]}", quota=False)
            else:
                raise AvailabilityError(f'HTTP_{status_code}', f"HTTP error {status_code} on {connection_name}: {raw[:80]}", quota=is_quota)
        except (urllib.error.URLError, TimeoutError) as net_err:
            err_str = str(net_err).lower()
            if 'timed out' in err_str or isinstance(net_err, TimeoutError):
                raise NetworkTimeoutError('TIMEOUT', f"Request timed out on {connection_name}/{model} after {effective_timeout}s")
            raise AvailabilityError('CONNECTION_REFUSED', f"Connection failed on {connection_name}: {type(net_err).__name__} {str(net_err)[:100]}")
        except Exception as generic_err:
            if isinstance(generic_err, TechnicalError):
                raise generic_err
            raise AvailabilityError('UNKNOWN_TECHNICAL_ERROR', f"Unexpected error on {connection_name}/{model}: {str(generic_err)[:100]}")
