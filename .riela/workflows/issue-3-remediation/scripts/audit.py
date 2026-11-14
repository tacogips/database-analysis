"""Owner post-run audit; does not execute SQL or launch containers."""
from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,shutil,subprocess
root=Path(__file__).resolve().parents[4]
out=root/'results/reviews/issue-3'; run=None
for line in (out/'canonical-invocation.log').read_text().splitlines():
 try:
  item=json.loads(line)
  if isinstance(item,dict) and 'result' in item:run=Path(item['result']) .parent
 except ValueError:pass
assert run is not None and run.resolve().is_relative_to(root/'results/sqlserver/issue3/runs')
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
plans=events('fixture_control_stats_count_plan');before=events('stats_before_count');after=events('stats_after_count')
check('stats_three_profiles', [x['profile'] for x in plans]==['off','sync_on','async_on'] and all(x['count']==4096 and x['plan_status']=='actual_plan_captured' and not x['optimizer_stats_usage'] for x in plans))
for x,y in zip(before,after):
 s=x['settings'][0];profile=x['profile'];expected={'off':(False,False,False),'sync_on':(True,True,False),'async_on':(True,True,True)}[profile]
 check('stats_'+profile,tuple(s[k] for k in ['is_auto_create_stats_on','is_auto_update_stats_on','is_auto_update_stats_async_on'])==expected and x['stats']==y['after'] and len(y['recovery_windows'])==3 and 'not observed' in y['conclusion'])
check('source_lock_matches_runtime',sha(root/'sqlserver/issue3/packages.lock.json')==sha(run/'packages.lock.json'))
# Verify the actual CONTEXT_INFO-RLS fixture and guarded rejection events, not old 4096-row success.
check('rls_seeded_physical_five',events('fixture_control_seeded_physical_count')[0]['count']==5)
controls=events('fixture_control_count')
check('rls_controls_2_3_0_not_physical_empty',len(controls)==3 and [(x['tenant'],x['visible']) for x in controls]==[(1,2),(2,3),(99,0)] and all(x['seeded_physical']==x['physical_metadata_reference']==5 for x in controls))
held=events('rls_hold_preserves_context')
check('rls_seven_refusals',len(held)==7 and {x['name'] for x in held}=={'business_1','unknown_null_later_1','unknown_business_2_later_1','missing_null_later_2'})
check('before_tagwrite_and_candidatecount',all(x['refused'] and x['direct_wrapper_refused'] and x['contract_only_refused'] and x['direct_contract_only_refused'] and x['tag_writes']==x['candidate_count_dispatches']==0 for x in held))
check('business_context_preserved_both_connections',all(x['originalContext']==x['contextAfter'] and x['owner_before']==x['owner_after']==x['observer_before']==x['observer_after'] for x in held))
check('null_not_approval_and_later_business_init',sum(x['originalContext'] is None for x in held)==2 and sum(x['pass']==1 for x in held)==3)
check('held_no_open_request_tx_snapshot',all(not x['state']['Requests'] and not x['state']['Transactions'] and not x['state']['Snapshots'] and not x['state']['ActiveTransactions'] and x['state']['Sessions'][0]['open_transaction_count']==0 for x in held))
releases=events('rls_hold_owned_dispose_release')
check('rls_four_owned_disposals_no_kill',len(releases)==4 and all(not x['kill_sent'] and x['status']=='session_request_tx_snapshot_absent' for x in releases))
lastpoll=None
for v in e:
 if v['name']=='release_poll':lastpoll=v['detail']
 if v['name']=='rls_hold_owned_dispose_release':check('rls_release_poll_'+v['detail']['name'],lastpoll is not None and all(not lastpoll[k] for k in ['Sessions','Requests','Transactions','Snapshots','ActiveTransactions']))
check('rls_policy_still_enabled',all(x['is_enabled'] and x['is_schema_bound'] for x in events('rls_policy_final')[0]))
check('job_exclusive_positive_approval',len(events('job_exclusive_approval'))==5 and all(x['tag_writes']==1 and x['contract']['Usage'] in [2,'JOB_EXCLUSIVE'] and x['contract']['BusinessInitCompletedAndAudited'] and x['contract']['RlsAndDependenciesNonconflicting'] and x['contract']['LaterInitAuditTriggersNonconflicting'] for x in events('job_exclusive_approval')))
check('rls_no_operational_certification','HOLD_refusal_only' in p['rls_context_safety'])
# Recheck the exact same local Unix daemon after the owned cleanup, without accepting inspect errors as absence.
cli=['docker','--host',r['context']['endpoint']];check('unix_daemon',r['context']['endpoint'].startswith('unix:///'))
inventories={}
for kind,names in {'container':['lab-sqlserver-issue3','lab-sqlserver-issue3-client'],'volume':['lab-sqlserver-issue3-data'],'network':['lab-sqlserver-issue3-network']}.items():
 args=cli+[kind,'ls']+(['-a'] if kind=='container' else [])+['--format','{{.Names}}' if kind=='container' else '{{.Name}}']
 q=subprocess.run(args,capture_output=True,text=True);inventories[kind]={'argv':args,'exit_status':q.returncode,'stdout':q.stdout,'stderr':q.stderr}
 check('owned_'+kind+'_absent',q.returncode==0 and not set(names)&set(q.stdout.splitlines()))
check('controller_cleanup',r['cleanup']=='passed_owned_runtime_resources_absent' and not r['cleanup_errors'])
archive=root/('results/reference/issue-3-runtime-'+run.name);archive.mkdir(exist_ok=True)
for f in run.iterdir():
 if f.is_file():
  target=archive/f.name
  if target.exists():check('immutable_archive_'+f.name,sha(f)==sha(target))
  else:shutil.copyfile(f,target)
manifest={'classification':'new_issue3_context_guard_functional_run_not_production_rls_support','original_run':str(run.relative_to(root)),'files':{f.name:sha(f) for f in archive.iterdir() if f.is_file() and f.name!='manifest.json'}}
write(archive/'manifest.json',manifest)
docs={f:sha(root/f) for f in b['mutableDocuments']}
import re
from collections import deque
graph={};missing=[];pending='results/reviews/issue-3/acceptance.json'
for f in docs:
 links=[];file=root/f
 for target in re.findall(r'\]\(([^)]+)\)',file.read_text()):
  if '://' in target or target.startswith('#'):continue
  path=(file.parent/target.split('#')[0]).resolve();rel=str(path.relative_to(root));links.append(rel)
  if not path.exists() and rel!=pending:missing.append([f,target])
 graph[f]=links
check('local_links_except_pending_receipt',not missing,missing)
dist={'README.md':0};queue=deque(['README.md'])
while queue:
 f=queue.popleft()
 for t in graph.get(f,[]):
  if t not in dist:dist[t]=dist[f]+1;queue.append(t)
targets=['docs/README.md','docs/guidelines/mysql.md','docs/guidelines/sql-server.md','docs/guidelines/operations-runbook.md',pending]
check('github_entry_within_two_clicks',all(dist.get(f,999)<=2 for f in targets),{f:dist.get(f) for f in targets})
check('current_receipt_links',all('results/reviews/issue-3/acceptance.json' in (root/f).read_text() for f in docs))
report={'status':'passed','checkedAt':datetime.now(timezone.utc).isoformat(),'checks':checks,'cleanupInventory':inventories,'archiveManifest':str((archive/'manifest.json').relative_to(root))}
report.update(documents=docs, navigationGraph=graph, itemAssessment={'contextContract':'Owner checked driver default-deny approval before context mutation/candidate COUNT, no NULL/admin/nonpool inference; clear unsupported CONTEXT_INFO-RLS HOLD/export branch and immutable matching reader/observer/KILL contract for reserved slot.', 'rlsHold':'Actual RLS controls visible2/3/0 with physical5; seven guarded rejections preserve raw business/NULL context, zero tag writes and candidate dispatches, four owned disposal releases; ordinary guarded cases still establish ownership/cancel/end separately.', 'history':'Prior Issue1/2 sources, receipts and evidence unchanged; current five-doc links and archived previous receipt separate. No production RLS COUNT/cancel or SLO claim.'})
write(out/'owner-audit.json',report)
proof={'status':'owner_verified','command':'mise -C sqlserver/issue3 run all','actualExitCode':0,'runtimeArchive':str(archive.relative_to(root)),'archiveManifestSha256':sha(archive/'manifest.json'),'sourceHashes':r['source_after'],'ownerAuditSha256':sha(out/'owner-audit.json'),'auditScriptSha256':sha(Path(__file__)),'canonicalInvocationSha256':sha(out/'canonical-invocation.json'),'canonicalLogSha256':sha(out/'canonical-invocation.log'),'scope':{'completion':'passed','ownership':'5 actual two-connection identities; 3 mismatches refused','transactionRelease':'5 owned session/requests/captured transaction IDs/snapshots absent','stats':'OFF/sync ON/async ON; COUNT=4096; actual plans; update not observed in narrow schema/plan/window','rlsContextSafety':'HOLD refusal only: 7 candidate refusals with context preserved; fixture controls visible2/3/0, physical5; no operational RLS COUNT+cancel certification','precision':'known non-RLS fixture plus explicitly separate RLS fixture controls only','serviceBudget':'unavailable_functional_only','recovery':'retention released; physical allocation and production recovery unverified','communication':'idle connection Dispose simulation only, not network partition','leastPrivilege':'SA functional probe plus denied observer; full production role certification unmeasured','platform':'ARM64 SQL amd64 emulation, unsupported; no native performance certification'}}
proof.update(documents=docs,baselineSha256=sha(out/'baseline.json'),oldSnapshotManifest=b['oldDocumentSnapshot'],oldSnapshotManifestSha256=sha(root/b['oldDocumentSnapshot']),officialSourcesSha256=sha(out/'official-sources.json'),historicalIssue2ProofSha256=sha(root/'results/reviews/issue-2/owner-proof.json'))
write(out/'owner-proof.json',proof)
print(json.dumps({'status':'passed','checks':len(checks),'archive':str(archive.relative_to(root))}))
