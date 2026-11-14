"""Foreground owner invocation after author terminal; dedicated Ruby all owns data and resources."""
from pathlib import Path
from datetime import datetime,timezone
import json,subprocess,time,shutil,hashlib
root=Path(__file__).resolve().parents[4];out=root/'results/reviews/mysql-engine-count-100m'
a=json.loads((out/'author-launch.json').read_text());assert a['exitCode']==0 and a['runtimeStatus']=='completed'
assert not json.loads((out/'author-result.json').read_text())['unresolvedIssues']
b=json.loads((out/'baseline.json').read_text())
for f,h in b['protectedFiles'].items():assert hashlib.sha256((root/f).read_bytes()).hexdigest()==h,f
old=out/'canonical-invocation.json'
if old.exists():
 folder=out/('prior-invocation-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'));folder.mkdir()
 for n in ['canonical-invocation.json','canonical-invocation.log']:
  if (out/n).exists():shutil.copyfile(out/n,folder/n)
r={'command':['mise','-C','mysql/engine-count-100m','run','all'],'status':'running','startedAt':datetime.now(timezone.utc).isoformat(),'authorSession':a['sessionId'],'ownerExtensions':'Latest user steering: frozen manual pre-count index/page inspection, varied records and additional composite reference; owner offline verifier passed.'}
p=out/'canonical-invocation.json';p.write_text(json.dumps(r,indent=2)+'\n');start=time.monotonic()
with (out/'canonical-invocation.log').open('w') as log:q=subprocess.run(r['command'],cwd=root,stdout=log,stderr=subprocess.STDOUT)
r.update(status='process_finished',exitCode=q.returncode,finishedAt=datetime.now(timezone.utc).isoformat(),monotonicSeconds=time.monotonic()-start);p.write_text(json.dumps(r,indent=2)+'\n');print(json.dumps(r));raise SystemExit(q.returncode)
