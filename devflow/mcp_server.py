#!/usr/bin/env python3
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from .db import DB
from .doctor import run_doctor

DBPATH = os.getenv('DEVFLOW_DB', 'runtime/devflow.sqlite3')

TOOLS = [
    {
        'name': 'devflow_create_workflow',
        'description': 'Create a persistent workflow. vault_path is required to execute QA evidence; without it the workflow stops in NEEDS_USER.',
        'inputSchema': {
            'type': 'object',
            'required': ['project_name', 'repo_path', 'roadmap'],
            'properties': {
                'project_name': {'type': 'string'},
                'repo_path': {'type': 'string'},
                'vault_path': {'type': 'string'},
                'roadmap': {'type': 'string'},
                'base_branch': {'type': 'string'}
            }
        }
    },
    {
        'name': 'devflow_set_vault',
        'description': 'Set and persist the explicitly confirmed project vault path for a workflow.',
        'inputSchema': {
            'type': 'object',
            'required': ['workflow_id', 'vault_path'],
            'properties': {
                'workflow_id': {'type': 'string'},
                'vault_path': {'type': 'string'}
            }
        }
    },
    {
        'name': 'devflow_start',
        'description': 'Start autonomous worker processing.',
        'inputSchema': {
            'type': 'object',
            'required': ['workflow_id'],
            'properties': {'workflow_id': {'type': 'string'}}
        }
    },
    {
        'name': 'devflow_status',
        'description': 'Get short workflow status.',
        'inputSchema': {
            'type': 'object',
            'required': ['workflow_id'],
            'properties': {'workflow_id': {'type': 'string'}}
        }
    },
    {
        'name': 'devflow_pause',
        'description': 'Pause autonomous processing.',
        'inputSchema': {
            'type': 'object',
            'required': ['workflow_id'],
            'properties': {'workflow_id': {'type': 'string'}}
        }
    },
    {
        'name': 'devflow_resume',
        'description': 'Resume autonomous processing.',
        'inputSchema': {
            'type': 'object',
            'required': ['workflow_id'],
            'properties': {'workflow_id': {'type': 'string'}}
        }
    },
    {
        'name': 'devflow_cancel',
        'description': 'Cancel workflow without deleting evidence.',
        'inputSchema': {
            'type': 'object',
            'required': ['workflow_id'],
            'properties': {'workflow_id': {'type': 'string'}}
        }
    },
    {
        'name': 'devflow_tasks',
        'description': 'List task summaries.',
        'inputSchema': {
            'type': 'object',
            'required': ['workflow_id'],
            'properties': {'workflow_id': {'type': 'string'}}
        }
    },
    {
        'name': 'devflow_events',
        'description': 'List concise event history.',
        'inputSchema': {
            'type': 'object',
            'required': ['workflow_id'],
            'properties': {'workflow_id': {'type': 'string'}}
        }
    },
    {
        'name': 'devflow_doctor',
        'description': 'Run read-only system and declarative routing diagnostics before starting workflows.',
        'inputSchema': {
            'type': 'object',
            'properties': {
                'db_path': {'type': 'string'},
                'config_path': {'type': 'string'}
            }
        }
    }
]

def result(x):
    return {'content': [{'type': 'text', 'text': json.dumps(x, ensure_ascii=False)}]}

def call(name, a):
    d = DB(DBPATH)
    wid = a.get('workflow_id')
    
    if name == 'devflow_doctor':
        return run_doctor(db_path=a.get('db_path'), config_path=a.get('config_path'))
        
    if name == 'devflow_create_workflow':
        wid = d.create(a['project_name'], a['repo_path'], a['roadmap'], a.get('base_branch'), a.get('vault_path'))
        return {'workflow_id': wid, 'status': 'READY'}
        
    w = d.get(wid) if wid else None
    if wid and not w:
        raise ValueError('unknown workflow_id')
        
    if name == 'devflow_set_vault':
        d.set_vault(wid, a['vault_path'])
        return {'workflow_id': wid, 'vault_path': a['vault_path'], 'status': 'READY'}
    if name == 'devflow_start':
        d.setwf(wid, status='ENGINEERING', paused=0)
        return {'workflow_id': wid, 'status': 'ENGINEERING'}
    if name == 'devflow_pause':
        d.setwf(wid, paused=1)
        return {'workflow_id': wid, 'status': 'PAUSED'}
    if name == 'devflow_resume':
        task = d.task(w['current_task']) if w['current_task'] else None
        status = 'ENGINEERING' if w['status'] == 'READY' else ('DEV' if w['status'] == 'NEEDS_USER' and task and task['status'] == 'DEV' else w['status'])
        d.setwf(wid, paused=0, status=status, blocker=None)
        return {'workflow_id': wid, 'status': 'RESUMED'}
    if name == 'devflow_cancel':
        d.setwf(wid, status='CANCELLED', paused=1)
        return {'workflow_id': wid, 'status': 'CANCELLED'}
    if name == 'devflow_tasks':
        return {'workflow_id': wid, 'tasks': [dict(x) for x in d.tasks(wid)]}
    if name == 'devflow_events':
        with d.con() as c:
            return {'workflow_id': wid, 'events': [dict(x) for x in c.execute('SELECT at,level,message FROM events WHERE workflow_id=? ORDER BY id DESC LIMIT 30', (wid,))]}
    if name == 'devflow_status':
        t = d.task(w['current_task']) if w['current_task'] else None
        with d.con() as c:
            run = c.execute('SELECT provider,model,fallback_position,result,candidate_id,connection,attempt FROM runs WHERE workflow_id=? ORDER BY rowid DESC LIMIT 1', (wid,)).fetchone()
        return {
            'workflow_id': wid,
            'state': w['status'],
            'progress': f"{sum(x['status'] == 'PASSED' for x in d.tasks(wid))}/{len(d.tasks(wid))} tasks passed",
            'current_task': t['title'] if t else None,
            'role': w['status'],
            'model': dict(run) if run else None,
            'last_result': w['last_result'],
            'blockers': w['blocker'],
            'fallbacks_used': bool(run and run['fallback_position'] > 1)
        }
    raise ValueError(f'unknown tool: {name}')

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass
    def do_POST(self):
        if self.path != '/mcp':
            self.send_error(404)
            return
        try:
            req = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))))
            method = req.get('method')
            ident = req.get('id')
            if method == 'initialize':
                out = {'protocolVersion': '2025-03-26', 'capabilities': {'tools': {}}, 'serverInfo': {'name': 'devflow', 'version': '1.2.0'}}
            elif method == 'tools/list':
                out = {'tools': TOOLS}
            elif method == 'tools/call':
                out = result(call(req['params']['name'], req['params'].get('arguments', {})))
            else:
                out = {'error': {'code': -32601, 'message': 'method not found'}}
            body = json.dumps({'jsonrpc': '2.0', 'id': ident, 'result': out} if 'error' not in out else {'jsonrpc': '2.0', 'id': ident, **out}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            body = json.dumps({'jsonrpc': '2.0', 'id': None, 'error': {'code': -32603, 'message': str(e)}}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(body)

def serve():
    ThreadingHTTPServer(('127.0.0.1', int(os.getenv('DEVFLOW_PORT', '8788'))), Handler).serve_forever()

if __name__ == '__main__':
    serve()
