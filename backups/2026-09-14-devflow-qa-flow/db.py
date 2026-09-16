import json
import sqlite3
import time
import uuid
from pathlib import Path

SCHEMA = '''
CREATE TABLE IF NOT EXISTS workflows(id TEXT PRIMARY KEY, project_name TEXT, repo_path TEXT, workspace_path TEXT, roadmap TEXT, base_branch TEXT, status TEXT, paused INTEGER DEFAULT 0, created_at REAL, updated_at REAL, current_task TEXT, last_result TEXT, blocker TEXT);
CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, workflow_id TEXT, title TEXT, area TEXT, complexity TEXT, criticality TEXT, objective TEXT, context_refs TEXT, dependencies TEXT, acceptance_criteria TEXT, status TEXT, corrections INTEGER DEFAULT 0, qa_result TEXT, iqa_result TEXT);
CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, workflow_id TEXT, task_id TEXT, role TEXT, area TEXT, complexity TEXT, criticality TEXT, provider TEXT, model TEXT, quota_group TEXT, fallback_position INTEGER, start REAL, end REAL, duration REAL, result TEXT, retry_count INTEGER DEFAULT 0, input_tokens INTEGER, output_tokens INTEGER, cached_tokens INTEGER, detail TEXT);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, workflow_id TEXT, task_id TEXT, at REAL, level TEXT, message TEXT, data TEXT);
CREATE TABLE IF NOT EXISTS provider_health(provider TEXT, model TEXT, quota_group TEXT, status TEXT, until REAL, reason TEXT, updated_at REAL, PRIMARY KEY(provider,model));
'''

class DB:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.init()
    def con(self):
        c = sqlite3.connect(self.path); c.row_factory = sqlite3.Row; return c
    def init(self):
        with self.con() as c: c.executescript(SCHEMA)
    def create(self, project, repo, roadmap, branch=None):
        ident, now = 'wf-'+uuid.uuid4().hex[:12], time.time()
        with self.con() as c: c.execute('INSERT INTO workflows(id,project_name,repo_path,roadmap,base_branch,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)', (ident,project,repo,roadmap,branch,'READY',now,now))
        self.event(ident, None, 'INFO', '[WORKFLOW] created'); return ident
    def event(self,w,t,level,msg,data=None):
        with self.con() as c: c.execute('INSERT INTO events(workflow_id,task_id,at,level,message,data) VALUES(?,?,?,?,?,?)',(w,t,time.time(),level,msg,json.dumps(data or {})))
    def get(self,w):
        with self.con() as c: return c.execute('SELECT * FROM workflows WHERE id=?',(w,)).fetchone()
    def setwf(self,w,**kw):
        kw['updated_at']=time.time(); sets=','.join(k+'=?' for k in kw)
        with self.con() as c: c.execute(f'UPDATE workflows SET {sets} WHERE id=?',(*kw.values(),w))
    def addtask(self,w,x):
        ident=x.get('id') or 'task-'+uuid.uuid4().hex[:8]
        vals=(ident,w,x['title'],x.get('area','BACKEND'),x.get('complexity','SIMPLE'),x.get('criticality','NORMAL'),x.get('objective',''),json.dumps(x.get('context_refs',[])),json.dumps(x.get('dependencies',[])),json.dumps(x.get('acceptance_criteria',[])),'PENDING')
        with self.con() as c: c.execute('INSERT INTO tasks(id,workflow_id,title,area,complexity,criticality,objective,context_refs,dependencies,acceptance_criteria,status) VALUES(?,?,?,?,?,?,?,?,?,?,?)',vals)
        return ident
    def tasks(self,w):
        with self.con() as c: return c.execute('SELECT * FROM tasks WHERE workflow_id=? ORDER BY rowid',(w,)).fetchall()
    def task(self,t):
        with self.con() as c: return c.execute('SELECT * FROM tasks WHERE id=?',(t,)).fetchone()
    def settask(self,t,**kw):
        sets=','.join(k+'=?' for k in kw)
        with self.con() as c: c.execute(f'UPDATE tasks SET {sets} WHERE id=?',(*kw.values(),t))
    def run(self,**x):
        x['id']='run-'+uuid.uuid4().hex[:12]; cols=','.join(x); q=','.join('?' for _ in x)
        with self.con() as c: c.execute(f'INSERT INTO runs ({cols}) VALUES ({q})',tuple(x.values()))
        return x['id']
