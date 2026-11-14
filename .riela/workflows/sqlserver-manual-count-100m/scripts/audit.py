"""Independent post-terminal owner audit; no DB startup or data mutations."""
from pathlib import Path
from datetime import datetime,timezone
import json,hashlib,subprocess,os,shutil,xml.etree.ElementTree as ET
root=Path(__file__).resolve().parents[4];out=root/'results/reviews/sqlserver-manual-count-100m'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest();read=lambda p:json.loads(p.read_text())
canonical=read(out/'canonical-invocation.json');assert canonical['status']=='process_finished' and canonical['exitCode']==1
terminal=None
for line in reversed((out/'canonical-invocation.log').read_text().splitlines()):
 try:value=json.loads(line)
 except ValueError:continue
 if isinstance(value,dict) and 'result' in value and 'exit_status' in value:terminal=value;break
assert terminal and terminal['exit_status']==1
path=Path(terminal['result']);assert path.is_file() and path.parent.parent==root/'results/sqlserver/manual-count-100m/runs'
d=read(path);probe=d['probe'];checks=[]
def check(name,passed,detail=None):
 checks.append({'name':name,'passed':bool(passed),'detail':detail})
 if not passed:raise AssertionError(name)
post=read(out/'owner-post-terminal-capacity.json')
check('raw controller1 preserved; only immediate capacity observation failed',d['exit_status']==1 and d['status']=='incomplete' and d['completion']=='passed_18_commands_owner_audit_pending' and d.get('error') is None and d['cleanup_errors']==['RuntimeError: host capacity not restored within 1 GiB (shared-host observation)'])
check('independent later same capacity criterion bound to exact terminal run',post['status']=='passed_post_terminal_owned_cleanup_and_capacity' and post['rawRunSha256']==sha(path) and post['canonicalInvocationSha256']==sha(out/'canonical-invocation.json') and post['controllerExitCode']==1 and post['capacityRestoredWithin1GiBAtAdditionalCheck'] and post['hostFreeObservedBytes']>=d['free_before_bytes']-1024**3 and all(c['exitCode']==0 and (not c['stdout'].strip() if 'docker' in c['argv'][0] else True) for c in post['commands']))
check('precision and release separately',probe['completion']=='passed_all_18_commands' and probe['transaction_release']=='passed_each_sample_and_disposed_condition' and d['service_budget']=='UNAVAILABLE_quiet_static_lab')
check('source before after current',d['source_unchanged'] and d['source_before']==d['source_after'] and all(sha(root/f)==h for f,h in d['source_before'].items()),d['source_before'])
smoke=probe['preload_monitor_smoke']
check('real monitor smoke before load',smoke['status']=='passed' and smoke['accepted'] is True and smoke['errors']==[] and all(smoke[k] is True for k in ['before_load','open_session_release_verified','session_disposed_release_verified','admin_disposed_release_verified']))
author_files=['author-launch.json','runtime-repair-author-launch.json','entrypoint-repair-author-launch.json','engine-repair-author-launch.json']
authors=[read(out/f) for f in author_files]
check('four separate Sol author sessions completed',len({a['sessionId'] for a in authors})==4 and all(a['model']=='gpt-6.1-sol' and a['exitCode']==0 and a['runtimeStatus']=='completed' for a in authors))
application=read(out/'engine-repair-owner-application.json')
check('actual run binds final owner application and offline19',canonical['repairApplicationSha256']==sha(out/'engine-repair-owner-application.json') and canonical['offlineReceiptSha256']==sha(out/'engine-repair-offline-final.json') and application['offlineReceiptSha256']==canonical['offlineReceiptSha256'])
check('actual owner executor binding',canonical['ownerExecuteSha256']==application['ownerExecuteSha256']==sha(Path(__file__).with_name('execute.py')))
check('final application source matches actual run',all(d['source_before'][f]==h for f,h in application['finalSourceHashes'].items()))
for filename in ['monitor-syntax-failure-owner-cleanup.json','startup-proof-failure-owner-cleanup.json','ag-restore-failure-owner-cleanup.json']:
 previous=read(out/filename)
 check(filename+' previous failure preserved and removed',previous['rawRunExitCode']==1 and previous['ownedRootAbsent'] and previous['ownedRuntimeAbsent'] and not Path(read(root/previous['rawRun'])['stage']['path']).parent.exists())
b=read(out/'baseline.json');check('old protected sources/evidence unchanged',all(sha(root/f)==h for f,h in b['protectedFiles'].items()),len(b['protectedFiles']))
check('runtime artifact hashes',all(sha(path.parent/f)==h for f,h in d['artifact_hashes'].items()))
check('owned resources and data cleaned with separate later capacity confirmation',d['cleanup']=='failed_or_unknown_preserved_evidence' and d['stage_absent'] and not Path(d['stage']['path']).parent.exists() and not d['capacity_restored_within_1GiB'] and post['ownedRuntimeAbsent'] and post['ownedRootAbsent'])
check('guest side guarded unlink',d['stage_removed_through_guest'] is True)
check('no command timeout or forced termination',not any(c.get('timed_out') or c.get('forced_process_kill') or c.get('terminated_by_controller') for c in d['commands']))
check('client actual exit0',d['client_exit_status']==0 and d['client_final_inspect']['State']['Running'] is False)
for name in ['lab-sqlserver-manual100m','lab-sqlserver-manual100m-secondary']:
 state=d[name+'_terminal_state'];check(name+' actual stopped exit0',not state['Running'] and state['ExitCode']==0 and not state['OOMKilled'])
 proof=d['actual_sqlserver_rosetta'][name]
 check(name+' actual Rosetta exe',proof['container_id']==d['owned_ids']['container:'+name] and proof['pid_executables'] and all('rosetta' in v and 'qemu' not in v.lower() for v in proof['pid_executables'].values()))
check('final Rosetta proofs after real SQL',d['final_sqlserver_rosetta_verified_after_18_counts'] and 'interpreter /mnt/lima-rosetta/rosetta' in d['rosetta_registration'])
check('actual architecture SQL amd64 SDK arm64',d['sql_image'][0]['Architecture']=='amd64' and d['sdk_image'][0]['Architecture']=='arm64')
receipts=probe['commits'];check('exact1000 postCOMMIT receipts',len(receipts)==1000)
for i,receipt in enumerate(receipts):
 raw=receipt['raw'][0][0]
 check('receipt '+str(i),receipt['batch']==i and receipt['committed_total']==(i+1)*100000 and raw['committed_rows']==100000 and raw['first_id']==i*100000+1 and raw['last_id']==(i+1)*100000 and raw['transaction_count']==0)
cases=['B-clustered','B-narrow','B-composite','C-composite','D-composite','A-composite'];conditions=probe['conditions'];samples=probe['samples']
check('six expected cases', [c['condition'] for c in conditions]==cases)
check('all18 slots retained',len(samples)==18)
raws={p.name:read(p) for p in path.parent.glob('raw/*.json')}
def raw_for(suffix):
 found=[(name,value) for name,value in raws.items() if name.endswith('-'+suffix+'.json')]
 assert len(found)==1,(suffix,len(found))
 return found[0]
_,primary_settings=raw_for('actual-server-settings')
identities=[v['raw'][0][0] for k,v in raws.items() if k.endswith('-ag-server-identity.json')]
primary_version=primary_settings['raw'][0][0]['product_version']
check('both actual SQL2022 runtime versions match across all identity observations',len(identities)>=2 and {r['server_name'] for r in identities}=={'countprimary','countsecondary'} and primary_version.startswith('16.') and all(r['product_version']==primary_version and r['is_hadr_enabled']==1 for r in identities),{'primary':primary_version,'identityObservations':len(identities)})
summary=[]
for case in cases:
 condition=next(c for c in conditions if c['condition']==case);route=case[0];isolation=5 if route=='C' else 2
 found=[s for s in samples if s['condition']==case];state=condition['actual_state']
 check(case+' 3ordered samples',[s['sample'] for s in found]==[1,2,3])
 if route!='A':check(case+' actual nonAG state',state['is_ag_primary'] is None and state['role_desc'] is None and state['is_read_committed_snapshot_on']==(1 if route=='B' else 0) and state['snapshot_isolation_state']==(1 if route=='C' else 0))
 else:check(case+' actual readable synced secondary',state['is_ag_primary']==0 and state['role_desc']=='SECONDARY' and state['secondary_role_allow_connections_desc'] in ['ALL','READ_ONLY'] and state['redo_queue_size']==0 and state['synchronization_state_desc']=='SYNCHRONIZED' and state['synchronization_health_desc']=='HEALTHY')
 for sample in found:
  check(case+' sample'+str(sample['sample'])+' exact outcome',sample['accepted'] and sample['completed'] and not sample['completion_unknown'] and sample['error'] is None and sample['value']==100000000 and sample['full_completion_ns']>=sample['first_result_ns']>0 and sample['id']['Isolation']==isolation and sample['sql']==condition['selected_sql'])
  cold=sample['cold_buffer_proof'];check(case+' sample'+str(sample['sample'])+' empty target data buffers',sample['cold_buffer_reset_verified'] and cold['verified'] and cold['cached_target_pages']==0 and cold['dirty_target_pages']==0)
  check(case+' sample'+str(sample['sample'])+' result meaning',sample['precision']==('observed_rows_quiet_match_only' if route=='D' else 'exact_rows_static_fixture'))
 check(case+' actual SQL D hint only',('WITH (NOLOCK)' in condition['selected_sql'])==(route=='D'))
 _,columns=raw_for(case+'-stored-columns');rows=columns['raw'][0]
 check(case+' 100actual stored columns',len(rows)==100 and rows[0]['name']=='id' and rows[0]['system_type_id']==127 and rows[0]['max_length']==8 and all(not r['is_nullable'] and not r['is_computed'] for r in rows) and all(r['name']==f'c{i:03d}' and r['system_type_id']==56 and r['max_length']==4 for i,r in enumerate(rows[1:],1)))
 _,catalog=raw_for(case+'-index-catalog');names={r['index_name'] for r in catalog['raw'][0]};expected={'PK_Target'}|({'k_count'} if case=='B-narrow' else {'k_count_v2'} if case.endswith('composite') else set())
 check(case+' actual index structure',names==expected)
 check(case+' selected plan index',condition['planned_scans'] and all(scan['index'].strip('[]') in expected for scan in condition['planned_scans'] if scan['table']=='[Target]'))
 plan=ET.parse(path.parent/(case+'-estimated.sqlplan'));check(case+' estimated not actual plan',not any(node.tag.endswith('RunTimeCountersPerThread') for node in plan.iter()))
 _,inventory=raw_for(case+'-inventory');check(case+' metadata SQL value and latency',inventory['elapsed_ns']>0 and inventory['raw'] and inventory['error'] is None)
 pages=condition.get('pages')
 check(case+' pages known or explicitly unknown',pages is not None and pages['used_bytes']>0 and pages['reserved_bytes']>=pages['used_bytes'] or pages is None and bool(condition.get('unavailable')))
 summary.append({'case':case,'route':route,'state':state,'scalarNs':[s['first_result_ns'] for s in found],'fullNs':[s['full_completion_ns'] for s in found],'selectedSQL':condition['selected_sql'],'pages':pages})
check('unchanged composite across4states',len({c['structure_sha256'] for c in conditions if c['index']=='composite'})==1)
check('actual manual restore AG setup',probe['ag_setup']=='actual_secondary_readable_manual_full_log_restore_same_committed_fixture' and probe['secondary_gate']==d['secondary_gate'])
# Independently replay the source-bound acceptance model, in addition to the direct checks above.
model=subprocess.run(['mise','-C','sqlserver/manual-count-100m','exec','--','ruby','-r','./model','-r','json','-e','abort "incomplete" unless ManualModel.complete?(JSON.parse(File.read(ARGV.fetch(0))))',str(path.parent/'probe.json')],cwd=root,capture_output=True,text=True)
check('source model replay',model.returncode==0,{'stdout':model.stdout,'stderr':model.stderr})
endpoint=d['context']['endpoint'];assert endpoint.startswith('unix://');env={'PATH':os.environ['PATH'],'HOME':str(Path.home()),'DOCKER_CONFIG':str(Path.home()/'.docker')}
for kind,args in [('container',['ls','-a']),('network',['ls']),('volume',['ls'])]:
 cmd=['docker','--host',endpoint,kind]+args+['--filter','label=count.manual100m.owner='+d['owner'],'--format','{{json .}}'];r=subprocess.run(cmd,capture_output=True,text=True,env=env)
 check('independent '+kind+' absence',r.returncode==0 and not r.stdout.strip(),{'argv':cmd,'exitCode':r.returncode,'stdout':r.stdout,'stderr':r.stderr})
archive=root/'results/reference'/('sqlserver-manual-count-100m-'+path.parent.name)
if archive.exists():
 assert not archive.is_symlink()
 existing=read(archive/'manifest.json');assert existing['sourceRun']==str(path.parent.relative_to(root))
 assert set(existing['files'])=={str(p.relative_to(path.parent)) for p in path.parent.rglob('*') if p.is_file()}
 assert all(sha(archive/f)==h and sha(path.parent/f)==h for f,h in existing['files'].items())
else:
 shutil.copytree(path.parent,archive);manifest={str(p.relative_to(archive)):sha(p) for p in sorted(archive.rglob('*')) if p.is_file()};(archive/'manifest.json').write_text(json.dumps({'sourceRun':str(path.parent.relative_to(root)),'files':manifest},indent=2)+'\n')
audit={'status':'passed','checkedAt':datetime.now(timezone.utc).isoformat(),'run':str(path.relative_to(root)),'runSha256':sha(path),'checks':checks,'summary':summary,'archive':str(archive.relative_to(root)),'archiveManifestSha256':sha(archive/'manifest.json')};(out/'owner-audit.json').write_text(json.dumps(audit,indent=2)+'\n')
proof={'status':'owner_audited_final_reviews_pending','sourceHashes':d['source_before'],'documentHashes':{p:sha(root/p) for p in ['docs/sqlserver-manual-count-100m.md','docs/guidelines/sql-server.md']},'ownerAuditSha256':sha(out/'owner-audit.json'),'canonicalInvocationSha256':sha(out/'canonical-invocation.json'),'rawRun':str(path.relative_to(root)),'rawRunSha256':sha(path),'archiveManifest':str((archive/'manifest.json').relative_to(root)),'archiveManifestSha256':sha(archive/'manifest.json'),'hostSpecSha256':sha(out/'host-spec.json'),'latestStateRequestSha256':sha(out/'latest-state-request.json'),'countsTotal':18};proof['controllerExitCode']=1;proof['postTerminalCapacitySha256']=sha(out/'owner-post-terminal-capacity.json');proof['authorLaunchHashes']={f:sha(out/f) for f in author_files};proof['repairApplicationSha256']=sha(out/'engine-repair-owner-application.json');(out/'owner-proof.json').write_text(json.dumps(proof,indent=2)+'\n');print(json.dumps({'status':'passed','checks':len(checks),'archive':audit['archive']}))
