# DevFlow Declarative Multi-Transport Routing

O DevFlow MCP agora utiliza um sistema de roteamento declarativo v2, desacoplando a lógica do agente do transporte físico (Proxy, CLI, API).

## Arquitetura
Role -> Candidate (Connection + Model) -> Transport (Proxy/CLI/API) -> Protocol Adapter.

## Configuração (config/agent-routing.json)
Schema v2 introduz campos obrigatórios:
- **transport**: proxy, direct-cli, direct-api.
- **provider**: provedor (ex: cliproxy, openai).
- **protocol**: protocolo (ex: openai-chat, codex-cli).

## Transportes
1. **Proxy**: Gateway HTTP (ex: CLIProxy).
2. **Direct CLI**: Execução local segura via subprocess.
3. **Direct API**: Chamadas diretas de API com normalização via Protocol Adapters.
