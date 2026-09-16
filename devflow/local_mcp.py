#!/usr/bin/env python3
"""Local-only read-only Git and browser-report MCP servers."""
import argparse
import json
import re
import subprocess
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PROJECTS_ROOT = Path('/Users/joaopaulo/Documents/Projetos').resolve()
REPORT_ROOT = Path('/Users/joaopaulo/Meu Drive/Projetos (1)/DadosTeste/002-DevFlow-MCP/capturas-navegador')

GIT_TOOLS = [
    {'name': 'git_repository_summary', 'description': 'Read-only branch, status, and recent commits for an allowed local Git repository.', 'inputSchema': {'type': 'object', 'required': ['repo_path'], 'properties': {'repo_path': {'type': 'string'}}}},
    {'name': 'git_status', 'description': 'Read-only git status for an allowed local repository.', 'inputSchema': {'type': 'object', 'required': ['repo_path'], 'properties': {'repo_path': {'type': 'string'}}}},
    {'name': 'git_diff', 'description': 'Read-only unstaged and staged diff for an allowed local repository.', 'inputSchema': {'type': 'object', 'required': ['repo_path'], 'properties': {'repo_path': {'type': 'string'}}}},
    {'name': 'git_log', 'description': 'Read-only recent commit log for an allowed local repository.', 'inputSchema': {'type': 'object', 'required': ['repo_path'], 'properties': {'repo_path': {'type': 'string'}, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 30}}}},
]

BROWSER_TOOLS = [
    {'name': 'browser_capture_visible_screen', 'description': 'Capture the currently visible desktop for a report. Focus the intended browser page first. This does not click, navigate, read browser data, or send the image externally; it may include all visible screen content.', 'inputSchema': {'type': 'object', 'required': ['report_name'], 'properties': {'report_name': {'type': 'string'}}}},
]


def safe_name(value):
    value = re.sub(r'[^A-Za-z0-9._-]+', '-', value).strip('.-')
    if not value:
        raise ValueError('report_name must contain letters or numbers')
    return value[:80]


def safe_repo(value):
    repo = Path(value).expanduser().resolve()
    if PROJECTS_ROOT not in repo.parents and repo != PROJECTS_ROOT:
        raise ValueError('repo_path must stay under /Users/joaopaulo/Documents/Projetos')
    if not repo.is_dir():
        raise ValueError('repo_path is not a directory')
    result = subprocess.run(['git', '-C', str(repo), 'rev-parse', '--is-inside-work-tree'], capture_output=True, text=True, timeout=10)
    if result.returncode != 0 or result.stdout.strip() != 'true':
        detail = (result.stderr or result.stdout).strip().replace('\n', ' ')
        raise ValueError('repo_path is not a Git worktree: ' + detail[:240])
    return repo


def git_run(repo, *args):
    result = subprocess.run(['git', '-C', str(repo), *args], capture_output=True, text=True, timeout=20)
    output = (result.stdout + result.stderr).strip()
    if result.returncode:
        raise ValueError(output[:400])
    return output[:30000]


def git_call(name, args):
    repo = safe_repo(args['repo_path'])
    if name == 'git_status':
        return {'repo_path': str(repo), 'status': git_run(repo, 'status', '--short', '--branch')}
    if name == 'git_diff':
        return {'repo_path': str(repo), 'unstaged': git_run(repo, 'diff', '--'), 'staged': git_run(repo, 'diff', '--cached', '--')}
    if name == 'git_log':
        limit = int(args.get('limit', 10))
        if not 1 <= limit <= 30:
            raise ValueError('limit must be 1..30')
        return {'repo_path': str(repo), 'log': git_run(repo, 'log', f'-{limit}', '--date=short', '--pretty=format:%h %ad %s')}
    if name == 'git_repository_summary':
        try:
            commits = git_run(repo, 'log', '-5', '--date=short', '--pretty=format:%h %ad %s')
        except ValueError:
            commits = '(repository has no commits yet)'
        return {'repo_path': str(repo), 'branch': git_run(repo, 'branch', '--show-current'), 'status': git_run(repo, 'status', '--short', '--branch'), 'recent_commits': commits}
    raise ValueError('unknown Git tool')


def screenshot_path(name):
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    return REPORT_ROOT / f'{time.strftime("%Y%m%d-%H%M%S")}-{safe_name(name)}.png'


def browser_call(name, args):
    target = screenshot_path(args['report_name'])
    if name == 'browser_capture_visible_screen':
        result = subprocess.run(['/usr/sbin/screencapture', '-x', '-t', 'png', str(target)], capture_output=True, text=True, timeout=20)
        if result.returncode != 0 or not target.exists():
            raise ValueError('screen capture failed: ' + (result.stderr or result.stdout)[:300])
        return {'screenshot_path': str(target), 'mode': 'visible_screen', 'note': 'Visible desktop captured locally; review it before sharing.'}
    raise ValueError('unknown browser tool')


def make_handler(mode):
    tools = GIT_TOOLS if mode == 'git' else BROWSER_TOOLS
    def response(value):
        return {'content': [{'type': 'text', 'text': json.dumps(value, ensure_ascii=False)}]}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_POST(self):
            if self.path != '/mcp':
                self.send_error(404); return
            req_id = None
            try:
                request = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))))
                req_id = request.get('id')
                method = request.get('method')
                if method == 'initialize':
                    result = {'protocolVersion': '2025-03-26', 'capabilities': {'tools': {}}, 'serverInfo': {'name': f'local-{mode}', 'version': '0.1.0'}}
                elif method == 'tools/list':
                    result = {'tools': tools}
                elif method == 'tools/call':
                    params = request['params']; tool = params['name']; args = params.get('arguments', {})
                    result = response(git_call(tool, args) if mode == 'git' else browser_call(tool, args))
                else:
                    raise ValueError('method not found')
                body = {'jsonrpc': '2.0', 'id': req_id, 'result': result}
            except Exception as error:
                body = {'jsonrpc': '2.0', 'id': req_id, 'error': {'code': -32603, 'message': str(error)}}
            data = json.dumps(body).encode()
            self.send_response(200); self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)
    return Handler


def serve(mode, port):
    ThreadingHTTPServer(('127.0.0.1', port), make_handler(mode)).serve_forever()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('mode', choices=('git', 'browser')); parser.add_argument('--port', type=int, required=True)
    options = parser.parse_args(); serve(options.mode, options.port)
