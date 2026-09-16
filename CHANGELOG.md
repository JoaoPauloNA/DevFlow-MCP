# Changelog

Todas as alterações notáveis neste projeto serão documentadas neste arquivo.

O formato é baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.0.0/),
e este projeto adere ao [Versionamento Semântico](https://semver.org/lang/pt-BR/).

## [1.2.0] - 2026-09-16

### Adicionado
- **Declarative Agent Routing:** Configuração 100% declarativa em `config/agent-routing.json` e schema formal Draft-07 em `config/agent-routing.schema.json`.
- **Tríade Connection, Candidate e Role:** Separação conceitual entre endpoints de transporte, instâncias de modelos e papéis do DevFlow.
- **Suporte a $N$ Candidatos:** Lista ordenada de candidatos por papel sem limite artificial de fallbacks.
- **Retries Técnicos:** Suporte a retries locais por candidato (`technical_retries`) para falhas transitórias (como JSON malformado ou timeout de leitura).
- **Routing Snapshot Imutável:** Congelamento da configuração de roteamento (`routing_schema_version`, `routing_config_sha256`, `resolved_routes`) na criação de cada workflow, garantindo imutabilidade mesmo após edições de hot-config.
- **Auditoria Detalhada de Runs:** Registro em banco das colunas `candidate_id`, `connection`, `attempt`, `technical_retry` e `fallback_reason`.
- **Ferramenta de Migração:** Utilitário CLI `devflow migrate-routing-config` para converter configurações `roles.json` v1.1 em `agent-routing.json` v1.2 com validação de equivalência (`MODEL_MAPPING_UNCHANGED`).
- **DevFlow Doctor Expandido:** Nova verificação `ROUTING CONFIG` no comando `devflow doctor` e no tool `devflow_doctor`.
- **Suíte de Testes Automatizada:** Suíte de 24 novos testes unitários e de integração cobrindo cenários de fallback sequencial, snapshot imutável, rejeição de segredos literais e migração.

### Modificado
- `devflow/router.py`: Refatorado para `RoleRouter` declarativo que opera com base em snapshots de configuração.
- `devflow/worker.py`: Adaptado para invocar candidatos desacoplados e gravar auditoria granular de conexões e tentativas.
- `devflow/db.py`: Migração de schema SQLite aditiva para colunas de snapshot e auditoria de runs com fechamento explícito de conexões.

### Compatibilidade
- Suporte automático em tempo de execução para arquivos `config/roles.json` legados sem necessidade de conversão prévia manual.

---

## [1.1.0] - 2026-09-15

### Adicionado
- **Pipeline de QA em 5 Níveis:** Sequência estrita `QA` $\rightarrow$ `BLACK_BOX_QA` $\rightarrow$ `WHITE_BOX_QA` $\rightarrow$ `INDEPENDENT_QA` $\rightarrow$ `FINAL_ENGINEER`.
- **Evidências Reais de Navegador:** Integração com Brave Browser para captura de tela real e validação anti-alucinação.
- **Isolamento via Git Worktrees:** Criação de branches e diretórios de execução temporários para proteção do repositório principal.
- **Relatórios Imutáveis e SHA-256:** Geração de relatórios Markdown datados e registro de hashes na tabela `artifacts`.
- **Bug Loop Determinístico:** Retorno automático para `DEV` em caso de falha de QA, limitado a 3 ciclos antes de `NEEDS_USER`.
