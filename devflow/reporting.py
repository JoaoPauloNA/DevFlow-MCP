"""Immutable, vault-local QA evidence and report writer."""
import hashlib
import shutil
import time
from datetime import datetime
from pathlib import Path

from .visual import inspect_page

ROLE_REPORT = {
    'QA': 'QA_REPORT', 'BLACK_BOX_QA': 'BLACK_BOX_REPORT',
    'WHITE_BOX_QA': 'WHITE_BOX_REPORT', 'INDEPENDENT_QA': 'IQA_REPORT',
    'FINAL_ENGINEER': 'FINAL_REPORT',
}
ROLE_LABEL = {
    'QA': 'QA-Inicial', 'BLACK_BOX_QA': 'QA-Black-Box',
    'WHITE_BOX_QA': 'QA-White-Box', 'INDEPENDENT_QA': 'QA-Independente',
    'FINAL_ENGINEER': 'Engenharia-Final',
}

def _safe(value):
    return ''.join(c if c.isalnum() or c in '-_' else '-' for c in value).strip('-') or 'escopo'

def qa_root(vault_path):
    root=Path(vault_path).expanduser().resolve()
    candidate=root/'08-QA-e-Auditoria'
    if candidate.is_dir(): return candidate
    # Do not manufacture a project convention. A pre-existing explicit QA
    # directory is acceptable; otherwise worker asks the user.
    found=[p for p in root.iterdir() if p.is_dir() and 'qa' in p.name.lower() and ('audit' in p.name.lower() or 'qualidade' in p.name.lower())]
    if len(found)==1: return found[0]
    raise ValueError('vault QA report directory is unknown; confirm the project QA folder')

def _unique(base, stem):
    path=base/f'{stem}.md'; number=2
    while path.exists():
        path=base/f'{stem}-R{number}.md'; number+=1
    return path

def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def write_report(db, workflow, task, role, verdict, result, checks=(), report_refs=()):
    base=qa_root(workflow['vault_path'])
    date=datetime.now().strftime('%Y-%m-%d'); scope=_safe(task['title'])
    label=ROLE_LABEL[role]; report=_unique(base, f'{date}__{label}-{scope}-{role}')
    asset_dir=base/'Assets'/report.stem
    visual={'ok': True, 'issues': [], 'screenshots': []}
    # UI evidence must be an actual browser-rendered image. No image is
    # invented if capture is unavailable; the verdict is then BLOCKED/FAIL.
    if role in ('BLACK_BOX_QA','WHITE_BOX_QA'):
        visual=inspect_page(workflow['workspace_path'])
        if visual['ok']:
            asset_dir.mkdir(parents=True, exist_ok=False)
            copied=[]
            for index, source in enumerate(visual['screenshots'],1):
                target=asset_dir/f'{index:02d}-real-{role.lower()}.png'
                shutil.copy2(source,target); copied.append(target)
                db.artifact(workflow['id'],task['id'],'SCREENSHOT',str(target),role,_hash(target),{'purpose':'real browser render'})
            visual['screenshots']=[str(x) for x in copied]
        else:
            verdict='BLOCKED' if verdict=='PASS' else verdict
    lines=[
        f'# {label} — {_safe(workflow["project_name"])} — {task["title"]}', '',
        f'**Data:** {date}  ', f'**Agente:** `{role}`  ', f'**Workflow:** `{workflow["id"]}`  ',
        f'**Tarefa:** `{task["id"]}`  ', f'**Escopo:** {task["objective"]}', '',
        '## Conclusão', '', f'**Veredito: {verdict}.**', '',
        '## Critérios e execução', '', '| Critério | Resultado esperado | Resultado observado | Veredito |', '| --- | --- | --- | --- |',
    ]
    criteria=__import__('json').loads(task['acceptance_criteria'] or '[]')
    for criterion in criteria or ['Critérios não detalhados no workflow']:
        lines.append(f'| {criterion} | Atendido sem contradição | {result.get("summary") or result.get("issues") or "Ver evidências"} | {verdict} |')
    lines += ['', '## Evidências técnicas', '']
    for command, output in checks:
        lines += [f'### Comando — `{command}`', '```text', str(output)[-4000:], '```', '']
    for index, screenshot in enumerate(visual.get('screenshots',[]),1):
        relative=Path(screenshot).relative_to(base)
        lines += [f'### Evidência {index} — Renderização real', '', f'![Evidência real {index}]({relative.as_posix()})', '', 'Resultado esperado:', 'Renderização utilizável sem os defeitos detectados pelo gate visual.', '', 'Resultado observado:', 'Captura real do navegador armazenada localmente.', '', f'Veredito: {verdict}', '']
    if visual.get('issues'):
        lines += ['## Limitações / bloqueios', *[f'- {issue}' for issue in visual['issues']], '']
    if report_refs:
        lines += ['## Referências de QA', *[f'- `{Path(ref).name}`' for ref in report_refs], '']
    lines += ['## Falhas e riscos', *([f'- {x}' for x in result.get('issues',[])] or ['- Nenhuma falha adicional registrada nesta rodada.']), '']
    report.write_text('\n'.join(lines),encoding='utf-8')
    artifact_type=ROLE_REPORT[role]
    db.artifact(workflow['id'],task['id'],artifact_type,str(report),role,_hash(report),{'verdict':verdict})
    return str(report), verdict, visual
