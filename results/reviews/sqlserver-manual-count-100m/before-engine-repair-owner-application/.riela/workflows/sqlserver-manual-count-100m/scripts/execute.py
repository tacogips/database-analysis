"""Owner-only SQL run, strictly after terminal MySQL measurement AND cleanup audit."""
from pathlib import Path
from datetime import datetime,timezone
import json,hashlib,subprocess,time,shutil,os
root=Path(__file__).resolve().parents[4];out=root/'results/reviews/sqlserver-manual-count-100m'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
read=lambda p:json.loads(p.read_text())
a=read(out/'author-launch.json');assert a['exitCode']==0 and a['runtimeStatus']=='completed'
assert not read(out/'author-result.json')['unresolvedIssues']
offline=read(out/'owner-state-matrix-offline-final.json');assert offline['exitCode']==0 and offline['status']=='passed_offline_only'
for f,h in offline['sourceHashes'].items():assert sha(root/f)==h,f
repair=read(out/'entrypoint-repair-owner-application.json')
assert repair['status']=='applied_offline18_passed_ready_for_blank_all'
assert repair['offlineReceiptSha256']==sha(out/'owner-state-matrix-offline-final.json')
for file in ['runtime-repair-author-launch.json','entrypoint-repair-author-launch.json']:
 amendment=read(out/file);assert amendment['exitCode']==0 and amendment['runtimeStatus']=='completed' and amendment['model']=='gpt-6.1-sol'
old_cleanup=read(out/'monitor-syntax-failure-owner-cleanup.json')
assert old_cleanup['ownedRuntimeAbsent'] and old_cleanup['ownedRootAbsent']
assert not Path(read(root/old_cleanup['rawRun'])['stage']['path']).parent.exists()
startup_cleanup=read(out/'startup-proof-failure-owner-cleanup.json')
assert startup_cleanup['ownedRuntimeAbsent'] and startup_cleanup['ownedRootAbsent']
assert not Path(read(root/startup_cleanup['rawRun'])['stage']['path']).parent.exists()
mysql=root/'results/reviews/mysql-engine-count-100m-cold';m=read(mysql/'canonical-invocation.json')
assert m['status']=='process_finished' and m['exitCode']==0,'Never overlap SQL DB/build/pulls with MySQL measurement'
audit=read(mysql/'owner-audit.json');assert audit['status']=='passed' and all(x['passed'] for x in audit['checks'])
run=read(root/audit['run']);assert run['cleanup']=='passed_owned_runtime_and_hostdata_absent' and not Path(run['data_owner_root']).exists()
for f,h in read(out/'baseline.json')['protectedFiles'].items():assert sha(root/f)==h,f
endpoint=run['context']['endpoint'];assert endpoint.startswith('unix://')
env={'PATH':os.environ['PATH'],'HOME':str(Path.home()),'DOCKER_CONFIG':str(Path.home()/'.docker'),'LANG':'C.UTF-8'}
cli=['docker','--host',endpoint]
inventory=subprocess.run(cli+['container','ls','-a','--format','{{.Names}}'],capture_output=True,text=True,env=env,check=True)
assert not any(name in inventory.stdout.splitlines() for name in ['lab-mysql-engine-count-100m','lab-mysql-engine-count-100m-cold'])
for name in ['canonical-invocation.json','image-preparation.json']:
 if (out/name).exists():
  folder=out/('prior-'+name.removesuffix('.json')+'-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'));folder.mkdir()
  for f in [name,name.replace('.json','.log')]:
   if (out/f).exists():shutil.copyfile(out/f,folder/f)
prep={'status':'preparing','startedAt':datetime.now(timezone.utc).isoformat(),'mysqlOwnerAuditSha256':sha(mysql/'owner-audit.json'),'commands':[]}
pp=out/'image-preparation.json';pp.write_text(json.dumps(prep,indent=2)+'\n')
images=[('linux/amd64','mcr.microsoft.com/mssql/server:2025-latest@sha256:245f6b1c8903edae52cb20fa31419cefcde1a49938bb8c3447099011fc871ca3'),('linux/arm64','mcr.microsoft.com/dotnet/sdk:10.0@sha256:212fe4cb74627d5adf9817a4088906ae3edff1bd853bb27565d4b46731efe61e')]
with (out/'image-preparation.log').open('w') as log:
 for platform,image in images:
  cmd=cli+['pull','--platform',platform,image];start=time.monotonic();q=subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,env=env)
  prep['commands'].append({'argv':cmd,'exitCode':q.returncode,'monotonicSeconds':time.monotonic()-start});pp.write_text(json.dumps(prep,indent=2)+'\n');assert q.returncode==0,'pinned image pull failed, preserve logs'
prep.update(status='completed',finishedAt=datetime.now(timezone.utc).isoformat());pp.write_text(json.dumps(prep,indent=2)+'\n')
r={'command':['mise','-C','sqlserver/manual-count-100m','run','all'],'status':'running','startedAt':datetime.now(timezone.utc).isoformat(),'authorSession':a['sessionId'],'repairAuthorSessions':[read(out/f)['sessionId'] for f in ['runtime-repair-author-launch.json','entrypoint-repair-author-launch.json']],'repairApplicationSha256':sha(out/'entrypoint-repair-owner-application.json'),'offlineReceiptSha256':sha(out/'owner-state-matrix-offline-final.json'),'mysqlOwnerAuditSha256':sha(mysql/'owner-audit.json'),'hostSpecSha256':sha(out/'host-spec.json'),'imagePreparationSha256':sha(pp),'rosettaRequired':True}
p=out/'canonical-invocation.json';p.write_text(json.dumps(r,indent=2)+'\n');start=time.monotonic()
with (out/'canonical-invocation.log').open('w') as log:q=subprocess.run(r['command'],cwd=root,stdout=log,stderr=subprocess.STDOUT)
r.update(status='process_finished',exitCode=q.returncode,finishedAt=datetime.now(timezone.utc).isoformat(),monotonicSeconds=time.monotonic()-start);p.write_text(json.dumps(r,indent=2)+'\n');print(json.dumps(r));raise SystemExit(q.returncode)
