"""Owner cleanup of the exact failed first SQL nonce, preserving raw failure and source."""
from pathlib import Path
from datetime import datetime,timezone
import json,hashlib,subprocess,os,shlex,time
root=Path(__file__).resolve().parents[4];out=root/'results/reviews/sqlserver-manual-count-100m'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
read=lambda p:json.loads(p.read_text())
p=root/'results/sqlserver/manual-count-100m/runs/20261108T213706Z-b0798be83c64c6dc5992bed4adae8ed5/result.json';d=read(p)
canonical=read(out/'canonical-invocation.json');assert canonical['status']=='process_finished' and canonical['exitCode']==1 and d['exit_status']==1
assert d['source_unchanged'] and all(sha(root/f)==h for f,h in d['source_before'].items())
owner=d['owner'];assert owner=='b0798be83c64c6dc5992bed4adae8ed5'
stage=Path(d['stage']['path']);target=stage.parent;base=root/'.data/sqlserver-manual-count-100m'
assert target==base/owner and stage==target/'stage' and not target.is_symlink()
archive=root/'results/reference'/('sqlserver-manual-count-100m-monitor-syntax-failed-'+p.parent.name)
manifest=read(archive/'manifest.json');assert all(sha(archive/f)==h for f,h in manifest['files'].items())
commands=[];env={'PATH':os.environ['PATH'],'HOME':str(Path.home()),'DOCKER_CONFIG':str(Path.home()/'.docker')}
receipt={'status':'checking_exact_failed_nonce','startedAt':datetime.now(timezone.utc).isoformat(),'rawRun':str(p.relative_to(root)),'rawRunSha256':sha(p),'archiveManifest':str((archive/'manifest.json').relative_to(root)),'archiveManifestSha256':sha(archive/'manifest.json'),'commands':commands,'rawRunExitCode':1,'rawCleanup':d['cleanup'],'rawForcedPrimaryExitCode':d['lab-sqlserver-manual100m_terminal_state']['ExitCode']}
rp=out/'monitor-syntax-failure-owner-cleanup.json'
def persist():rp.write_text(json.dumps(receipt,indent=2)+'\n')
def run(argv,timeout=60):
 q=subprocess.run(argv,capture_output=True,text=True,env=env,timeout=timeout);commands.append({'argv':argv,'exitCode':q.returncode,'stdout':q.stdout,'stderr':q.stderr});persist();assert q.returncode==0,commands[-1];return q
run(['mise','-C','sqlserver/manual-count-100m','exec','--','ruby','-r','./model','-e','ManualModel.topology_stage!(*ARGV)',str(base),owner,str(stage)])
endpoint=d['context']['endpoint'];assert endpoint.startswith('unix://');cli=['docker','--host',endpoint]
for kind,args in [('container',['ls','-a']),('network',['ls']),('volume',['ls']),('image',['ls'])]:
 q=run(cli+[kind]+args+['--filter','label=count.manual100m.owner='+owner,'--format','{{json .}}']);assert not q.stdout.strip()
for kind,args,names,fmt in [('container',['ls','-a'],['lab-sqlserver-manual100m','lab-sqlserver-manual100m-secondary','lab-sqlserver-manual100m-client'],'{{.Names}}'),('network',['ls'],['lab-sqlserver-manual100m-network'],'{{.Name}}')]:
 for name in names:
  q=run(cli+[kind]+args+['--filter','name=^'+name+'$','--format',fmt]);assert not q.stdout.strip()
socket=Path(endpoint.removeprefix('unix://'));profile=socket.parent.name
assert d['context']['name']=='colima-'+profile and socket.parent.parent.name=='colima'
receipt['status']='validated_owned_topology_and_all_runtime_absent';persist()
run(['env','COLIMA_HOME='+str(socket.parent.parent),'colima','--profile',profile,'ssh','--','sh','-c','sudo -n -- rm -rf -- '+shlex.quote(str(target))])
assert not target.exists() and not target.is_symlink()
receipt['ownedRootAbsent']=True;receipt['ownedRuntimeAbsent']=True;receipt['status']='owned_paths_deleted_capacity_recheck';persist()
for i in range(30):
 q=run(['df','-Pk',str(root)]);free=int(q.stdout.splitlines()[-1].split()[3])*1024
 receipt['hostFreeBytes']=free
 if free>=140*1024**3:break
 time.sleep(1)
assert free>=140*1024**3
receipt.update(status='failed_run_preserved_owned_cleanup_verified',finishedAt=datetime.now(timezone.utc).isoformat(),meaning='Failure/forced primary137 remain failure. Independent owner post-terminal absence/deletion is not successful measurement. New run must be blank.');persist()
print(json.dumps({'status':receipt['status'],'hostFreeGiB':free/1024**3,'rawPrimaryExitCode':receipt['rawForcedPrimaryExitCode']}))
