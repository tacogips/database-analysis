"""Owner post-terminal evidence audit. Never starts Docker/DB or reruns measurements."""
from pathlib import Path
import json,hashlib,subprocess,datetime,statistics,shutil,os
root=Path(__file__).resolve().parents[4];out=root/'results/reviews/mysql-engine-count-100m'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
canonical=json.loads((out/'canonical-invocation.json').read_text())
assert canonical['status']=='process_finished' and canonical['exitCode']==0,'canonical process must finish successfully before audit'
lines=(out/'canonical-invocation.log').read_text().splitlines();terminal=None
for line in reversed(lines):
 try:v=json.loads(line)
 except ValueError:continue
 if isinstance(v,dict) and 'result' in v and 'exit_status' in v:terminal=v;break
assert terminal and terminal['exit_status']==0
p=Path(terminal['result']);assert p.is_file() and p.parent.parent==root/'results/mysql-engine-count-100m/runs'
d=json.loads(p.read_text());checks=[]
def check(name,ok,details=None):
 checks.append({'name':name,'passed':bool(ok),'details':details})
 if not ok:raise AssertionError(name)
check('actual canonical terminal',d['exit_status']==0 and d['status']=='completed_owner_audit_and_two_reviews_required')
check('four-condition twelve-count completion',d['completion']=='passed_two_stages_four_conditions_twelve_counts' and d['completed_valid_counts_total']==12)
check('precision separate from service budget',d['precision']=='passed_exact_100000000_all_four_conditions' and d['service_budget']=='UNAVAILABLE_static_catalogue_no_service_workload')
check('runtime cleanup',d['cleanup']=='passed_owned_runtime_and_hostdata_absent')
check('source before after current',d['source_unchanged'] and d['source_before']==d['source_after'] and all(sha(root/f)==h for f,h in d['source_before'].items()),d['source_before'])
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
check('no cancelled count variants sent',not any('COUNT(1)' in q.upper() for q in requests))
summary=[]
for s in d['stages']:
 e=s['engine'];schema=[x.split('\t') for x in s['schema']];check(e+' exact 100 stored columns',len(schema)==100 and schema[0]==['id','bigint unsigned','NO','PRI'] and all(x[0]==f'c{i:03d}' and x[1:3]==['int','NO'] for i,x in enumerate(schema[1:],1)));bs=s['batches'];check(e+' commit receipts',len(bs)==1000 and all(x['batch']==i+1 and x['rows']==100000 and x['status']=='valid' and x['raw']==['100000'] and x['acknowledged_after_commit'] for i,x in enumerate(bs)))
 check(e+' loaded exactly 100M',s['accumulated_rows']==100000000)
 check(e+' condition order',[c['id'] for c in s['conditions']]==expected[e])
 check(e+' server and all clients exit0',s['container_exit_status']==0 and s['server_attach_exit_status']==0 and all(x['exit_status']==0 for x in s['client_exits']))
 check(e+' stage cleanup',s['cleanup']=='passed_container_reaped_and_host_stage_absent' and not s['cleanup_errors'])
 ids=[]
 for c in s['conditions']:
  name=c['id'];samples=c['counts'];m=c['manual_reference'];ids.append(c['count_session']['connection_id'])
  check(name+' exact 3 valid counts',len(samples)==3 and [x['index'] for x in samples]==[1,2,3] and all(x['status']=='valid' and x['value']==100000000 and x['raw']==['100000000'] and x['full_request_elapsed_ns']>=x['caller_elapsed_ns']>0 for x in samples))
  check(name+' reference SQL raw timings',all(m[k]['sql'] and m[k]['raw'] and m[k]['elapsed_ns']>0 for k in ['inventory','explain']))
  check(name+' dedicated RC session',c['count_session']['fresh_dedicated'] and c['count_session']['isolation']=='READ-COMMITTED')
  expected_keys={'PRIMARY'}|({'k_count'} if name=='innodb_secondary_present' else {'k_count_v2'} if name=='innodb_composite_secondary' else set())
  check(name+' actual index catalogue',set(x.split('\t')[2] for x in c['indexes'])==expected_keys)
  if e=='InnoDB':check(name+' actual selected planned key',m['selected_index'] in expected_keys)
  stats=m['index_page_stats'];check(name+' metadata uncertainty handled',stats['status']=='not_applicable_non_InnoDB' if e=='MyISAM' else (stats.get('size_pages',0)>0 and stats.get('leaf_pages',0)>0 if stats['status']=='available_reference_statistics' else stats.get('size_pages') is None))
  vals=[x['caller_elapsed_ns'] for x in samples];check(name+' descriptive summary',c['caller_ns_summary']=={'median':statistics.median(vals),'min':min(vals),'max':max(vals),'samples':3})
  summary.append({'condition':name,'selectedIndex':m['selected_index'],'callerNs':vals,'fullRequestNs':[x['full_request_elapsed_ns'] for x in samples],'summary':c['caller_ns_summary'],'pageStats':stats,'tableMetadata':c['table_bytes_metadata'],'countSession':c['count_session']})
 check(e+' distinct count connections',len(ids)==len(set(ids)) and all(str(x)!=s['connection_id'][0] for x in ids))
 if e=='InnoDB':
  for k in ['index_build','composite_index_build']:check(k+' completed separate timing',s[k]['status']=='passed' and s[k]['elapsed_ns']>0)
  check('refinement assessed actual narrow shape',s['index_refinement_assessment']['decision']=='add_wider_composite_reference' and s['index_refinement_assessment']['row_data_changed_after_baseline'] is False)
check('host owner data absent',not Path(d['data_owner_root']).exists() and not Path(d['data_owner_root']).is_symlink())
endpoint=d['context']['endpoint'];assert endpoint.startswith('unix://')
env={'PATH':os.environ['PATH'],'HOME':str(Path.home()),'DOCKER_CONFIG':str(Path.home()/'.docker')}
cmd=['docker','--host',endpoint,'container','ls','-a','--format','{{.Names}}'];r=subprocess.run(cmd,capture_output=True,text=True,env=env)
check('independent terminal container inventory',r.returncode==0 and 'lab-mysql-engine-count-100m' not in r.stdout.splitlines(),{'argv':cmd,'exitCode':r.returncode,'stdout':r.stdout,'stderr':r.stderr})
archive=root/'results/reference'/('mysql-engine-count-100m-'+p.parent.name)
assert not archive.exists(),'archive already exists; inspect rather than overwrite'
shutil.copytree(p.parent,archive)
manifest={str(q.relative_to(archive)):sha(q) for q in sorted(archive.rglob('*')) if q.is_file()}
(archive/'manifest.json').write_text(json.dumps({'sourceRun':str(p.parent.relative_to(root)),'files':manifest},indent=2)+'\n')
audit={'status':'passed','checkedAt':datetime.datetime.now(datetime.timezone.utc).isoformat(),'canonical':str((out/'canonical-invocation.json').relative_to(root)),'run':str(p.relative_to(root)),'runSha256':sha(p),'archive':str(archive.relative_to(root)),'archiveManifestSha256':sha(archive/'manifest.json'),'checks':checks,'summary':summary,'ratios':d['caller_median_ratios'],'notes':['No production workload: service budget unavailable.','Static MyISAM not a nonlocking service candidate.','Four conditions supersede three-condition author plan via recorded owner steering.']}
(out/'owner-audit.json').write_text(json.dumps(audit,indent=2)+'\n')
proof={'status':'owner_audited_final_reviews_pending','sourceHashes':d['source_before'],'documentHashes':{'docs/mysql-engine-count-100m.md':sha(root/'docs/mysql-engine-count-100m.md')},'ownerAuditSha256':sha(out/'owner-audit.json'),'canonicalInvocationSha256':sha(out/'canonical-invocation.json'),'rawRun':str(p.relative_to(root)),'rawRunSha256':sha(p),'archiveManifest':str((archive/'manifest.json').relative_to(root)),'archiveManifestSha256':sha(archive/'manifest.json'),'finalRequestSha256':sha(out/'final-request.json'),'steeringAddendumSha256':sha(out/'steering-addendum.json'),'hostSpecSha256':sha(out/'host-spec.json'),'actualConditions':list(x['condition'] for x in summary),'countsTotal':12}
(out/'owner-proof.json').write_text(json.dumps(proof,indent=2)+'\n');print(json.dumps({'status':audit['status'],'checks':len(checks),'archive':audit['archive']}))
