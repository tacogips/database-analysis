"""Audit the one explicitly interrupted nonce, remove its owned fixture, archive evidence/source."""
from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,os,subprocess,shlex,shutil,stat
root=Path(__file__).resolve().parents[4];out=root/'results/reviews/mysql-engine-count-100m-cold'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
read=lambda p:json.loads(p.read_text())
decision=read(out/'load-duration-interruption.json');p=root/decision['rawResult'];d=read(p);canonical=read(out/'canonical-invocation.json')
assert canonical['status']=='process_finished' and canonical['exitCode']==1 and d['exit_status']==1
assert d['source_unchanged'] and all(sha(root/f)==h for f,h in d['source_before'].items())
assert all(x.get('reaped') and isinstance(x.get('exit_status'),int) for x in d['processes'])
assert all(s.get('container_exit_status')==0 and s.get('server_attach_exit_status')==0 for s in d['stages'])
owner=d['run_id'].split('-')[-1];target=Path(d['data_owner_root'])
assert owner=='ca74a6381f9f93ff0ed6ed7c87471500' and target==root/'.data/engine-count-100m-cold'/owner
assert not target.is_symlink()
root_existed_before=target.exists()
if root_existed_before:
 for directory in [root/'.data',target.parent,target,target/'MyISAM',target/'MyISAM/stage']:
  assert directory.resolve()==directory and not directory.is_symlink() and directory.stat().st_uid==os.getuid() and stat.S_IMODE(directory.stat().st_mode)&0o077==0,str(directory)
 marker=target/'.owner.json';assert not marker.is_symlink() and read(marker)=={'nonce':owner,'root':str(target),'uid':os.getuid()}
 assert set(x.name for x in target.iterdir())=={'.owner.json','MyISAM'}
 for parent,dirs,files in os.walk(target,followlinks=False):
  assert not any((Path(parent)/name).is_symlink() for name in dirs+files)
endpoint=d['context']['endpoint'];assert endpoint.startswith('unix://')
socket=Path(endpoint.removeprefix('unix://'));profile=socket.parent.name;assert d['context']['name']=='colima-'+profile and socket.parent.parent.name=='colima'
env={'PATH':os.environ['PATH'],'HOME':str(Path.home()),'DOCKER_CONFIG':str(Path.home()/'.docker')};commands=[]
def run(argv,expected=0):
 q=subprocess.run(argv,capture_output=True,text=True,env=env,timeout=60);commands.append({'argv':argv,'exitCode':q.returncode,'stdout':q.stdout,'stderr':q.stderr});assert expected is None or q.returncode==expected,commands[-1];return q
for kind,args in [('container',['ls','-a']),('network',['ls']),('volume',['ls'])]:
 q=run(['docker','--host',endpoint,kind]+args+['--filter','label=count.engine100m.cold.owner='+owner,'--format','{{json .}}']);assert not q.stdout.strip()
q=run(['docker','--host',endpoint,'container','ls','-a','--format','{{.Names}}']);assert 'lab-mysql-engine-count-100m-cold' not in q.stdout.splitlines()
def deleted():
 q=run(['lsof','-nP','+L1'],expected=None);assert q.returncode in [0,1];rows=[]
 for line in q.stdout.splitlines():
  fields=line.split(None,9)
  if len(fields)==10 and fields[9].startswith(str(target)+'/'):rows.append({'pid':int(fields[1]),'fd':fields[3],'size':int(fields[6]),'path':fields[9]})
 # Do not persist unrelated processes/paths.
 commands[-1]['stdout']='[filtered exact nonce only; see descriptor lists]'
 return rows
before=deleted()
vm=['env','COLIMA_HOME='+str(socket.parent.parent),'colima','--profile',profile,'ssh','--','sh','-c']
# Revalidate already-deleted InnoDB paths before removing the still-existing MyISAM root.
if before:run(vm+['stat -- '+' '.join(shlex.quote(x) for x in sorted({r['path'] for r in before}))],expected=None)
if root_existed_before:
 (out/'load-duration-owner-cleanup-progress.json').write_text(json.dumps({'status':'validated_before_guest_unlink','target':str(target),'commands':commands},indent=2)+'\n')
 run(vm+['sudo -n -- rm -rf -- '+shlex.quote(str(target))])
 (out/'load-duration-owner-cleanup-progress.json').write_text(json.dumps({'status':'guest_unlink_exit0','target':str(target),'commands':commands},indent=2)+'\n')
assert not target.exists() and not target.is_symlink()
after=deleted()
if after:
 run(vm+['stat -- '+' '.join(shlex.quote(x) for x in sorted({r['path'] for r in after}))],expected=None);after=deleted()
space=run(['df','-Pk',str(root)]);free=int(space.stdout.splitlines()[-1].split()[3])*1024;assert free>=140*1024**3
archive=root/'results/reference'/('mysql-engine-count-100m-cold-duration-stopped-'+p.parent.name);assert not archive.exists()
shutil.copytree(p.parent,archive)
for f,h in d['source_before'].items():
 dest=archive/'source'/f;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(root/f,dest);assert sha(dest)==h
(archive/'manifest.json').write_text(json.dumps({'sourceRun':str(p.parent.relative_to(root)),'sourceHashes':d['source_before'],'files':{str(q.relative_to(archive)):sha(q) for q in sorted(archive.rglob('*')) if q.is_file()}},indent=2)+'\n')
receipt={'status':'interrupted_not_accepted_owned_cleanup_audited','rootExistedAtThisAudit':root_existed_before,'previousHelperAttempt':'Earlier helper reached post-unlink df assertion before asynchronous space reclamation was visible. Its actual exit was1; this audit independently rechecks terminal absence and capacity without repeating deletion when root is already absent.','checkedAt':datetime.now(timezone.utc).isoformat(),'rawResult':str(p.relative_to(root)),'rawResultSha256':sha(p),'canonicalExitCode':1,'rawCleanup':d['cleanup'],'sourceUnchanged':True,'allProcessesReaped':True,'allServersStoppedExitZero':True,'ownedRuntimeAbsent':True,'ownedRootAbsent':True,'deletedDescriptorsBefore':before,'deletedDescriptorsAfter':after,'hostFreeBytes':free,'archive':str(archive.relative_to(root)),'archiveManifestSha256':sha(archive/'manifest.json'),'commands':commands,'note':'Raw run is incomplete/exit1 with retained-stage cleanup decision. This separate owner cleanup is not experimental success.'}
(out/'load-duration-stop-audit.json').write_text(json.dumps(receipt,indent=2)+'\n')
decision.update(status='actual_terminal_exit1_owner_cleanup_audited',finishedAt=canonical['finishedAt'],stopAuditSha256=sha(out/'load-duration-stop-audit.json'));(out/'load-duration-interruption.json').write_text(json.dumps(decision,indent=2)+'\n')
print(json.dumps({'status':receipt['status'],'remainingOwnedDeletedDescriptors':len(after),'hostFreeGiB':free/1024**3,'archive':receipt['archive']}))
