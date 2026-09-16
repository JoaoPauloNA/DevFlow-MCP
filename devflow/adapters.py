import json
from .executor import ProviderExecutor

class ProtocolAdapter:
    """Base class for normalizing provider-specific responses."""
    def normalize(self, raw_response):
        raise NotImplementedError

class OpenAIResponsesAdapter(ProtocolAdapter):
    def normalize(self, raw):
        return {
            'text': raw.get('choices', [{}])[0].get('message', {}).get('content', ''),
            'input_tokens': raw.get('usage', {}).get('prompt_tokens', 0),
            'output_tokens': raw.get('usage', {}).get('completion_tokens', 0),
            'finish_reason': raw.get('choices', [{}])[0].get('finish_reason', 'unknown')
        }

class OpenAIChatAdapter(OpenAIResponsesAdapter): pass

class AnthropicMessagesAdapter(ProtocolAdapter):
    def normalize(self, raw):
        return {
            'text': raw.get('content', [{}])[0].get('text', ''),
            'input_tokens': raw.get('usage', {}).get('input_tokens', 0),
            'output_tokens': raw.get('usage', {}).get('output_tokens', 0),
            'finish_reason': raw.get('stop_reason', 'unknown')
        }

class DirectApiExecutor:
    """Executor for direct API calls using appropriate Protocol Adapters."""
    def __init__(self, secrets_resolver=None):
        self.executor = ProviderExecutor(secrets_resolver)
        self.adapters = {
            'openai-responses': OpenAIResponsesAdapter(),
            'openai-chat': OpenAIChatAdapter(),
            'anthropic-messages': AnthropicMessagesAdapter()
        }

class DirectApiExecutor:
    """Executor for direct API calls using appropriate Protocol Adapters."""
    def __init__(self, secrets_resolver=None):
        from .executor import ProviderExecutor # Avoid cyclic import
        self.executor = ProviderExecutor(secrets_resolver)
        self.adapters = {
            'openai-responses': OpenAIResponsesAdapter(),
            'openai-chat': OpenAIChatAdapter(),
            'anthropic-messages': AnthropicMessagesAdapter()
        }

class DirectApiExecutor:
    """Executor for direct API calls using appropriate Protocol Adapters."""
    def __init__(self, secrets_resolver=None):
        self.secrets_resolver = secrets_resolver
        self.adapters = {
            'openai-responses': OpenAIResponsesAdapter(),
            'openai-chat': OpenAIChatAdapter(),
            'anthropic-messages': AnthropicMessagesAdapter()
        }

    def execute(self, conn_name, conn_cfg, model, messages, temperature=0.1, max_tokens=12000, timeout=None):
        from .executor import HttpExecutor
        executor = HttpExecutor(self.secrets_resolver)
        raw = executor.execute(conn_name, conn_cfg, model, messages, temperature, max_tokens, timeout)
        
        protocol = conn_cfg.get('protocol')
        adapter = self.adapters.get(protocol)
        
        if adapter:
            return adapter.normalize(raw)
        return raw


 # Fallback to raw if no adapter found
