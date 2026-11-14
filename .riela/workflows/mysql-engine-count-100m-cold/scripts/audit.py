"""Owner post-terminal evidence audit. Never starts Docker/DB or reruns measurements."""
from pathlib import Path
import json,hashlib,subprocess,datetime,statistics,shutil,os,shlex
root=Path(__file__).resolve().parents[4];out=root/'results/reviews/mysql-engine-count-100m-cold'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
canonical=json.loads((out/'canonical-invocation.json').read_text())
assert canonical['status']=='process_finished' and canonical['exitCode']==0,'canonical process must finish successfully before audit'
lines=(out/'canonical-invocation.log').read_text().splitlines();terminal=None
for line in reversed(lines):
 try:v=json.loads(line)
 except ValueError:continue
 if isinstance(v,dict) and 'result' in v and 'exit_status' in v:terminal=v;break
assert terminal and terminal['exit_status']==0
p=Path(terminal['result']);assert p.is_file() and p.parent.parent==root/'results/mysql-engine-count-100m-cold/runs'
d=json.loads(p.read_text());checks=[]
def check(name,ok,details=None):
 checks.append({'name':name,'passed':bool(ok),'details':details})
 if not ok:raise AssertionError(name)
check('actual canonical terminal',d['exit_status']==0 and d['status']=='completed_owner_audit_and_two_reviews_required')
check('cold-buffer policy proven',d['cold_buffer_policy']=='passed_all_12_fresh_process_boots_terminal_clean' and d['all_boots_terminal_clean'])
check('four-condition twelve-count completion',d['completion']=='passed_two_stages_four_conditions_twelve_counts' and d['completed_valid_counts_total']==12)
check('precision separate from service budget',d['precision']=='passed_exact_100000000_all_four_conditions' and d['service_budget']=='UNAVAILABLE_static_catalogue_no_service_workload')
check('runtime cleanup',d['cleanup']=='passed_owned_runtime_and_hostdata_absent')
check('source before after current',d['source_unchanged'] and d['source_before']==d['source_after'] and all(sha(root/f)==h for f,h in d['source_before'].items()),d['source_before'])
duration=json.loads((out/'load-duration-owner-application.json').read_text())
duration_author=json.loads((out/'load-duration-author-launch.json').read_text())
offline=json.loads((out/'owner-offline-final.json').read_text())
check('duration-only separate Sol author and owner application',duration_author['exitCode']==0 and duration_author['runtimeStatus']=='completed' and duration_author['model']=='gpt-6.1-sol' and duration['status']=='applied_offline67_passed_ready_for_blank_all' and canonical['loadDurationApplicationSha256']==sha(out/'load-duration-owner-application.json') and canonical['durationAuthorSession']==duration_author['sessionId'])
check('duration revision source-bound offline verification',offline['exitCode']==0 and offline['checks']==67 and offline['sourceHashes']==d['source_before'] and duration['offlineReceiptSha256']==sha(out/'owner-offline-final.json') and all(d['source_before'][x['path']]==x['afterSha256'] for x in duration['changes']))
b=json.loads((out/'baseline.json').read_text());check('original protected evidence unchanged',all(sha(root/f)==h for f,h in b['protectedFiles'].items()),{'fileCount':len(b['protectedFiles'])})
check('all child processes reaped',all(x.get('reaped') and isinstance(x.get('exit_status'),int) for x in d['processes']))
check('all mysql clients successful',d['all_mysql_clients_exit_zero'] and all(x['exit_status']==0 for x in d['processes'] if x['tag'].endswith('-mysql-client')))
check('no timed out subprocess',not any(x.get('timed_out') for x in d['commands']))
check('artifact hashes',all(sha(p.parent/f)==h for f,h in d['artifacts_sha256'].items()))
expected={'InnoDB':['innodb_primary_only','innodb_secondary_present','innodb_composite_secondary'],'MyISAM':['myisam_primary_only']}
check('exact engine order',[s['engine'] for s in d['stages']]==list(expected))
check('native ARM measured architecture',d['architecture']['host']=='arm64' and d['architecture']['daemon']=='aarch64' and d['architecture']['image']=='arm64' and d['architecture']['no_platform_override'])
check('pinned image identity',d['image']['Id']=='sha256:a81d043930e51745522bd5c430961cf8286f0a23f0a9a2a6e0108f7ea71b0902')
requests=[]
for q in p.parent.glob('*-client-*.requests.log'):
 for line in q.read_text().splitlines():
  item=json.loads(line)
  if 'sql' in item:requests.append(item['sql'])
check('exactly twelve plain measured count requests',sum(q=='SELECT COUNT(*) FROM lab.count_target;' for q in requests)==12)
check('exactly twelve targeted TABLE_ROWS requests',sum(q=="SELECT TABLE_ROWS FROM information_schema.TABLES WHERE TABLE_SCHEMA='lab' AND TABLE_NAME='count_target';" for q in requests)==12)
check('no cancelled count variants sent',not any('COUNT(1)' in q.upper() for q in requests))
summary=[]
for s in d['stages']:
 e=s['engine'];schema=[x.split('\t') for x in s['schema']];check(e+' exact 100 stored columns',len(schema)==100 and schema[0]==['id','bigint unsigned','NO','PRI'] and all(x[0]==f'c{i:03d}' and x[1:3]==['int','NO'] for i,x in enumerate(schema[1:],1)));bs=s['batches'];check(e+' commit receipts',len(bs)==1000 and all(x['batch']==i+1 and x['rows']==100000 and x['status']=='valid' and x['raw']==['100000'] and x['acknowledged_after_commit'] for i,x in enumerate(bs)))
 check(e+' loaded exactly 100M',s['accumulated_rows']==100000000)
 check(e+' condition order',[c['id'] for c in s['conditions']]==expected[e])
 check(e+' server and all clients exit0',s['container_exit_status']==0 and s['server_attach_exit_status']==0 and all(x['exit_status']==0 for x in s['client_exits']))
 check(e+' stage cleanup',s['cleanup']=='passed_container_reaped_and_host_stage_absent' and not s['cleanup_errors'])
 ids=[]
 boots=s['boots'];check(e+' exact boot count',len(boots)==(10 if e=='InnoDB' else 4))
 check(e+' every boot actual clean exit/reap',all(b['cold_globals_verified'] and b['attach']['reaped'] and b['attach']['exit_status']==0 and b['stop']['completion']=='clean_stopped_reaped' and b['stop']['command_exit_status']==0 and b['container_final_state']['ExitCode']==0 and b['container_final_state']['Running'] is False for b in boots))
 check(e+' one count per count boot',[b['counts_started'] for b in boots]==[0]+[1]*(len(boots)-1))
 check(e+' same container data and UUID across clean resets',len(set(b['container_id'] for b in boots))==1 and len(set(b['datadir'] for b in boots))==1 and len(set(b['server_uuid'] for b in boots))==1)
 check(e+' stage cold policy',s['cold_buffer_policy']=='passed_all_count_boots_terminal_clean')
 for c in s['conditions']:
  name=c['id'];samples=c['counts'];m=c['manual_reference'];ids.extend(tuple(x['count_session']['identity']) for x in c['counts'])
  check(name+' exact 3 valid counts',len(samples)==3 and [x['index'] for x in samples]==[1,2,3] and all(x['status']=='valid' and x['value']==100000000 and x['raw']==['100000000'] and x['full_request_elapsed_ns']>=x['caller_elapsed_ns']>0 for x in samples))
  check(name+' reference SQL raw timings',all(m[k]['sql'] and m[k]['raw'] and m[k]['elapsed_ns']>0 for k in ['inventory','explain']))
  check(name+' dedicated RC session',c['count_session']['fresh_dedicated'] and c['count_session']['isolation']=='READ-COMMITTED')
  expected_keys={'PRIMARY'}|({'k_count'} if name=='innodb_secondary_present' else {'k_count_v2'} if name=='innodb_composite_secondary' else set())
  check(name+' actual index catalogue',set(x.split('\t')[2] for x in c['indexes'])==expected_keys)
  if e=='InnoDB':check(name+' actual selected planned key',m['selected_index'] in expected_keys)
  for sample in samples:
   ref=sample['manual_reference'];meta=ref['table_rows'];reset=sample['reset'];boot=next(b for b in boots if b['ordinal']==sample['boot_ordinal'])
   check(name+' sample'+str(sample['index'])+' cold reset before count',reset['status']=='verified' and reset['previous_boot_terminal'] and boot['purpose']=='count' and sample['cold_buffer_policy']=='verified_fresh_process_preload_off_metadata_pages_disclosed' and sample['count_session']['identity'][0]==boot['ordinal'])
   check(name+' sample'+str(sample['index'])+' TABLE_ROWS value and timing',len(meta['raw'])==1 and meta['full_request_elapsed_ns']>=meta['caller_elapsed_ns']>0 and (meta['value'] is None and meta['raw']==['NULL'] and meta['signed_error_pct'] is None or isinstance(meta['value'],int) and meta['value']>=0 and meta['raw']==[str(meta['value'])] and meta['signed_error_pct']==100.0*(meta['value']-100000000)/100000000))
   check(name+' sample'+str(sample['index'])+' current own plan and session',ref['explain']['raw']==sample['plan'] and sample['count_session']['boot_ordinal']==boot['ordinal'] and sample['count_session']['server_uuid']==boot['server_uuid'])
  stats=m['index_page_stats'];check(name+' metadata uncertainty handled',stats['status']=='not_applicable_non_InnoDB' if e=='MyISAM' else (stats.get('size_pages',0)>0 and stats.get('leaf_pages',0)>0 if stats['status']=='available_reference_statistics' else stats.get('size_pages') is None))
  vals=[x['caller_elapsed_ns'] for x in samples];check(name+' descriptive summary',c['caller_ns_summary']=={'median':statistics.median(vals),'min':min(vals),'max':max(vals),'samples':3})
  summary.append({'condition':name,'selectedIndex':m['selected_index'],'callerNs':vals,'fullRequestNs':[x['full_request_elapsed_ns'] for x in samples],'summary':c['caller_ns_summary'],'pageStats':stats,'tableMetadata':c['table_bytes_metadata'],'countSession':c['count_session']})
 check(e+' distinct count connections',len(ids)==len(set(ids)))
 if e=='InnoDB':
  for k in ['index_build','composite_index_build']:check(k+' completed separate timing',s[k]['status']=='passed' and s[k]['elapsed_ns']>0)
  check('refinement assessed actual narrow shape',s['index_refinement_assessment']['decision']=='add_wider_composite_reference' and s['index_refinement_assessment']['row_data_changed_after_baseline'] is False)
check('host owner data absent',not Path(d['data_owner_root']).exists() and not Path(d['data_owner_root']).is_symlink())
endpoint=d['context']['endpoint'];assert endpoint.startswith('unix://')
env={'PATH':os.environ['PATH'],'HOME':str(Path.home()),'DOCKER_CONFIG':str(Path.home()/'.docker')}
cmd=['docker','--host',endpoint,'container','ls','-a','--format','{{.Names}}'];r=subprocess.run(cmd,capture_output=True,text=True,env=env)
check('independent terminal container inventory',r.returncode==0 and 'lab-mysql-engine-count-100m-cold' not in r.stdout.splitlines(),{'argv':cmd,'exitCode':r.returncode,'stdout':r.stdout,'stderr':r.stderr})
# Read-only guest metadata revalidation lets virtiofs forget already unlinked own files.
# It does not purge caches, restart the VM, touch other runs, or alter the measured source.
def own_deleted_descriptors():
 q=subprocess.run(['lsof','-nP','+L1'],capture_output=True,text=True,timeout=30)
 assert q.returncode==0,q.stderr
 rows=[]
 for line in q.stdout.splitlines():
  fields=line.split(None,9)
  if len(fields)==10 and fields[9].startswith(d['data_owner_root']+'/'):
   rows.append({'pid':int(fields[1]),'fd':fields[3],'bytes':int(fields[6]),'path':fields[9]})
 return rows
before=own_deleted_descriptors();guest=None
if before:
 socket=Path(endpoint.removeprefix('unix://'));profile=socket.parent.name
 assert d['context']['name']=='colima-'+profile and socket.parent.parent.name=='colima'
 paths=sorted(set(x['path'] for x in before));cmd=['env','COLIMA_HOME='+str(socket.parent.parent),'colima','--profile',profile,'ssh','--','sh','-c','stat -- '+' '.join(shlex.quote(x) for x in paths)]
 r=subprocess.run(cmd,capture_output=True,text=True,timeout=30)
 guest={'argv':cmd,'exitCode':r.returncode,'stdout':r.stdout,'stderr':r.stderr}
after=own_deleted_descriptors();space=subprocess.run(['df','-Pk',str(root)],capture_output=True,text=True,check=True)
free_bytes=int(space.stdout.splitlines()[-1].split()[3])*1024
import re
def known_system_cache(row):
 relative=Path(row['path']).relative_to(Path(d['data_owner_root']))
 parts=relative.parts
 if len(parts)<4 or parts[0] not in ['InnoDB','MyISAM'] or parts[1:3]!=('stage','data'):return False
 tail=parts[3:]
 return (len(tail)==1 and tail[0] in ['auto.cnf','ibdata1','undo_001','undo_002','#ib_16384_0.dblwr','#ib_16384_1.dblwr']) or (len(tail)==2 and tail[0]=='#innodb_redo' and re.fullmatch(r'#ib_redo[0-9]+(?:_tmp)?',tail[1]) is not None)
process_proofs=[]
for pid in sorted({x['pid'] for x in after}):
 q=subprocess.run(['ps','-p',str(pid),'-o','pid=,comm='],capture_output=True,text=True,check=True)
 process_proofs.append({'pid':pid,'stdout':q.stdout,'exitCode':q.returncode})
cache_bytes=sum(x['bytes'] for x in after)
reclaim={'scope':'Exact current nonce only. Logical owned runtime/path deletion is distinct from physical OS/VM file-cache reclamation. Prior interrupted references remain separately classified.','userCacheChoice':'DBバッファを毎回リセットし、OS・Mac側の残存は明記する','before':before,'guestRevalidation':guest,'after':after,'hostDf':space.stdout,'hostFreeBytes':free_bytes,'remainingBytes':cache_bytes,'processProofs':process_proofs,'physicalCacheStatus':'released_observed' if not after else 'readonly_shared_VZ_known_engine_cache_retained_not_purged','logicalCleanup':'owned_runtime_and_paths_absent','globalCachePurgeOrVMRestart':False}
(out/'host-deleted-descriptor-cleanup.json').write_text(json.dumps(reclaim,indent=2)+'\n')
check('no target table payload retained in deleted descriptors',not any(Path(x['path']).suffix.lower() in ['.ibd','.myd','.myi'] for x in after))
check('residual file cache explicitly scoped not physical cleanup success',all(x['fd'].endswith('r') and known_system_cache(x) for x in after) and all('com.apple.Virtualization.VirtualMachine.xpc/Contents/MacOS/com.apple.Virtualization.VirtualMachine' in x['stdout'] for x in process_proofs) and cache_bytes<=2*1024**3,{'artifact':'results/reviews/mysql-engine-count-100m-cold/host-deleted-descriptor-cleanup.json','beforeCount':len(before),'afterCount':len(after),'remainingBytes':cache_bytes,'physicalCacheStatus':reclaim['physicalCacheStatus'],'meaning':'2GiB diagnostic ceiling covers the configured1GiB redo plus system/retired buffers; unknown files, writable handles or payload remain failures. Retained shared cache is disclosed, never labeled purged.'})
check('host capacity available for separate SQL stage',free_bytes>=70*1024**3,{'hostFreeBytes':free_bytes})
# Regenerate disclosure from the just-recorded actual cache observation before binding documents.
report=subprocess.run(['python3',str(Path(__file__).with_name('report.py'))],cwd=root,capture_output=True,text=True)
check('final report includes actual logical and physical cleanup classification',report.returncode==0,{'stdout':report.stdout,'stderr':report.stderr})
archive=root/'results/reference'/('mysql-engine-count-100m-cold-'+p.parent.name)
assert not archive.exists(),'archive already exists; inspect rather than overwrite'
shutil.copytree(p.parent,archive)
manifest={str(q.relative_to(archive)):sha(q) for q in sorted(archive.rglob('*')) if q.is_file()}
(archive/'manifest.json').write_text(json.dumps({'sourceRun':str(p.parent.relative_to(root)),'files':manifest},indent=2)+'\n')
historical_manifests={}
for directory in ['mysql-engine-count-100m-cold-capacity-stopped-20261108T162849Z-0e82949c4ce394e3baab969403f8b6c1','mysql-engine-count-100m-cold-duration-stopped-20261108T172604Z-ca74a6381f9f93ff0ed6ed7c87471500']:
 history=root/'results/reference'/directory
 historical=json.loads((history/'manifest.json').read_text())
 check(directory+' historical reference hashes',all(sha(history/f)==h for f,h in historical['files'].items()))
 historical_manifests[str((history/'manifest.json').relative_to(root))]=sha(history/'manifest.json')
audit={'status':'passed','checkedAt':datetime.datetime.now(datetime.timezone.utc).isoformat(),'canonical':str((out/'canonical-invocation.json').relative_to(root)),'run':str(p.relative_to(root)),'runSha256':sha(p),'archive':str(archive.relative_to(root)),'archiveManifestSha256':sha(archive/'manifest.json'),'checks':checks,'summary':summary,'ratios':d['caller_median_ratios'],'notes':['No production workload: service budget unavailable.','Static MyISAM not a nonlocking service candidate.','Cold-buffer author56, owner TABLE_ROWS/readiness extension and separate Sol59 duration-only author; prior interrupted runs remain separate incomplete references.']}
(out/'owner-audit.json').write_text(json.dumps(audit,indent=2)+'\n')
proof={'status':'owner_audited_final_reviews_pending','sourceHashes':d['source_before'],'documentHashes':{name:sha(root/name) for name in ['docs/mysql-engine-count-100m.md','docs/guidelines/mysql-innodb.md','docs/guidelines/mysql-myisam.md']},'ownerAuditSha256':sha(out/'owner-audit.json'),'canonicalInvocationSha256':sha(out/'canonical-invocation.json'),'rawRun':str(p.relative_to(root)),'rawRunSha256':sha(p),'archiveManifest':str((archive/'manifest.json').relative_to(root)),'archiveManifestSha256':sha(archive/'manifest.json'),'latestTableRowsRequestSha256':sha(out/'latest-table-rows-request.json'),'hostSpecSha256':sha(out/'host-spec.json'),'hostDeletedDescriptorCleanupSha256':sha(out/'host-deleted-descriptor-cleanup.json'),'actualConditions':list(x['condition'] for x in summary),'countsTotal':12,'loadDurationAuthorLaunchSha256':sha(out/'load-duration-author-launch.json'),'loadDurationApplicationSha256':sha(out/'load-duration-owner-application.json'),'loadDurationStopAuditSha256':sha(out/'load-duration-stop-audit.json')}
proof['historicalManifestHashes']=historical_manifests
proof['cacheScopeCorrectionManifestSha256']=sha(out/'before-cache-scope-correction/manifest.json')
proof['physicalCacheStatus']=reclaim['physicalCacheStatus']
(out/'owner-proof.json').write_text(json.dumps(proof,indent=2)+'\n');print(json.dumps({'status':audit['status'],'checks':len(checks),'archive':audit['archive']}))
