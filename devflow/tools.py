import json
import shlex
import subprocess
from pathlib import Path
class ToolRuntime:
    def __init__(self,root): self.root=Path(root).resolve()
    def path(self,p):
        raw=Path(p)
        # Some compatible endpoints echo a workspace-relative path prefixed by
        # the worktree location. Normalize only that exact prefix, never ../.
        if not raw.is_absolute() and self.root.name in raw.parts:
            raw=Path(*raw.parts[raw.parts.index(self.root.name)+1:])
        x=(raw if raw.is_absolute() else self.root/raw).resolve()
        if x != self.root and self.root not in x.parents: raise ValueError('path outside workspace')
        return x
    def list_files(self,p='.'): return '\n'.join(str(x.relative_to(self.root)) for x in self.path(p).rglob('*') if x.is_file())[:12000]
    def read_file(self,p): return self.path(p).read_text()[:30000]
    def search_text(self,q,p=''):
        return '\n'.join(f'{x.relative_to(self.root)}:{i}:{line}' for x in self.path(p or '.').rglob('*') if x.is_file() for i,line in enumerate(x.read_text(errors='ignore').splitlines(),1) if q.lower() in line.lower())[:20000]
    def apply_patch(self,p,content):
        x=self.path(p); x.parent.mkdir(parents=True,exist_ok=True); x.write_text(content); return 'written '+p
    def append_file(self,p,content):
        x=self.path(p)
        if not x.is_file(): raise ValueError('cannot append a missing file')
        x.write_text(x.read_text()+content); return 'appended '+p
    def command(self,cmd):
        """Execute one allow-listed, non-shell command inside the worktree."""
        if any(x in cmd for x in (';', '|', '&', '`', '$(', '>', '<', '\n')):
            raise ValueError('shell composition is blocked')
        argv=shlex.split(cmd)
        if not argv: raise ValueError('empty command')
        allowed={'git','npm','pnpm','yarn','bun','pytest','python','python3','node','npx','ruff','eslint','go','cargo','make','mvn','gradle','./gradlew','./mvnw'}
        if argv[0] not in allowed: raise ValueError(f'command not allowed: {argv[0]}')
        banned={'reset','clean','push','branch','checkout','restore','rebase'}
        if argv[0]=='git' and any(part in banned for part in argv[1:]): raise ValueError('unsafe git operation')
        r=subprocess.run(argv,cwd=self.root,text=True,capture_output=True,timeout=120)
        return (r.stdout+r.stderr)[-20000:]+f'\nexit={r.returncode}'
    def default_checks(self):
        checks=[]
        if (self.root/'package.json').exists(): checks.append('npm test')
        if any(self.root.glob('test_*.py')) or (self.root/'tests').exists(): checks.append('python3 -m unittest discover')
        return [(cmd,self.command(cmd)) for cmd in checks]
