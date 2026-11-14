"""Read-only independent Sol source review while MyISAM fixture loads; not final acceptance."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib, json, subprocess

root = Path(__file__).resolve().parents[4]
out = root / 'results/reviews/sqlserver-manual-count-100m'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
offline = json.loads((out / 'owner-runtime-repair-offline.json').read_text())
assert offline['exitCode'] == 0
for name, digest in offline['sourceHashes'].items():
    assert sha(root / name) == digest, name
stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
attempt = out / ('entrypoint-repair-author-' + stamp)
attempt.mkdir()
inputs = {'sourceHashes': offline['sourceHashes'], 'offlineReceiptSha256': sha(out / 'owner-runtime-repair-offline.json')}
prompt = 'Author a minimal read-only proposal fixing a proven SQL container lifecycle bug AFTER prior Sol62 repair applied. Do not edit source, run tests/build/Docker/SQL/cleanup, or start a DB. Read current sqlserver/manual-count-100m/{compose.yaml,lab.rb,model.rb,verify_offline.rb,README.md} and owner-captured results/reviews/sqlserver-manual-count-100m/server-entrypoint-inspection/{launch_sqlservr.sh,init_custom_setup.sh,run_custom_setup.sh,source.json}.\nActual pinned SQL2025 entrypoint starts "$@" in background, runs custom setup, then waits; it has NO TERM forwarding trap. Docker stop20 on failedrun forced137. Increasing stop to60 alone (already Sol62 applied) is not enough to prove orderly termination. Preserve actual forced137 as failed historical run. All actual oldSQL PIDs verified absent and owned namespace deleted; no measured COUNT from that run.\nPropose exact replacements [{path,old,new}] to supply BOTH compose SQL services a correct signal-forwarding startup preserving permissions_check, Developer PID normalization ifneeded, init_custom_setup and run_custom_setup semantics. Prefer inline YAML entrypoint, retaining exact two hostbind mounts/no newvolumes, correct Compose $$ escaping. TERM/INT must reach the specifically captured SQL child; wait for its actual exit and propagate code. Do not turn137/nonzero/OOM into0; do not send signals to unknown process/group. Startup failure must terminate/wait its own started child and preserve failure. Avoid shell traps that wait on wrongPID, return stale status, or lose signal during setup. Keep existing server60/CLI65/cleanup240 bounds and all ownership/architecture/resource limits. If a source/test/readme change is required, include exactreplacement; allowed files compose.yaml,lab.rb,model.rb,verify_offline.rb,README.md ONLY.\nAlso review runtime_rosetta identity filter: initial proof captured launch_sqlservr shell argv containing sqlservr path, not actual SQL comm. Make actualSQL-server Rosetta proof wait boundedly for /opt/mssql/bin/sqlservr process itself (not a shell whose arguments mention it), rejectQEMU, preserve PID/container identity/sourcebinding and post18count proof. No new SQL COUNT/fixture/data/indices/route changes; keep actual preload monitor smoke from Sol62. This is indispensable correctness repair, not new benchmark scope.\nReturn JSON status=proposal_ready,replacements,rationale,offlineChecksRequired,unresolvedIssues,inputBindings. Everyoldsnippetmustoccuronce. Existing18offline suite owner-run afterapply; actual lifecycle/SQL validation owner freshall only. No agentexecution. Exactsourcebindings: ' + json.dumps(inputs)
name = 'temp-sql100m-entrypoint-repair-author-sol'
base = root / '.private.handover/sql100m-entrypoint-repair-author'
dest = base / name
(dest / 'nodes').mkdir(parents=True, exist_ok=True)
node = {'id':'author','executionBackend':'codex-agent','model':'gpt-6.1-sol','modelFreeze':True,'effort':'high','agentSandbox':'read-only','sessionPolicy':{'mode':'new'},'variables':{},'promptTemplate':prompt,'output':{'jsonSchema':{'type':'object','required':['status','replacements','rationale','offlineChecksRequired','unresolvedIssues','inputBindings'],'properties':{},'additionalProperties':True},'maxValidationAttempts':2}}
wf = {'workflowId':name,'description':'Minimal runtime repair author proposal; no DB or source edits','defaults':{'nodeTimeoutMs':3600000,'maxLoopIterations':1},'entryStepId':'author','nodes':[{'id':'author','nodeFile':'nodes/author.json'}],'steps':[{'id':'author','nodeId':'author','role':'worker'}]}
for target, value in [(dest/'workflow.json',wf),(dest/'nodes/author.json',node),(attempt/'inputs.json',inputs),(attempt/'temporary-payload.json',{'workflow':wf,'nodePayloads':{'nodes/author.json':node},'cliCompatibility':'Installed CLI lacks inline JSON support; explicit definition directory used.'})]:
    target.write_text(json.dumps(value,indent=2)+'\n')
launch = {'startedAt':datetime.now(timezone.utc).isoformat(),'model':'gpt-6.1-sol','purpose':'repair author proposal only, not final acceptance','inputs':inputs,'attempt':str(attempt.relative_to(root))}
(out/'entrypoint-repair-author-launch.json').write_text(json.dumps(launch,indent=2)+'\n')
with (attempt/'validation.json').open('w') as f:
    subprocess.run(['riela','workflow','validate',name,'--workflow-definition-dir',str(base),'--output','json'],stdout=f,check=True,cwd=root)
with (attempt/'run.log').open('w') as f:
    proc = subprocess.run(['riela','workflow','run',name,'--workflow-definition-dir',str(base),'--working-dir',str(root),'--session-store',str(root/'.riela/sessions'),'--artifact-root',str(root/'.riela/artifacts'),'--max-steps','1','--output','jsonl'],stdout=f,stderr=subprocess.STDOUT,cwd=root)
launch.update(exitCode=proc.returncode,finishedAt=datetime.now(timezone.utc).isoformat())
for line in (attempt/'run.log').read_text().splitlines():
    try: event=json.loads(line)
    except ValueError: continue
    if event.get('type')=='session_started':
        launch['sessionId']=event['sessionId'];break
if launch.get('sessionId'):
    runtime=json.loads((root/'.riela/artifacts'/launch['sessionId']/'runtime-snapshot.json').read_text())
    launch['runtimeStatus']=runtime['session']['status']
    if runtime.get('rootOutput') is not None:
        (out/'entrypoint-repair-author-result.json').write_text(json.dumps(runtime['rootOutput'],indent=2)+'\n')
        (attempt/'result.json').write_text(json.dumps(runtime['rootOutput'],indent=2)+'\n')
for p in [out/'entrypoint-repair-author-launch.json',attempt/'launch.json']:
    p.write_text(json.dumps(launch,indent=2)+'\n')
(attempt/'manifest.json').write_text(json.dumps({'files':{str(p.relative_to(attempt)):sha(p) for p in attempt.rglob('*') if p.is_file()}},indent=2)+'\n')
print(json.dumps(launch))
raise SystemExit(proc.returncode)
