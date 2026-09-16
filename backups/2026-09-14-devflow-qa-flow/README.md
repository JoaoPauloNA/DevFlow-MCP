# DevFlow MCP (provisorio)

Local-only MCP and autonomous worker for approved roadmaps. Runtime state is `runtime/devflow.sqlite3`; no prompt or secret is persisted. Start the MCP at `127.0.0.1:8788/mcp` and worker separately with the two supplied LaunchAgents. Configuration is declarative in `config/roles.json`.

The worker owns ENGINEER -> DEV -> QA -> independent QA. It uses only one candidate per attempt, retries a transient provider failure once, then falls through the configured route. Codex 2 and Codex 3 precede the primary Codex route; primary Codex is the final configured fallback. Frontend routes retain Gemini first, including SVG/image work. QA failures return to the same DEV route and never trigger model fallback. Three DEV->QA correction cycles are permitted.
