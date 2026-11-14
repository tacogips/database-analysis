"""Owner post-run audit; does not execute SQL or launch containers."""
from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,shutil,subprocess
root=Path(__file__).resolve().parents[4]
out=root/'results/reviews/issue-1'; run=root/'results/issue-1/runs/20261108T013522-6bd01cec80ff'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
r=json.loads((run/'result.json').read_text());p=json.loads((run/'probe.json').read_text());b=json.loads((out/'baseline.json').read_text());checks=[]
def check(name,ok,detail=None):
 checks.append(dict(name=name,passed=bool(ok),detail=detail));assert ok,name
check('actual_foreground_exit',json.loads((out/'canonical-invocation.json').read_text())['exitCode']==r['exit_status']==r['client_exit_status']==p['exit_status']==0)
check('all_commands_finished',all(c['exit_status']==0 and not c['timed_out'] for c in r['commands']))
check('final_source_and_old_evidence',r['source_before']==r['source_after'] and all(sha(root/f)==h for f,h in {**b['protectedFiles'],**r['source_after']}.items()),{'protected_files':len(b['protectedFiles']),'source_files':len(r['source_after'])})
check('artifact_bytes',all(sha(run/f)==h for f,h in r['artifacts'].items()) and sha(run/'controller.log')==r['raw_log_sha256'])
e=p['events']; events=lambda n:[v['detail'] for v in e if v['name']==n]
cases=['server_error','client_cancel','command_timeout','dispose_connection','management_fallback']
check('five_cases',events('case_passed')==cases)
check('two_actual_connections',len(events('two_connection_identity_equal'))==5 and all(x['client_id_not_assumed_equal_to_server_id'] and x['server_identity']['Tag'] for x in events('two_connection_identity_equal')))
wrong=events('wrong_identity_refused_owner_usable')
check('three_wrong_identities_refused',len(wrong)==3 and all(not x['kill_sent'] and x['refused']=='refused_identity_mismatch_or_absent' for x in wrong) and all(sum(x[k] for x in wrong)==1 for k in ['wrong_tag','wrong_time','wrong_connection_id']))
check('restricted_visibility_unknown',len(events('restricted_observer'))==1 and events('restricted_observer')[0]['status'].startswith('monitor_unavailable'))
post=events('post_fault_before_finally')
check('attention_not_transaction_end',len(post)==3 and {x['case_name'] for x in post}==set(cases[:3]) and all(not x['state']['Requests'] and x['state']['Transactions'] and x['state']['Snapshots'] and x['state']['ActiveTransactions'] and x['state']['Sessions'][0]['open_transaction_count']==1 for x in post))
check('actual_finally_paths',len(events('original_rollback'))==3 and 'kill_sent' in events('management_cleanup') and len(events('communication_failure_simulation'))==1)
segments=[];segment=[]
for v in e:
 segment.append(v)
 if v['name']=='case_passed':segments.append(segment);segment=[]
for seg,name in zip(segments,cases):
 polls=[x['detail'] for x in seg if x['name']=='release_poll'];last=polls[-1]
 check('release_'+name,all(not last[k] for k in ['Sessions','Requests','Transactions','Snapshots','ActiveTransactions']))
check('retention_not_physical_recovery',len(events('retention_release'))==5 and all('unverified' in x['production_recovery'] for x in events('retention_release')) and p['service_budget']=='unavailable_functional_only')
plans=events('stats_count_plan');before=events('stats_before_count');after=events('stats_after_count')
check('stats_three_profiles', [x['profile'] for x in plans]==['off','sync_on','async_on'] and all(x['count']==4096 and x['plan_status']=='actual_plan_captured' and not x['optimizer_stats_usage'] for x in plans))
for x,y in zip(before,after):
 s=x['settings'][0];profile=x['profile'];expected={'off':(False,False,False),'sync_on':(True,True,False),'async_on':(True,True,True)}[profile]
 check('stats_'+profile,tuple(s[k] for k in ['is_auto_create_stats_on','is_auto_update_stats_on','is_auto_update_stats_async_on'])==expected and x['stats']==y['after'] and len(y['recovery_windows'])==3 and 'not observed' in y['conclusion'])
check('source_lock_matches_runtime',sha(root/'sqlserver/issue1/packages.lock.json')==sha(run/'packages.lock.json'))
# Recheck the exact same local Unix daemon after the owned cleanup, without accepting inspect errors as absence.
cli=['docker','--host',r['context']['endpoint']];check('unix_daemon',r['context']['endpoint'].startswith('unix:///'))
inventories={}
for kind,names in {'container':['lab-sqlserver-issue1','lab-sqlserver-issue1-client'],'volume':['lab-sqlserver-issue1-data'],'network':['lab-sqlserver-issue1-network']}.items():
 args=cli+[kind,'ls']+(['-a'] if kind=='container' else [])+['--format','{{.Names}}' if kind=='container' else '{{.Name}}']
 q=subprocess.run(args,capture_output=True,text=True);inventories[kind]={'argv':args,'exit_status':q.returncode,'stdout':q.stdout,'stderr':q.stderr}
 check('owned_'+kind+'_absent',q.returncode==0 and not set(names)&set(q.stdout.splitlines()))
check('controller_cleanup',r['cleanup']=='passed_owned_runtime_resources_absent' and not r['cleanup_errors'])
archive=root/'results/reference/issue-1-runtime-20261108T013522-6bd01cec80ff';archive.mkdir(exist_ok=True)
for f in run.iterdir():
 if f.is_file():
  target=archive/f.name
  if target.exists():check('immutable_archive_'+f.name,sha(f)==sha(target))
  else:shutil.copyfile(f,target)
manifest={'classification':'new_issue1_functional_run_not_old_performance_benchmark','original_run':str(run.relative_to(root)),'files':{f.name:sha(f) for f in archive.iterdir() if f.is_file() and f.name!='manifest.json'}}
write(archive/'manifest.json',manifest)
report={'status':'passed','checkedAt':datetime.now(timezone.utc).isoformat(),'checks':checks,'cleanupInventory':inventories,'archiveManifest':str((archive/'manifest.json').relative_to(root))}
write(out/'owner-audit.json',report)
proof={'status':'owner_verified','command':'mise -C sqlserver/issue1 run all','actualExitCode':0,'runtimeArchive':str(archive.relative_to(root)),'archiveManifestSha256':sha(archive/'manifest.json'),'sourceHashes':r['source_after'],'ownerAuditSha256':sha(out/'owner-audit.json'),'auditScriptSha256':sha(Path(__file__)),'canonicalInvocationSha256':sha(out/'canonical-invocation.json'),'canonicalLogSha256':sha(out/'canonical-invocation.log'),'scope':{'completion':'passed','ownership':'5 actual two-connection identities; 3 mismatches refused','transactionRelease':'5 owned session/requests/captured transaction IDs/snapshots absent','stats':'OFF/sync ON/async ON; COUNT=4096; actual plans; update not observed in narrow schema/plan/window','precision':'fixture only','serviceBudget':'unavailable_functional_only','recovery':'retention released; physical allocation and production recovery unverified','communication':'idle connection Dispose simulation only, not network partition','leastPrivilege':'SA functional probe plus denied observer; full production role certification unmeasured','platform':'ARM64 SQL amd64 emulation, unsupported; no native performance certification'}}
write(out/'owner-proof.json',proof)
print(json.dumps({'status':'passed','checks':len(checks),'archive':str(archive.relative_to(root))}))
