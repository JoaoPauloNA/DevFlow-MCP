import json
import os
import subprocess
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

class HttpExecutor:
    def __init__(self, secrets_resolver):
        self.secrets_resolver = secrets_resolver

    def execute(self, connection_name, connection_cfg, model, messages, temperature, max_tokens, timeout):
        base_url = connection_cfg.get('base_url', '').rstrip('/')
        if not base_url:
            raise AvailabilityError('NO_BASE_URL', f"Connection '{connection_name}' has no base_url configured")

        effective_timeout = timeout or connection_cfg.get('timeout', 180)
        secrets = self.secrets_resolver()
        auth_header = resolve_auth_header(connection_cfg.get('auth'), connection_name, secrets)

        endpoint = f"{base_url}/chat/completions"
        payload = {'model': model, 'messages': messages, 'temperature': temperature, 'max_tokens': max_tokens}
        
        req = urllib.request.Request(endpoint, data=json.dumps(payload).encode('utf-8'), headers={'Content-Type': 'application/json'})
        if auth_header and auth_header != 'cli-session':
            req.add_header('Authorization', auth_header)
        
        try:
            with urllib.request.urlopen(req, timeout=effective_timeout) as resp:
                return json.loads(resp.read().decode('utf-8'))
        except urllib.error.HTTPError as http_err:
            status_code = http_err.code
            is_quota = status_code in (401, 403, 429)
            if status_code in (401, 403):
                raise AuthError(f'HTTP_{status_code}', "Auth error", quota=is_quota)
            elif status_code == 429:
                raise RateLimitError(f'HTTP_{status_code}', "Rate limit", quota=True)
            raise AvailabilityError(f'HTTP_{status_code}', "HTTP error")
        except Exception as e:
            raise AvailabilityError('CONNECTION_ERROR', str(e))

class CliExecutor:
    def execute(self, connection_name, connection_cfg, model, messages, timeout=None):
        cmd = connection_cfg.get('command')
        if not cmd or not os.path.isabs(cmd):
            raise AvailabilityError('INVALID_CMD', f"Connection {connection_name} needs absolute command path")
            
        prompt = messages[-1]['content']
        try:
            result = subprocess.run([cmd, '--model', model, prompt], capture_output=True, text=True, timeout=timeout or 300)
            if result.returncode != 0:
                raise AvailabilityError('CLI_FAILED', result.stderr or result.stdout)
            return json.loads(result.stdout)
        except subprocess.TimeoutExpired:
            raise NetworkTimeoutError('CLI_TIMEOUT', f"CLI execution timed out on {connection_name}")
        except Exception as e:
            raise AvailabilityError('CLI_EXEC_ERROR', str(e))

class ProviderExecutor:
    """Decoupled executor for AI model requests across Proxy, Direct API, and CLI transports."""
    def __init__(self, secrets_resolver=None):
        self.secrets_resolver = secrets_resolver or (lambda: {})
        self.http_exec = HttpExecutor(self.secrets_resolver)
        self.cli_exec = CliExecutor()

    def execute(self, connection_name, connection_cfg, model, messages, temperature=0.1, max_tokens=12000, timeout=None):
        from .adapters import DirectApiExecutor
        transport = connection_cfg.get('transport', 'proxy')
        if transport == 'direct-cli':
            return self.cli_exec.execute(connection_name, connection_cfg, model, messages, timeout)
        elif transport == 'direct-api':
            api_exec = DirectApiExecutor(self.secrets_resolver)
            return api_exec.execute(connection_name, connection_cfg, model, messages, temperature, max_tokens, timeout)
        else: # proxy
            return self.http_exec.execute(connection_name, connection_cfg, model, messages, temperature, max_tokens, timeout)

