import json, time, urllib.request, urllib.error
from .config import CONFIG, secret_values

class AvailabilityError(Exception): pass
class Router:
    def __init__(self,db): self.db=db; self.secrets=secret_values()
    def route(self,role,area=None,complexity=None,criticality=None):
        # New QA roles deliberately inherit the already-approved QA/IQA
        # policies. This adds no account or provider and preserves routes.
        if role in ('BLACK_BOX_QA','QA'): key=f'QA:{complexity}'
        elif role in ('WHITE_BOX_QA','INDEPENDENT_QA','IQA'): key=f'IQA:{criticality}'
        elif role in ('ENGINEER','FINAL_ENGINEER'): key='ENGINEER'
        else: key=f'DEV:{area}:{complexity}'
        return [(p,m,CONFIG['providers'][p]) for p,m in CONFIG['routes'][key]]
    def primary_codex_allowed(self):
        """Keep primary Codex as a quota-only fallback for Codex 2 and 3."""
        now=time.time()
        with self.db.con() as c:
            exhausted={row['quota_group'] for row in c.execute(
                'SELECT quota_group FROM provider_health WHERE status="QUOTA_EXHAUSTED" AND until>?',
                (now,)
            )}
        return {'codex2','codex3'} <= exhausted
    def candidates(self,*a):
        now=time.time(); out=[]
        for pos,(p,m,cfg) in enumerate(self.route(*a),1):
            with self.db.con() as c:
                h=c.execute('SELECT status,until FROM provider_health WHERE provider=? AND model=?',(p,m)).fetchone()
                group=c.execute('SELECT MAX(until) u FROM provider_health WHERE quota_group=? AND status="QUOTA_EXHAUSTED"',(cfg['quota_group'],)).fetchone()
            if (h and h['until']>now) or (group and group['u'] and group['u']>now): continue
            out.append((pos,p,m,cfg))
        return out
    def unavailable(self,p,m,cfg,reason,quota=False):
        until=time.time()+(900 if quota else 120)
        with self.db.con() as c: c.execute('INSERT OR REPLACE INTO provider_health VALUES(?,?,?,?,?,?,?)',(p,m,cfg['quota_group'],'QUOTA_EXHAUSTED' if quota else 'UNAVAILABLE',until,reason,time.time()))
    def chat(self,p,m,cfg,messages):
        body=json.dumps({'model':m,'messages':messages,'temperature':0.1,'max_tokens':12000}).encode()
        req=urllib.request.Request(cfg['base_url']+'/chat/completions',data=body,headers={'Authorization':'Bearer '+self.secrets[cfg['secret_key']],'Content-Type':'application/json'})
        for retry in range(2):
            try: return json.load(urllib.request.urlopen(req,timeout=180)),retry
            except urllib.error.HTTPError as e:
                raw=e.read(400).decode(errors='replace').lower(); quota=e.code in (401,403,429) or 'quota' in raw or 'rate' in raw
                if retry or quota: raise AvailabilityError(f'{e.code}:{raw[:80]}|quota={quota}')
            except (urllib.error.URLError,TimeoutError) as e:
                if retry: raise AvailabilityError(f'{type(e).__name__}|quota=False')
        raise AvailabilityError('unreachable|quota=False')
