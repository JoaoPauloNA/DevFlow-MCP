import json, os, re, shutil, subprocess, time
from pathlib import Path
from .router import Router, RoleRouter, AvailabilityError, TechnicalError
from .tools import ToolRuntime
from .visual import inspect_page
from .reporting import qa_root, write_report

def content(response):
    return response.get('choices', [{}])[0].get('message', {}).get('content', '') or ''

def json_object(text):
    fenced = re.search(r'```(?:json)?\s*(.*?)```', text, re.S)
    candidate = (fenced.group(1) if fenced else text).strip()
    decoder = json.JSONDecoder()
    for marker in ('{', '['):
        start = candidate.find(marker)
        if start >= 0:
            try:
                return decoder.raw_decode(candidate[start:])[0]
            except json.JSONDecodeError:
                continue
    raise ValueError('model response did not contain a JSON envelope')

class Worker:
    def __init__(self, db, router=None, worktrees=None):
        self.db = db
        self.router = router or Router(db)
        self.worktrees = Path(worktrees or Path(db.path).parent / 'worktrees')

    def prepare(self, w):
        repo = Path(w['repo_path']).resolve()
        target = self.worktrees / w['id']
        if w['workspace_path']:
            return w['workspace_path']
        try:
            subprocess.run(['git', '-C', str(repo), 'rev-parse', '--is-inside-work-tree'], check=True, capture_output=True, text=True)
            self.worktrees.mkdir(parents=True, exist_ok=True)
            base = w['base_branch'] or 'HEAD'
            subprocess.run(['git', '-C', str(repo), 'worktree', 'add', '-b', w['id'], str(target), base], check=True, capture_output=True, text=True)
            path = str(target)
        except Exception as e:
            # Never touch a possibly dirty source checkout; a non-git test workspace is already isolated by caller.
            if (repo / '.git').exists():
                raise RuntimeError('isolated worktree unavailable: ' + str(e)[:180])
            path = str(repo)
        self.db.setwf(w['id'], workspace_path=path)
        return path

    def _get_workflow_routing_snapshot(self, w):
        """Parse frozen routing configuration from workflow record if available."""
        if not w:
            return None
        raw_routes = w.get('resolved_routes') if isinstance(w, dict) else (w['resolved_routes'] if 'resolved_routes' in w.keys() else None)
        if raw_routes:
            try:
                return json.loads(raw_routes) if isinstance(raw_routes, str) else raw_routes
            except Exception:
                return None
        return None

    def invoke(self, w, role, task=None, prompt=''):
        area = task['area'] if task else None
        complexity = task['complexity'] if task else None
        criticality = task['criticality'] if task else None
        
        routing_snapshot = self._get_workflow_routing_snapshot(w)
        
        # Candidate resolution
        raw_candidates = self.router.candidates(role, area, complexity, criticality, routing_config=routing_snapshot)
        
        for cand_entry in raw_candidates:
            pos, cand_obj, p, m, conn_cfg = cand_entry
            candidate_id = cand_obj.get('id', f"{role}-{p}-{pos}")
            
            # Extract transport info
            transport = conn_cfg.get('transport', 'proxy')
            provider = conn_cfg.get('provider', 'unknown')
            protocol = conn_cfg.get('protocol', 'unknown')
            connection_name = p

            started = time.time()
            self.db.event(w['id'], task['id'] if task else None, 'INFO', f'[{role}] started — {transport}/{provider}/{protocol} ({p}/{m})', {
                'candidate_id': candidate_id, 
                'position': pos,
                'transport': transport,
                'provider': provider,
                'protocol': protocol
            })
            
            try:
                messages = [
                    {'role': 'system', 'content': 'You are a DevFlow role runner. Return only compact JSON. Never reveal reasoning.'},
                    {'role': 'user', 'content': prompt}
                ]
                
                # Execute using router (which delegates to executor)
                response, retry = self.router.execute_chat(cand_obj, p, m, conn_cfg, messages)

                # Normalize response if it came from adapter
                text = response.get('text', content(response))
                input_tokens = response.get('input_tokens', response.get('usage', {}).get('prompt_tokens', 0))
                output_tokens = response.get('output_tokens', response.get('usage', {}).get('completion_tokens', 0))
                
                self.db.run(
                    workflow_id=w['id'],
                    task_id=task['id'] if task else None,
                    role=role,
                    area=area,
                    complexity=complexity,
                    criticality=criticality,
                    provider=provider, # canonicalized
                    model=m,
                    quota_group=conn_cfg.get('quota_group', p),
                    fallback_position=pos,
                    start=started,
                    end=time.time(),
                    duration=time.time() - started,
                    result='OK',
                    retry_count=retry,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cached_tokens=None,
                    detail=f"transport={transport} protocol={protocol}",
                    candidate_id=candidate_id,
                    connection=connection_name,
                    attempt=retry + 1,
                    technical_retry=retry,
                    fallback_reason=None
                )
                return text
            except (AvailabilityError, TechnicalError) as e:
                raw = str(e)
                quota = getattr(e, 'quota', False) or ('quota=True' in raw)
                if hasattr(self.router, 'unavailable'):
                    self.router.unavailable(connection_name, m, conn_cfg, raw, quota)
                self.db.event(w['id'], task['id'] if task else None, 'WARN', f'[{role}] fallback from {transport}/{provider}/{protocol}', {
                    'position': pos,
                    'candidate_id': candidate_id,
                    'quota': quota,
                    'reason': raw[:180]
                })

        raise RuntimeError('all configured candidates unavailable')

    def engineer(self, w):
        if not w['vault_path']:
            self.db.setwf(w['id'], status='NEEDS_USER', blocker='Não sei qual é o vault correto deste projeto. Informe o caminho.')
            self.db.event(w['id'], None, 'WARN', '[VAULT] path required')
            return
        try:
            qa_root(w['vault_path'])
        except Exception as e:
            self.db.setwf(w['id'], status='NEEDS_USER', blocker='Não sei qual é o vault correto deste projeto. Informe o caminho.')
            self.db.event(w['id'], None, 'WARN', '[VAULT] QA directory ambiguous', {'reason': str(e)})
            return
        workspace = self.prepare(w)
        prompt = f'''Break this approved roadmap into independently executable tasks. Repo: {workspace}. Roadmap: {w['roadmap']}. Return JSON object {{"tasks":[{{"title":"...","area":"FRONTEND|BACKEND|DATABASE|CROSS_CUTTING","complexity":"SIMPLE|COMPLEX","criticality":"NORMAL|CRITICAL","objective":"...","context_refs":[],"dependencies":[],"acceptance_criteria":[]}}]}}. Critical means auth, authorization, security, irreversible data, migration, transactions, payments, concurrency, critical infrastructure, or broad change. Break CROSS_CUTTING when possible.'''
        result = json_object(self.invoke(w, 'ENGINEER', prompt=prompt))
        if 'uma única tarefa DEV' in w['roadmap']:
            tasks = result.get('tasks', [])
            if len(tasks) != 1 or tasks[0].get('area') != 'FRONTEND' or tasks[0].get('complexity') != 'COMPLEX':
                raise RuntimeError('ENGINEER did not produce the required single FRONTEND/COMPLEX task')
        for x in result.get('tasks', []):
            self.db.addtask(w['id'], x)
        if not result.get('tasks'):
            raise RuntimeError('engineer produced no tasks')
        self.db.setwf(w['id'], status='DEV', last_result=f"engineer created {len(result['tasks'])} tasks")
        self.db.event(w['id'], None, 'INFO', f"[ENGINEER] created {len(result['tasks'])} tasks")

    def dev(self, w, t):
        root = w['workspace_path']
        rt = ToolRuntime(root)
        visual_first = ''
        if t['area'] == 'FRONTEND' and 'referencia-tela.png' in w['roadmap']:
            visual_first = 'THIS IS A VISUAL HTML TASK. Your FIRST and mandatory action must be apply_patch for path index.html with the complete page. Do not inspect with ls or run_command first. The page must reference assets/arquitetura-limpa.png and must never contain referencia-tela.png. '
        prompt = f'''Implement this task in workspace {root}. Task: {dict(t)}. {visual_first}Use ONLY this compact JSON protocol: {{"actions":[{{"tool":"read_file|list_files|search_text|apply_patch|run_command|run_tests|git_status|git_diff","args":{{...}}}}],"result":"DONE|NEEDS_INFO","summary":"..."}}. Paths must be relative to the workspace. To create a file use apply_patch {{"path":"relative/path","content":"full file content"}}. To correct an existing file with a small CSS/HTML addition use apply_patch {{"path":"index.html","append":"CSS or HTML to append"}}. Never use cat, echo, tee, heredocs, redirection, cd, &&, pipes, or shell substitutions. run_tests may omit args and will auto-detect tests. You receive action results in the next turn; do not claim DONE until the relevant tests succeeded.'''
        result = {}
        feedback = []
        for _ in range(4):
            suffix = '' if not feedback else '\nTool results from your prior actions:\n' + json.dumps(feedback)[-16000:] + '\nContinue with actions or return DONE.'
            result = json_object(self.invoke(w, 'DEV', t, prompt + suffix))
            actions = result.get('actions', [])
            feedback = []
            for a in actions[:12]:
                feedback.append(self._execute_action(w, t, rt, a))
            if result.get('result') == 'DONE':
                break
            if not actions:
                raise RuntimeError('DEV needs information without a safe next action')
        if result.get('result') != 'DONE':
            # The bounded conversation ended, but the only source of truth now
            # is the material worktree. Let QA reject it with concrete evidence.
            result['summary'] = 'DEV tool loop bounded; forwarded to QA for material verification'
            self.db.event(w['id'], t['id'], 'WARN', '[DEV] bounded tool loop; forwarding to QA')
        self.db.settask(t['id'], status='QA')
        self.db.setwf(w['id'], status='QA', current_task=t['id'], last_result=result.get('summary', 'DEV completed'))
        self.db.event(w['id'], t['id'], 'INFO', f"[DEV][{t['id']}] completed")

    def _execute_action(self, w, t, rt, a):
        tool = a.get('tool') or a.get('name')
        args = a.get('args') or a.get('parameters') or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        cmd = args.get('command', '')
        try:
            if tool == 'read_file':
                output = rt.read_file(args['path'])
            elif tool == 'list_files':
                output = rt.list_files(args.get('path', '.'))
            elif tool == 'search_text':
                output = rt.search_text(args['query'], args.get('path', ''))
            elif tool == 'apply_patch':
                output = rt.append_file(args['path'], args['append']) if 'append' in args else rt.apply_patch(args['path'], args['content'])
            elif tool == 'run_command':
                output = rt.command(cmd)
            elif tool == 'run_tests':
                checks = rt.default_checks()
                output = '\n'.join(f'{name}: {value}' for name, value in checks) if checks else 'no test command detected; use run_command with an allow-listed direct command'
            elif tool == 'git_status':
                output = rt.command('git status --short')
            elif tool == 'git_diff':
                output = rt.command('git diff --')
            else:
                raise ValueError('unknown tool')
            return {'tool': tool, 'ok': True, 'output': output[-8000:]}
        except Exception as e:
            self.db.event(w['id'], t['id'], 'WARN', f'[DEV] tool rejected: {tool}', {'reason': str(e)[:180]})
            return {'tool': tool, 'ok': False, 'error': str(e)[:500]}

    def qa(self, w, t, role='QA'):
        root = w['workspace_path']
        rt = ToolRuntime(root)
        diff = rt.command('git diff --')
        status = rt.command('git status --short')
        checks = rt.default_checks()
        needs_visual = role in ('BLACK_BOX_QA', 'WHITE_BOX_QA') or t['area'] == 'FRONTEND' or 'referencia-tela.png' in w['roadmap']
        visual = inspect_page(root) if needs_visual else {'ok': True, 'issues': [], 'screenshots': []}
        failed = [cmd for cmd, out in checks if 'exit=0' not in out]
        if role == 'BLACK_BOX_QA':
            prompt = f'''Verify as BLACK_BOX_QA using only user-observable behavior. Do not use source code or git diff to decide. Workspace {root}; objective {t['objective']}; acceptance {t['acceptance_criteria']}; browser evidence {visual['screenshots']}; issues {visual['issues']}. Return compact JSON {{"result":"PASS|FAIL|BLOCKED","issues":[],"evidence":[],"summary":"..."}}. PASS requires real visual evidence.'''
        elif role == 'WHITE_BOX_QA':
            prompt = f'''Audit as WHITE_BOX_QA. Do not edit. Workspace {root}; objective {t['objective']}; acceptance {t['acceptance_criteria']}; git status {status}; diff {diff}; executed checks {checks}; rendered evidence {visual['screenshots']}; visual issues {visual['issues']}. Return compact JSON {{"result":"PASS|FAIL|BLOCKED","issues":[],"evidence":[],"summary":"..."}}. Never turn a non-executed check into PASS.'''
        elif role == 'INDEPENDENT_QA':
            refs = [a['path'] for a in self.db.artifacts(w['id'], t['id']) if a['type'] in ('QA_REPORT', 'BLACK_BOX_REPORT', 'WHITE_BOX_REPORT')]
            prompt = f'''Audit independently. Workspace {root}; task {dict(t)}; report references {refs}; checks {checks}. Do not receive prior conversations. Return compact JSON {{"result":"PASS|FAIL|BLOCKED","issues":[],"evidence":[],"summary":"..."}}.'''
        elif role == 'FINAL_ENGINEER':
            refs = [a['path'] for a in self.db.artifacts(w['id'], t['id'])]
            prompt = f'''Act as FINAL_ENGINEER only. Do not implement. Verify roadmap, scope, criteria and evidence refs {refs}. Return compact JSON {{"result":"PASS|FAIL|BLOCKED","issues":[],"evidence":[],"summary":"..."}}.'''
        else:
            prompt = f'''Verify concretely as QA. Workspace {root}; objective {t['objective']}; acceptance {t['acceptance_criteria']}; git status {status}; diff {diff}; mechanically executed checks {checks}; visual gate issues {visual['issues']}. Return ONLY JSON {{"result":"PASS|FAIL|BLOCKED","issues":[],"evidence":[],"tests_run":[],"summary":"..."}}. Do not say it merely looks correct.'''
        result = json_object(self.invoke(w, role, t, prompt))
        verdict = result.get('result', 'FAIL')
        if not visual['ok'] and verdict == 'PASS':
            verdict = 'FAIL'
            result.setdefault('issues', []).extend(visual['issues'])
        if failed and verdict == 'PASS':
            verdict = 'FAIL'
            result.setdefault('issues', []).append('mechanical check failed: ' + ', '.join(failed))
        refs = [a['path'] for a in self.db.artifacts(w['id'], t['id']) if a['type'].endswith('_REPORT')]
        report, verdict, visual = write_report(self.db, w, t, role, verdict, result, checks, refs)
        if verdict == 'PASS':
            next_state = {'QA': 'BLACK_BOX_QA', 'BLACK_BOX_QA': 'WHITE_BOX_QA', 'WHITE_BOX_QA': 'INDEPENDENT_QA', 'INDEPENDENT_QA': 'FINAL_ENGINEERING', 'FINAL_ENGINEER': 'PASSED'}[role]
            self.db.settask(t['id'], status=next_state, qa_result='PASS' if role == 'QA' else t['qa_result'], iqa_result='PASS' if role == 'INDEPENDENT_QA' else t['iqa_result'])
            self.db.setwf(w['id'], status=next_state, last_result=f'{role} PASS: {report}')
            self.db.event(w['id'], t['id'], 'INFO', f'[{role}] PASS', {'report': report})
            return
        if verdict == 'BLOCKED':
            self.db.setwf(w['id'], status='BLOCKED', blocker=f'{role} blocked: {report}', last_result=verdict)
            return
        n = t['corrections'] + 1
        bug = self.db.bug(w['id'], t['id'], role, 'HIGH', f'{role} failed: {t["title"]}', '; '.join(result.get('issues', [])) or 'QA failure', t['acceptance_criteria'], str(result.get('summary', '')), [report], t['area'])
        if n > 3:
            self.db.setwf(w['id'], status='NEEDS_USER', blocker='maximum DEV→QA correction cycles exceeded', last_result=bug)
        else:
            self.db.settask(t['id'], status='DEV', corrections=n, qa_result='FAIL' if role == 'QA' else t['qa_result'], iqa_result='FAIL' if role == 'INDEPENDENT_QA' else t['iqa_result'])
            self.db.setwf(w['id'], status='DEV', last_result=bug, current_task=t['id'])
            self.db.event(w['id'], t['id'], 'WARN', f'[{role}] FAIL -> DEV correction {n}/3', {'bug_id': bug, 'report': report})

    def step(self, wid):
        w = self.db.get(wid)
        if not w or w['paused'] or w['status'] in ('PASSED', 'FAILED', 'CANCELLED', 'NEEDS_USER', 'BLOCKED'):
            return
        try:
            if w['status'] in ('READY', 'ENGINEERING'):
                self.db.setwf(wid, status='ENGINEERING')
                return self.engineer(self.db.get(wid))
            ts = self.db.tasks(wid)
            active = next((x for x in ts if x['status'] in ('PENDING', 'DEV', 'QA', 'BLACK_BOX_QA', 'WHITE_BOX_QA', 'INDEPENDENT_QA', 'FINAL_ENGINEERING')), None)
            if not active:
                if ts and all(x['status'] == 'PASSED' for x in ts):
                    self.db.setwf(wid, status='PASSED', last_result='all tasks passed')
                    self.db.event(wid, None, 'INFO', '[WORKFLOW] PASSED')
                return
            self.db.setwf(wid, current_task=active['id'])
            if active['status'] in ('PENDING', 'DEV'):
                return self.dev(self.db.get(wid), active)
            role = {'QA': 'QA', 'BLACK_BOX_QA': 'BLACK_BOX_QA', 'WHITE_BOX_QA': 'WHITE_BOX_QA', 'INDEPENDENT_QA': 'INDEPENDENT_QA', 'FINAL_ENGINEERING': 'FINAL_ENGINEER'}[active['status']]
            return self.qa(self.db.get(wid), active, role)
        except Exception as e:
            self.db.setwf(wid, status='NEEDS_USER', blocker=str(e))
            self.db.event(wid, None, 'ERROR', '[WORKFLOW] needs user', {'reason': str(e)})

    def run_forever(self):
        while True:
            with self.db.con() as c:
                ids = [r['id'] for r in c.execute("SELECT id FROM workflows WHERE status IN ('ENGINEERING','DEV','QA','BLACK_BOX_QA','WHITE_BOX_QA','INDEPENDENT_QA','FINAL_ENGINEERING') AND paused=0")]
            for wid in ids:
                self.step(wid)
            time.sleep(2)
