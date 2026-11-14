"""Owner-only foreground invocation after author terminal. The dedicated Ruby controller owns DB resources."""
from pathlib import Path
from datetime import datetime,timezone
import json,subprocess,time,shutil,hashlib
root=Path(__file__).resolve().parents[4];out=root/'results/reviews/issue-3'
a=json.loads((out/'author-launch.json').read_text());assert a['exitCode']==0 and a['runtimeStatus']=='completed'
assert not json.loads((out/'author-result.json').read_text())['unresolvedIssues']
b=json.loads((out/'baseline.json').read_text())
for f,h in b['protectedFiles'].items():assert hashlib.sha256((root/f).read_bytes()).hexdigest()==h,f
for name in ['canonical-invocation.json','canonical-invocation.log']:
 p=out/name
 if p.exists():
  folder=out/('prior-invocation-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'));folder.mkdir();shutil.copyfile(p,folder/name)
record={'command':['mise','-C','sqlserver/issue3','run','all'],'status':'running','startedAt':datetime.now(timezone.utc).isoformat(),'authorSession':a['sessionId'],'authorCompleted':True}
p=out/'canonical-invocation.json';p.write_text(json.dumps(record,indent=2)+'\n');start=time.monotonic()
with (out/'canonical-invocation.log').open('w') as log:r=subprocess.run(record['command'],cwd=root,stdout=log,stderr=subprocess.STDOUT)
record.update(status='process_finished',exitCode=r.returncode,finishedAt=datetime.now(timezone.utc).isoformat(),monotonicSeconds=time.monotonic()-start)
p.write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record));raise SystemExit(r.returncode)
