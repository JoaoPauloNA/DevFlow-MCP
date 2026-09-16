import json, os, re, shutil, subprocess, time
from pathlib import Path
from .router import Router, AvailabilityError
from .tools import ToolRuntime
from .visual import inspect_page

def content(response):
    return response.get('choices',[{}])[0].get('message',{}).get('content','') or ''
def json_object(text):
    fenced=re.search(r'```(?:json)?\s*(.*?)```',text,re.S)
    candidate=(fenced.group(1) if fenced else text).strip()
    decoder=json.JSONDecoder()
    for marker in ('{','['):
        start=candidate.find(marker)
        if start >= 0:
            try: return decoder.raw_decode(candidate[start:])[0]
            except json.JSONDecodeError: continue
    raise ValueError('model response did not contain a JSON envelope')

class Worker:
    def __init__(self,db,router=None,worktrees=None):
        self.db=db; self.router=router or Router(db); self.worktrees=Path(worktrees or Path(db.path).parent/'worktrees')
    def prepare(self,w):
        repo=Path(w['repo_path']).resolve(); target=self.worktrees/w['id']
        if w['workspace_path']: return w['workspace_path']
        try:
            subprocess.run(['git','-C',str(repo),'rev-parse','--is-inside-work-tree'],check=True,capture_output=True,text=True)
            self.worktrees.mkdir(parents=True,exist_ok=True)
            base=w['base_branch'] or 'HEAD'
            subprocess.run(['git','-C',str(repo),'worktree','add','-b',w['id'],str(target),base],check=True,capture_output=True,text=True)
            path=str(target)
        except Exception as e:
            # Never touch a possibly dirty source checkout; a non-git test workspace is already isolated by caller.
            if (repo/'.git').exists(): raise RuntimeError('isolated worktree unavailable: '+str(e)[:180])
            path=str(repo)
        self.db.setwf(w['id'],workspace_path=path); return path
    def invoke(self,w,role,task=None,prompt=''):
        area=task['area'] if task else None; complexity=task['complexity'] if task else None; criticality=task['criticality'] if task else None
        for pos,p,m,cfg in self.router.candidates(role,area,complexity,criticality):
            if p=='codex' and hasattr(self.router,'primary_codex_allowed') and not self.router.primary_codex_allowed():
                self.db.event(w['id'],task['id'] if task else None,'INFO','[ROUTER] primary Codex reserved for Codex 2 and Codex 3 quota exhaustion')
                continue
            started=time.time(); self.db.event(w['id'],task['id'] if task else None,'INFO',f'[{role}] started — {p}/{m}')
            try:
                response,retry=self.router.chat(p,m,cfg,[{'role':'system','content':'You are a DevFlow role runner. Return only compact JSON. Never reveal reasoning.'},{'role':'user','content':prompt}])
                text=content(response); usage=response.get('usage',{})
                self.db.run(workflow_id=w['id'],task_id=task['id'] if task else None,role=role,area=area,complexity=complexity,criticality=criticality,provider=p,model=m,quota_group=cfg['quota_group'],fallback_position=pos,start=started,end=time.time(),duration=time.time()-started,result='OK',retry_count=retry,input_tokens=usage.get('prompt_tokens'),output_tokens=usage.get('completion_tokens'),cached_tokens=None,detail='compact result')
                return text
            except AvailabilityError as e:
                raw=str(e); quota='quota=True' in raw; self.router.unavailable(p,m,cfg,raw,quota)
                self.db.event(w['id'],task['id'] if task else None,'WARN',f'[{role}] fallback from {p}/{m}',{'position':pos,'quota':quota})
        raise RuntimeError('all configured candidates unavailable')
    def engineer(self,w):
        workspace=self.prepare(w)
        prompt=f'''Break this approved roadmap into independently executable tasks. Repo: {workspace}. Roadmap: {w['roadmap']}. Return JSON object {{"tasks":[{{"title":"...","area":"FRONTEND|BACKEND|DATABASE|CROSS_CUTTING","complexity":"SIMPLE|COMPLEX","criticality":"NORMAL|CRITICAL","objective":"...","context_refs":[],"dependencies":[],"acceptance_criteria":[]}}]}}. Critical means auth, authorization, security, irreversible data, migration, transactions, payments, concurrency, critical infrastructure, or broad change. Break CROSS_CUTTING when possible.''' 
        result=json_object(self.invoke(w,'ENGINEER',prompt=prompt))
        if 'uma única tarefa DEV' in w['roadmap']:
            tasks=result.get('tasks',[])
            if len(tasks)!=1 or tasks[0].get('area')!='FRONTEND' or tasks[0].get('complexity')!='COMPLEX':
                raise RuntimeError('ENGINEER did not produce the required single FRONTEND/COMPLEX task')
        for x in result.get('tasks',[]): self.db.addtask(w['id'],x)
        if not result.get('tasks'): raise RuntimeError('engineer produced no tasks')
        self.db.setwf(w['id'],status='DEV',last_result=f"engineer created {len(result['tasks'])} tasks")
        self.db.event(w['id'],None,'INFO',f"[ENGINEER] created {len(result['tasks'])} tasks")
    def dev(self,w,t):
        root=w['workspace_path']; rt=ToolRuntime(root)
        visual_first = ''
        if t['area']=='FRONTEND' and 'referencia-tela.png' in w['roadmap']:
            visual_first = 'THIS IS A VISUAL HTML TASK. Your FIRST and mandatory action must be apply_patch for path index.html with the complete page. Do not inspect with ls or run_command first. The page must reference assets/arquitetura-limpa.png and must never contain referencia-tela.png. '
        prompt=f'''Implement this task in workspace {root}. Task: {dict(t)}. {visual_first}Use ONLY this compact JSON protocol: {{"actions":[{{"tool":"read_file|list_files|search_text|apply_patch|run_command|run_tests|git_status|git_diff","args":{{...}}}}],"result":"DONE|NEEDS_INFO","summary":"..."}}. Paths must be relative to the workspace. To create a file use apply_patch {{"path":"relative/path","content":"full file content"}}. To correct an existing file with a small CSS/HTML addition use apply_patch {{"path":"index.html","append":"CSS or HTML to append"}}. Never use cat, echo, tee, heredocs, redirection, cd, &&, pipes, or shell substitutions. run_tests may omit args and will auto-detect tests. You receive action results in the next turn; do not claim DONE until the relevant tests succeeded.''' 
        result={}; feedback=[]
        for _ in range(4):
            suffix='' if not feedback else '\nTool results from your prior actions:\n'+json.dumps(feedback)[-16000:]+'\nContinue with actions or return DONE.'
            result=json_object(self.invoke(w,'DEV',t,prompt+suffix)); actions=result.get('actions',[]); feedback=[]
            for a in actions[:12]:
                feedback.append(self._execute_action(w,t,rt,a))
            if result.get('result')=='DONE': break
            if not actions: raise RuntimeError('DEV needs information without a safe next action')
        if result.get('result')!='DONE':
            # The bounded conversation ended, but the only source of truth now
            # is the material worktree. Let QA reject it with concrete evidence.
            result['summary']='DEV tool loop bounded; forwarded to QA for material verification'
            self.db.event(w['id'],t['id'],'WARN','[DEV] bounded tool loop; forwarding to QA')
        self.db.settask(t['id'],status='QA'); self.db.setwf(w['id'],status='QA',current_task=t['id'],last_result=result.get('summary','DEV completed'))
        self.db.event(w['id'],t['id'],'INFO',f"[DEV][{t['id']}] completed")
    def _execute_action(self,w,t,rt,a):
        tool=a.get('tool') or a.get('name')
        args=a.get('args') or a.get('parameters') or {}
        if isinstance(args, str):
            try: args=json.loads(args)
            except json.JSONDecodeError: args={}
        cmd=args.get('command','')
        try:
            if tool=='read_file': output=rt.read_file(args['path'])
            elif tool=='list_files': output=rt.list_files(args.get('path','.'))
            elif tool=='search_text': output=rt.search_text(args['query'],args.get('path',''))
            elif tool=='apply_patch': output=rt.append_file(args['path'],args['append']) if 'append' in args else rt.apply_patch(args['path'],args['content'])
            elif tool=='run_command': output=rt.command(cmd)
            elif tool=='run_tests':
                checks=rt.default_checks()
                output='\n'.join(f'{name}: {value}' for name,value in checks) if checks else 'no test command detected; use run_command with an allow-listed direct command'
            elif tool=='git_status': output=rt.command('git status --short')
            elif tool=='git_diff': output=rt.command('git diff --')
            else: raise ValueError('unknown tool')
            return {'tool':tool,'ok':True,'output':output[-8000:]}
        except Exception as e:
            self.db.event(w['id'],t['id'],'WARN',f'[DEV] tool rejected: {tool}',{'reason':str(e)[:180]})
            return {'tool':tool,'ok':False,'error':str(e)[:500]}
    def qa(self,w,t,independent=False):
        role='IQA' if independent else 'QA'; root=w['workspace_path']; rt=ToolRuntime(root)
        diff=rt.command('git diff --'); status=rt.command('git status --short'); checks=rt.default_checks()
        needs_visual = t['area']=='FRONTEND' or 'referencia-tela.png' in w['roadmap']
        visual = inspect_page(root) if needs_visual else {'ok':True,'issues':[],'screenshots':[]}
        failed=[cmd for cmd,out in checks if 'exit=0' not in out]
        prompt=f'''Verify concretely as {role}. Workspace {root}; objective {t['objective']}; acceptance {t['acceptance_criteria']}; git status {status}; diff {diff}; mechanically executed checks {checks}; visual renders {visual['screenshots']}; visual gate issues {visual['issues']}. Reject if the reference image is rendered, the clean asset is absent, desktop or mobile does not render, or visual fidelity is materially inadequate. Return ONLY JSON {{"result":"PASS|FAIL|IMPLEMENTATION_ERROR|ARCHITECTURE_ERROR|USER_DECISION_REQUIRED","issues":[],"evidence":[],"tests_run":[]}}. Do not say it merely looks correct.''' 
        result=json_object(self.invoke(w,role,t,prompt)); verdict=result.get('result','FAIL')
        if not visual['ok'] and verdict=='PASS':
            verdict='FAIL'; result.setdefault('issues',[]).extend(visual['issues'])
        if failed and verdict=='PASS':
            verdict='FAIL'; result.setdefault('issues',[]).append('mechanical check failed: '+', '.join(failed))
        if independent:
            if verdict=='PASS': self.db.settask(t['id'],status='PASSED',iqa_result='PASS'); self.db.event(w['id'],t['id'],'INFO',f'[IQA][{t["id"]}] PASS')
            elif verdict=='ARCHITECTURE_ERROR': self.db.settask(t['id'],status='ENGINEERING',iqa_result=verdict); self.db.setwf(w['id'],status='ENGINEERING',last_result=verdict)
            elif verdict=='USER_DECISION_REQUIRED': self.db.setwf(w['id'],status='NEEDS_USER',blocker='independent QA requested product decision')
            else: self.db.settask(t['id'],status='DEV',iqa_result=verdict); self.db.setwf(w['id'],status='DEV',last_result=verdict)
        elif verdict=='PASS': self.db.settask(t['id'],status='INDEPENDENT_QA',qa_result='PASS'); self.db.setwf(w['id'],status='INDEPENDENT_QA',last_result='QA PASS'); self.db.event(w['id'],t['id'],'INFO',f'[QA][{t["id"]}] PASS')
        else:
            n=t['corrections']+1
            if n>3: self.db.setwf(w['id'],status='NEEDS_USER',blocker='maximum DEV→QA correction cycles exceeded')
            else: self.db.settask(t['id'],status='DEV',corrections=n,qa_result='FAIL'); self.db.setwf(w['id'],status='DEV',last_result='QA FAIL'); self.db.event(w['id'],t['id'],'WARN',f'[QA][{t["id"]}] FAIL; correction {n}/3',result)
    def step(self,wid):
        w=self.db.get(wid)
        if not w or w['paused'] or w['status'] in ('PASSED','FAILED','CANCELLED','NEEDS_USER','BLOCKED'): return
        try:
            if w['status'] in ('READY','ENGINEERING'): self.db.setwf(wid,status='ENGINEERING'); return self.engineer(self.db.get(wid))
            ts=self.db.tasks(wid); active=next((x for x in ts if x['status'] in ('PENDING','DEV','QA','INDEPENDENT_QA')),None)
            if not active:
                if ts and all(x['status']=='PASSED' for x in ts): self.db.setwf(wid,status='PASSED',last_result='all tasks passed'); self.db.event(wid,None,'INFO','[WORKFLOW] PASSED')
                return
            self.db.setwf(wid,current_task=active['id'])
            if active['status'] in ('PENDING','DEV'): return self.dev(self.db.get(wid),active)
            if active['status']=='QA': return self.qa(self.db.get(wid),active)
            return self.qa(self.db.get(wid),active,True)
        except Exception as e:
            self.db.setwf(wid,status='NEEDS_USER',blocker=str(e)); self.db.event(wid,None,'ERROR','[WORKFLOW] needs user',{'reason':str(e)})
    def run_forever(self):
        while True:
            with self.db.con() as c: ids=[r['id'] for r in c.execute("SELECT id FROM workflows WHERE status IN ('ENGINEERING','DEV','QA','INDEPENDENT_QA') AND paused=0")]
            for wid in ids: self.step(wid)
            time.sleep(2)
