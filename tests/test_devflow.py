import base64, json, tempfile
from pathlib import Path
from devflow.config import CONFIG
from devflow.db import DB
from devflow.worker import Worker
from devflow.worker import json_object

EXPECTED={
'ENGINEER':[('codex2','gpt-5.6-terra-medium'),('codex3','gpt-5.6-sol-medium'),('codex','gpt-5.6-sol-medium')],
'DEV:FRONTEND:SIMPLE':[('agy','gemini-3.7-flash-high'),('agy3','gemini-3.7-flash-high'),('codex2','gpt-5.6-luna'),('codex3','gpt-5.6-luna'),('codex','gpt-5.6-luna')],
'DEV:FRONTEND:COMPLEX':[('agy','gemini-3.8-flash-high'),('codex2','gpt-5.6-terra-medium'),('codex3','gpt-5.6-terra-medium'),('codex','gpt-5.6-terra-medium')],
'DEV:BACKEND:SIMPLE':[('codex2','gpt-5.6-luna'),('agy3','gemini-3.7-flash-high'),('codex3','gpt-5.6-luna'),('codex','gpt-5.6-luna')],
'DEV:BACKEND:COMPLEX':[('codex2','gpt-5.6-terra-medium'),('agy','gemini-3.8-flash-high'),('codex3','gpt-5.6-terra-medium'),('codex','gpt-5.6-terra-medium')],
'DEV:DATABASE:SIMPLE':[('codex2','gpt-5.6-luna'),('agy3','gemini-3.7-flash-high'),('codex3','gpt-5.6-luna'),('codex','gpt-5.6-luna')],
'DEV:DATABASE:COMPLEX':[('codex2','gpt-5.6-terra-medium'),('agy','gemini-3.8-flash-high'),('codex3','gpt-5.6-terra-medium'),('codex','gpt-5.6-terra-medium')],
'QA:SIMPLE':[('codex2','gpt-5.6-luna'),('agy3','gemini-3.7-flash-high'),('codex3','gpt-5.6-luna'),('codex','gpt-5.6-luna')],
'QA:COMPLEX':[('agy','gemini-3.8-flash-high'),('codex2','gpt-5.6-terra-medium'),('codex3','gpt-5.6-terra-medium'),('codex','gpt-5.6-terra-medium')],
'IQA:NORMAL':[('agy3','gemini-3.7-flash-high'),('agy','gemini-3.7-flash-high'),('codex2','gpt-5.6-luna'),('codex3','gpt-5.6-luna'),('codex','gpt-5.6-luna')],
'IQA:CRITICAL':[('codex2','gpt-5.6-terra-medium'),('agy','gemini-3.8-flash-high'),('codex3','gpt-5.6-terra-medium'),('codex','gpt-5.6-terra-medium')]}
def test_exact_model_matrix():
    assert {k:[tuple(x) for x in v] for k,v in CONFIG['routes'].items()}==EXPECTED

def test_json_envelope_accepts_model_trailing_text():
    assert json_object('{"result":"DONE"}\nbrief note') == {'result':'DONE'}

class FakeRouter:
    def __init__(self,db,fail_first=False,qa_fail=False,black_fail=False): self.db=db; self.fail_first=fail_first; self.qa_fail=qa_fail; self.black_fail=black_fail; self.calls=[]
    def candidates(self,role,area=None,complexity=None,criticality=None):
        key='ENGINEER' if role in ('ENGINEER','FINAL_ENGINEER') else (f'IQA:{criticality}' if role in ('IQA','INDEPENDENT_QA','WHITE_BOX_QA') else (f'QA:{complexity}' if role in ('QA','BLACK_BOX_QA') else f'DEV:{area}:{complexity}'))
        routes=CONFIG['routes'][key]; start=2 if self.fail_first and not self.calls else 1
        return [(i,p,m,CONFIG['providers'][p]) for i,(p,m) in enumerate(routes[start-1:],start)]
    def chat(self,p,m,cfg,messages):
        prompt=messages[-1]['content']; self.calls.append((p,m,prompt))
        if 'Break this approved' in prompt: out={'tasks':[{'title':'sum','area':'BACKEND','complexity':'SIMPLE','criticality':'NORMAL','objective':'create sum','acceptance_criteria':['sum works']}]}
        elif 'Implement this task' in prompt: out={'actions':[{'tool':'apply_patch','args':{'path':'sum.py','content':'def soma(a,b): return a+b\n'}}],'result':'DONE','summary':'implemented'}
        elif 'Verify concretely as QA' in prompt:
            fails=self.qa_fail and sum('Verify concretely as QA' in x[2] for x in self.calls)==1
            out={'result':'FAIL' if fails else 'PASS','issues':['controlled first failure'] if fails else [],'evidence':['checked'],'tests_run':[]}
        elif 'BLACK_BOX_QA' in prompt:
            fails=self.black_fail and sum('BLACK_BOX_QA' in x[2] for x in self.calls)==1
            out={'result':'FAIL' if fails else 'PASS','issues':['controlled black-box failure'] if fails else [],'summary':'browser evidence checked'}
        else: out={'result':'PASS','issues':[],'evidence':['independent'],'tests_run':[]}
        return {'choices':[{'message':{'content':json.dumps(out)}}],'usage':{}},0
    def unavailable(self,*a): pass

def run_until(worker,wid):
    for _ in range(20): worker.step(wid)

def fixture(root, vault):
    (vault/'08-QA-e-Auditoria').mkdir(parents=True)
    (root/'assets').mkdir()
    (root/'assets'/'arquitetura-limpa.png').write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLZ6QAAAABJRU5ErkJggg=='))
    (root/'index.html').write_text('<!doctype html><title>Controlled DevFlow evidence</title><img src="assets/arquitetura-limpa.png" alt="evidence">')

def test_e2e_a_and_persistence():
    with tempfile.TemporaryDirectory() as x:
        root=Path(x)/'repo'; root.mkdir(); vault=Path(x)/'vault'; fixture(root,vault); db=DB(Path(x)/'state.sqlite'); wid=db.create('e2e',str(root),'create sum',vault_path=str(vault))
        worker=Worker(db,FakeRouter(db),Path(x)/'worktrees'); run_until(worker,wid)
        assert db.get(wid)['status']=='PASSED'
        assert DB(Path(x)/'state.sqlite').get(wid)['status']=='PASSED'
        artifacts=db.artifacts(wid)
        assert {a['type'] for a in artifacts} >= {'QA_REPORT','BLACK_BOX_REPORT','WHITE_BOX_REPORT','IQA_REPORT','FINAL_REPORT','SCREENSHOT'}
        assert all(Path(a['path']).exists() for a in artifacts)

def test_e2e_b_correction_loop_and_fallback_position():
    with tempfile.TemporaryDirectory() as x:
        root=Path(x)/'repo'; root.mkdir(); vault=Path(x)/'vault'; fixture(root,vault); db=DB(Path(x)/'state.sqlite'); wid=db.create('e2e-b',str(root),'create sum',vault_path=str(vault))
        worker=Worker(db,FakeRouter(db,fail_first=True,black_fail=True),Path(x)/'worktrees'); run_until(worker,wid)
        task=db.tasks(wid)[0]
        assert db.get(wid)['status']=='PASSED' and task['corrections']==1
        with db.con() as c:
            assert c.execute('SELECT COUNT(*) n FROM runs WHERE fallback_position=2').fetchone()['n']>0
            bug=c.execute('SELECT source FROM bug_reports WHERE workflow_id=?',(wid,)).fetchone()
            assert bug['source']=='BLACK_BOX_QA'
            events=[r['message'] for r in c.execute('SELECT message FROM events WHERE workflow_id=? ORDER BY id',(wid,))]
        fail=next(i for i,x in enumerate(events) if 'BLACK_BOX_QA] FAIL -> DEV' in x)
        assert not any('[ENGINEER]' in x for x in events[fail+1:])

def test_unknown_vault_needs_user_without_creating_a_folder():
    with tempfile.TemporaryDirectory() as x:
        root=Path(x)/'repo'; root.mkdir(); db=DB(Path(x)/'state.sqlite'); wid=db.create('unknown',str(root),'create sum')
        Worker(db,FakeRouter(db),Path(x)/'worktrees').step(wid)
        workflow=db.get(wid)
        assert workflow['status']=='NEEDS_USER'
        assert workflow['blocker']=='Não sei qual é o vault correto deste projeto. Informe o caminho.'
