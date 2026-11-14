"""Sol authors a duration-only patch proposal; live benchmark source remains untouched."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib, json, subprocess

root=Path(__file__).resolve().parents[4]
out=root/'results/reviews/mysql-engine-count-100m-cold'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
run=root/'results/mysql-engine-count-100m-cold/runs/20261108T172604Z-ca74a6381f9f93ff0ed6ed7c87471500/result.json'
d=json.loads(run.read_text()); sources=d['source_before']
assert all(sha(root/p)==h for p,h in sources.items())
stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
attempt=out/('load-duration-author-'+stamp);attempt.mkdir()
inputs={'sourceHashes':sources,'observedRun':str(run.relative_to(root)),'observedRunSha256AtLaunch':sha(run)}
prompt='''You are the author of a minimal timeout-only revision to the MySQL 100M cold-buffer benchmark. Return a patch proposal only; do NOT edit files or run any commands that execute SQL, tests, Docker, builds, measurements or cleanup. The existing process is alive; its frozen source must stay unchanged.
Read AGENTS.md and mysql/engine-count-100m-cold/lab.rb, README.md, verify_offline.rb, reference_flow.rb as needed. User explicitly requested this optional static MyISAM comparison and cold DB-buffer resets before every COUNT. Current MyISAM load (1000 batches x100k rows) has run >100 batches at about7.3sec/batch and will likely exceed the fixed 7200sec load-stage deadline. InnoDB nine counts already finished but the owner requires one complete reproducible all from blank for acceptance; failed runs stay separate references.
Author the smallest exact textual replacements to allow MyISAM fixture loading 14400seconds, retaining InnoDB loading7200seconds, individual SQL-request7200seconds, total-run6hours, count20minutes, index2hours, cleanup120seconds, all ownership/failure/data/SQL/cache/COUNT semantics. No data/load optimization, new SQL, new engine, new measurement, resume or mixed-run acceptance. Update README documentation of stage bounds accurately. If documentation currently conflates the individual-request limit with the stage limit, clarify it. Do not change other source files unless strictly needed. No tests are required for a duration-only constant/branch change; inspect existing tests for compatibility and report whether owner must rerun the existing offline suite.
Return JSON status="proposal_ready", replacements=[{path,old,new}], rationale, offlineChecksRequired, unresolvedIssues. Exact old strings must appear once per target file. No actual file edits. The owner may apply only AFTER the current invocation is terminal and cleanup/archiving is audited. This is authoring, not final review or measured success.
Exact source input bindings: '''+json.dumps(inputs)
name='temp-mysql-cold100m-load-duration-author-sol';base=root/'.private.handover/mysql-duration-plan';dest=base/name;(dest/'nodes').mkdir(parents=True,exist_ok=True)
node={'id':'author','executionBackend':'codex-agent','model':'gpt-6.1-sol','modelFreeze':True,'effort':'high','agentSandbox':'read-only','sessionPolicy':{'mode':'new'},'variables':{},'promptTemplate':prompt,'output':{'jsonSchema':{'type':'object','required':['status','replacements','rationale','offlineChecksRequired','unresolvedIssues'],'properties':{},'additionalProperties':True},'maxValidationAttempts':2}}
wf={'workflowId':name,'description':'Sol authors duration-only patch without touching live source','defaults':{'nodeTimeoutMs':3600000,'maxLoopIterations':1},'entryStepId':'author','nodes':[{'id':'author','nodeFile':'nodes/author.json'}],'steps':[{'id':'author','nodeId':'author','role':'worker'}]}
for path,value in [(dest/'workflow.json',wf),(dest/'nodes/author.json',node),(attempt/'inputs.json',inputs),(attempt/'temporary-payload.json',{'workflow':wf,'nodePayloads':{'nodes/author.json':node},'cliCompatibility':'Installed CLI lacks inline JSON; validated explicit definition used.'})]:path.write_text(json.dumps(value,indent=2)+'\n')
launch={'startedAt':datetime.now(timezone.utc).isoformat(),'model':'gpt-6.1-sol','purpose':'duration-only source proposal, not final acceptance','inputs':inputs,'attempt':str(attempt.relative_to(root))}
(out/'load-duration-author-launch.json').write_text(json.dumps(launch,indent=2)+'\n')
with (attempt/'validation.json').open('w') as f:subprocess.run(['riela','workflow','validate',name,'--workflow-definition-dir',str(base),'--output','json'],cwd=root,stdout=f,check=True)
with (attempt/'run.log').open('w') as f:proc=subprocess.run(['riela','workflow','run',name,'--workflow-definition-dir',str(base),'--working-dir',str(root),'--session-store',str(root/'.riela/sessions'),'--artifact-root',str(root/'.riela/artifacts'),'--max-steps','1','--output','jsonl'],stdout=f,stderr=subprocess.STDOUT,cwd=root)
launch.update(exitCode=proc.returncode,finishedAt=datetime.now(timezone.utc).isoformat())
for line in (attempt/'run.log').read_text().splitlines():
 try:e=json.loads(line)
 except ValueError:continue
 if e.get('type')=='session_started':launch['sessionId']=e['sessionId'];break
if launch.get('sessionId'):
 runtime=json.loads((root/'.riela/artifacts'/launch['sessionId']/'runtime-snapshot.json').read_text());launch['runtimeStatus']=runtime['session']['status']
 if runtime.get('rootOutput') is not None:
  for p in [out/'load-duration-author-result.json',attempt/'result.json']:p.write_text(json.dumps(runtime['rootOutput'],indent=2)+'\n')
assert all(sha(root/p)==h for p,h in sources.items()),'Author must not change live frozen source'
for p in [out/'load-duration-author-launch.json',attempt/'launch.json']:p.write_text(json.dumps(launch,indent=2)+'\n')
(attempt/'manifest.json').write_text(json.dumps({'files':{str(p.relative_to(attempt)):sha(p) for p in attempt.rglob('*') if p.is_file()}},indent=2)+'\n')
print(json.dumps({'exitCode':proc.returncode,'sessionId':launch.get('sessionId'),'runtimeStatus':launch.get('runtimeStatus')}))
raise SystemExit(proc.returncode)
