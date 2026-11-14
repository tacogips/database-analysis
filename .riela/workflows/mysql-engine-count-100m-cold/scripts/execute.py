"""Owner fixed cold-buffer all after author terminal and interrupted reference cleanup."""
from pathlib import Path
from datetime import datetime,timezone
import json,hashlib,subprocess,time,shutil
root=Path(__file__).resolve().parents[4];out=root/'results/reviews/mysql-engine-count-100m-cold'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
read=lambda p:json.loads(p.read_text())
a=read(out/'author-launch.json');assert a['exitCode']==0 and a['runtimeStatus']=='completed'
assert not read(out/'author-result.json')['unresolvedIssues']
offline=read(out/'owner-offline-final.json');assert offline['exitCode']==0
for f,h in offline['sourceHashes'].items():assert sha(root/f)==h,f
duration=read(out/'load-duration-owner-application.json')
assert duration['status']=='applied_offline67_passed_ready_for_blank_all'
assert duration['offlineReceiptSha256']==sha(out/'owner-offline-final.json')
stop=read(out/'load-duration-stop-audit.json')
assert stop['ownedRuntimeAbsent'] and stop['ownedRootAbsent'] and stop['allProcessesReaped']
assert not Path(read(root/stop['rawResult'])['data_owner_root']).exists()
duration_author=read(out/'load-duration-author-launch.json')
assert duration_author['exitCode']==0 and duration_author['runtimeStatus']=='completed'
old=root/'results/reviews/mysql-engine-count-100m';prior=read(old/'canonical-invocation.json');assert prior['status']=='process_finished' and prior['exitCode']==1
reference=read(old/'interrupted-run-audit.json');assert reference['status']=='interrupted_not_accepted_resource_absence_verified' and reference['finalRuntimeAndDataAbsent'] and reference['allChildProcessesReaped']
oldrun=read(root/reference['rawResult']);assert not Path(oldrun['data_owner_root']).exists()
for f,h in read(out/'baseline.json')['protectedFiles'].items():assert sha(root/f)==h,f
endpoint=oldrun['context']['endpoint'];assert endpoint.startswith('unix://')
cmd=['docker','--host',endpoint,'container','ls','-a','--format','{{.Names}}'];inv=subprocess.run(cmd,capture_output=True,text=True,check=True)
assert not any(x in inv.stdout.splitlines() for x in ['lab-mysql-engine-count-100m','lab-mysql-engine-count-100m-cold'])
if (out/'canonical-invocation.json').exists():
 dest=out/('prior-invocation-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'));dest.mkdir()
 for n in ['canonical-invocation.json','canonical-invocation.log']:
  if (out/n).exists():shutil.copyfile(out/n,dest/n)
r={'command':['mise','-C','mysql/engine-count-100m-cold','run','all'],'status':'running','startedAt':datetime.now(timezone.utc).isoformat(),'authorSession':a['sessionId'],'durationAuthorSession':duration_author['sessionId'],'loadDurationApplicationSha256':sha(out/'load-duration-owner-application.json'),'loadDurationStopAuditSha256':sha(out/'load-duration-stop-audit.json'),'offlineReceiptSha256':sha(out/'owner-offline-final.json'),'interruptedReferenceAuditSha256':sha(old/'interrupted-run-audit.json'),'hostSpecSha256':sha(out/'host-spec.json'),'coldRequirement':'Fresh mysqld before every COUNT; load/dump off. OS/macOS cache uncontrolled, per user choice.'}
p=out/'canonical-invocation.json';p.write_text(json.dumps(r,indent=2)+'\n');start=time.monotonic()
with (out/'canonical-invocation.log').open('w') as log:q=subprocess.run(r['command'],cwd=root,stdout=log,stderr=subprocess.STDOUT)
r.update(status='process_finished',exitCode=q.returncode,finishedAt=datetime.now(timezone.utc).isoformat(),monotonicSeconds=time.monotonic()-start);p.write_text(json.dumps(r,indent=2)+'\n');print(json.dumps(r));raise SystemExit(q.returncode)
