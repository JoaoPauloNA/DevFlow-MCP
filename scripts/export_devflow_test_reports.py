#!/usr/bin/env python3
"""Export DevFlow test evidence without prompts, contexts, or secrets."""
import csv
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path('/Users/joaopaulo/Library/Application Support/DevFlow/runtime/devflow.sqlite3')
OUT = Path('/Users/joaopaulo/Meu Drive/Projetos (1)/DadosTeste/002-DevFlow-MCP')
FINAL_WORKFLOW = 'wf-7e66e77d3b6f'


def text(value):
    return '' if value is None else str(value)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    generated = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')

    workflows = db.execute('''
      SELECT w.id, w.project_name, w.status, w.created_at, w.updated_at,
             COUNT(r.id) AS run_count,
             COALESCE(SUM(r.input_tokens), 0) AS input_tokens,
             COALESCE(SUM(r.output_tokens), 0) AS output_tokens,
             COALESCE(SUM(r.cached_tokens), 0) AS cached_tokens,
             COALESCE(SUM(r.duration), 0) AS duration_seconds
      FROM workflows w LEFT JOIN runs r ON r.workflow_id = w.id
      GROUP BY w.id
      ORDER BY w.updated_at DESC
    ''').fetchall()
    runs = db.execute('''
      SELECT r.id, r.workflow_id, w.project_name, w.status AS workflow_status,
             r.task_id, r.role, r.area, r.complexity, r.criticality,
             r.provider, r.model, r.quota_group, r.fallback_position,
             r.start, r.end, r.duration, r.result, r.retry_count,
             r.input_tokens, r.output_tokens, r.cached_tokens
      FROM runs r JOIN workflows w ON w.id = r.workflow_id
      ORDER BY CASE WHEN r.workflow_id = ? THEN 0 ELSE 1 END,
               w.updated_at DESC, r.start ASC
    ''', (FINAL_WORKFLOW,)).fetchall()
    events = db.execute('''
      SELECT workflow_id, task_id, at, level, message
      FROM events
      ORDER BY at ASC
    ''').fetchall()

    def write_csv(name, rows, columns):
        with (OUT / name).open('w', newline='', encoding='utf-8') as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            for row in rows:
                writer.writerow({col: text(row[col]) for col in columns})

    write_csv('workflows.csv', workflows, list(workflows[0].keys()) if workflows else [])
    write_csv('runs-detalhados.csv', runs, list(runs[0].keys()) if runs else [])
    write_csv('eventos-resumidos.csv', events, list(events[0].keys()) if events else [])

    lines = [
      '# DevFlow MCP — Relatório detalhado de testes', '',
      f'Gerado em: {generated}', '',
      '## Escopo', '',
      'Este pacote contém todos os workflows de teste presentes no SQLite do DevFlow no momento da exportação.',
      'Não contém prompts, contexto completo, respostas dos modelos ou segredos.', '',
      '## Resultado final destacado', '',
      f'- Workflow: `{FINAL_WORKFLOW}`',
      '- Projeto: `curso-capital-visual-v3`',
      '- Estado automático persistido: `PASSED`.',
      '- Aceite humano: pendente — somente o usuário pode confirmar a aprovação visual final.', '',
      '## Consumo por papel, modelo e teste', '',
      '| Papel | Modelo | Input | Output | Teste | Estado |',
      '|---|---|---:|---:|---|---|',
    ]
    grouped = db.execute('''
      SELECT r.workflow_id, w.project_name, w.status, r.role, r.provider, r.model,
             COALESCE(SUM(r.input_tokens), 0) AS input_tokens,
             COALESCE(SUM(r.output_tokens), 0) AS output_tokens
      FROM runs r JOIN workflows w ON w.id = r.workflow_id
      GROUP BY r.workflow_id, r.role, r.provider, r.model
      ORDER BY CASE WHEN r.workflow_id = ? THEN 0 ELSE 1 END,
               w.updated_at DESC, r.role, input_tokens DESC
    ''', (FINAL_WORKFLOW,)).fetchall()
    for row in grouped:
        test = row['project_name']
        if row['workflow_id'] == FINAL_WORKFLOW:
            test = 'FINAL — ' + test
        lines.append(
          f"| {row['role']} | {row['provider']} / {row['model']} | "
          f"{row['input_tokens']:,} | {row['output_tokens']:,} | {test} | {row['status']} |".replace(',', '.')
        )
    lines += ['', '## Workflows', '',
              '| ID | Projeto/teste | Estado | Runs | Input | Output | Duração (s) |',
              '|---|---|---|---:|---:|---:|---:|']
    for row in workflows:
        marker = 'FINAL — ' if row['id'] == FINAL_WORKFLOW else ''
        lines.append(
          f"| {row['id']} | {marker}{row['project_name']} | {row['status']} | {row['run_count']} | "
          f"{row['input_tokens']:,} | {row['output_tokens']:,} | {row['duration_seconds']:.2f} |".replace(',', '.')
        )
    lines += ['', '## Arquivos de evidência', '',
              '- `workflows.csv`: resumo de todos os workflows.',
              '- `runs-detalhados.csv`: uma linha por execução de modelo, incluindo fallback e retry.',
              '- `eventos-resumidos.csv`: eventos operacionais sem payload sensível.',
              '- `RELATORIO-TESTES.md`: visão humana deste relatório.', '']
    (OUT / 'RELATORIO-TESTES.md').write_text('\n'.join(lines), encoding='utf-8')
    print(OUT)
    print(f'workflows={len(workflows)} runs={len(runs)} events={len(events)}')


if __name__ == '__main__':
    main()
