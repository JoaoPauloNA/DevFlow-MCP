"""Local-only static and screenshot gates for visual DevFlow workflows."""
import subprocess
from pathlib import Path

BRAVE = '/Applications/Brave Browser.app/Contents/MacOS/Brave Browser'

def inspect_page(root):
    root = Path(root); html = root / 'index.html'; assets = root / 'assets'
    result = {'ok': False, 'issues': [], 'screenshots': []}
    if not html.is_file():
        result['issues'].append('index.html is missing'); return result
    source = html.read_text(errors='ignore')
    if 'referencia-tela.png' in source: result['issues'].append('reference image is rendered or referenced')
    if 'arquitetura-limpa.png' not in source: result['issues'].append('clean architecture image is not referenced')
    if any(x in source for x in ('http://','https://','file://')): result['issues'].append('external or absolute URL in page source')
    if not (assets/'arquitetura-limpa.png').is_file(): result['issues'].append('clean architecture asset is missing')
    if result['issues']: return result
    out = root / '.devflow-visual'; out.mkdir(exist_ok=True)
    for name, size in (('desktop',(1536,1024)),('mobile',(390,844))):
        shot = out / f'{name}.png'
        profile = out / f'.browser-{name}'
        cmd = [BRAVE,'--headless=new','--disable-gpu','--hide-scrollbars',f'--user-data-dir={profile}',f'--window-size={size[0]},{size[1]}',f'--screenshot={shot}',html.as_uri()]
        try:
            run = subprocess.run(cmd,text=True,capture_output=True,timeout=45)
            if run.returncode or not shot.is_file() or shot.stat().st_size < 1000: result['issues'].append(f'{name} screenshot failed')
            else: result['screenshots'].append(str(shot))
        except Exception as e: result['issues'].append(f'{name} screenshot failed: {type(e).__name__}')
    result['ok'] = not result['issues']; return result
