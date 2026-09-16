#!/bin/zsh
# Run this manually in Terminal. Terminal's existing Documents and Screen
# Recording grants are required; no credentials are read or written here.
set -euo pipefail

APP="$HOME/Library/Application Support/DevFlow"

stop_existing() {
  local port="$1"
  local pid
  pid="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -n "$pid" ]]; then
    kill "$pid"
  fi
}

stop_existing 8792
stop_existing 8793

nohup /usr/bin/env python3 "$APP/scripts/local_mcp.py" git 8792 \
  >"$APP/runtime/git-mcp-terminal.log" \
  2>"$APP/runtime/git-mcp-terminal.err" < /dev/null &
echo $! >"$APP/runtime/git-mcp-terminal.pid"

nohup /usr/bin/env python3 "$APP/scripts/local_mcp.py" browser 8793 \
  >"$APP/runtime/browser-mcp-terminal.log" \
  2>"$APP/runtime/browser-mcp-terminal.err" < /dev/null &
echo $! >"$APP/runtime/browser-mcp-terminal.pid"

sleep 1
echo 'Git MCP:       http://127.0.0.1:8792/mcp'
echo 'Browser MCP:   http://127.0.0.1:8793/mcp'
echo 'LibreChat will reconnect automatically.'
