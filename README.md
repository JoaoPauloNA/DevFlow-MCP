# DevFlow MCP (provisorio)

Local-only MCP and autonomous worker for approved roadmaps. Runtime state is `runtime/devflow.sqlite3`; no prompt or secret is persisted. Start the MCP at `127.0.0.1:8788/mcp` and worker separately with the two supplied LaunchAgents. Configuration is declarative in `config/roles.json`.

The worker owns the deterministic flow `ENGINEER -> DEV -> QA -> BLACK_BOX_QA -> WHITE_BOX_QA -> INDEPENDENT_QA -> FINAL_ENGINEER -> DONE`. Engineer runs only during preparation and final acceptance. A failure at any QA gate creates a structured Bug Report and returns directly to a fresh DEV execution; three correction cycles are permitted before `NEEDS_USER`.

Every workflow carries `project_name`, `repo_path`, and `vault_path`. `vault_path` is never guessed: a missing or ambiguous vault stops at `NEEDS_USER` with `Não sei qual é o vault correto deste projeto. Informe o caminho.` Use `devflow_set_vault` after the user provides the path; the mapping is persisted locally. Reports and filesystem evidence are immutable artifacts registered in SQLite by path and hash, while screenshot bytes stay in the project vault. Black-Box and White-Box require a real browser capture; unavailable capture becomes `BLOCKED`, never a decorative image or false PASS.
