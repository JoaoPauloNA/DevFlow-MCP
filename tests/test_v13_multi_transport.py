import unittest
import json
from pathlib import Path
from devflow.config import validate_routing_config
from devflow.executor import ProviderExecutor

class TestMultiTransportRouting(unittest.TestCase):
    def test_v2_schema_validation(self):
        cfg = {
            "schema_version": 2,
            "connections": {
                "proxy-conn": {
                    "transport": "proxy",
                    "provider": "cliproxy",
                    "protocol": "openai-chat",
                    "base_url": "http://localhost:8080",
                    "auth": {"type": "none"}
                },
                "cli-conn": {
                    "transport": "direct-cli",
                    "provider": "codex",
                    "protocol": "codex-cli",
                    "command": "/usr/bin/codex",
                    "auth": {"type": "cli-session"}
                }
            },
            "roles": {
                "ENGINEER": {
                    "candidates": [
                        {"id": "c1", "connection": "cli-conn", "model": "gpt-X"}
                    ]
                }
            }
        }
        self.assertTrue(validate_routing_config(cfg))

    def test_provider_executor_dispatch(self):
        # Mocking basic executor response
        executor = ProviderExecutor(secrets_resolver=lambda: {})
        # This will fail as URL/CMD is not real, but check dispatch logic
        conn_cfg = {"transport": "direct-api", "provider": "openai", "protocol": "openai-responses", "base_url": "http://invalid", "auth": {"type": "none"}}
        try:
            executor.execute("test", conn_cfg, "model", [{"role": "user", "content": "hi"}], timeout=1)
        except Exception as e:
            # We expect network/dispatch error, verify dispatch happened by error type
            self.assertFalse(isinstance(e, ValueError)) # Should be AvailabilityError or similar, not unknown transport
            
if __name__ == '__main__':
    unittest.main()
