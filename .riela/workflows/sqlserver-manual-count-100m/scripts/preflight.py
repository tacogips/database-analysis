"""Read-only independent Sol source review while MyISAM fixture loads; not final acceptance."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib, json, subprocess

root = Path(__file__).resolve().parents[4]
out = root / 'results/reviews/sqlserver-manual-count-100m'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
offline = json.loads((out / 'owner-state-matrix-offline-final.json').read_text())
assert offline['exitCode'] == 0
for name, digest in offline['sourceHashes'].items():
    assert sha(root / name) == digest, name
stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
attempt = out / ('source-preflight-' + stamp)
attempt.mkdir()
inputs = {'sourceHashes': offline['sourceHashes'], 'offlineReceiptSha256': sha(out / 'owner-state-matrix-offline-final.json')}
prompt = '''Review the CURRENT SQL Server benchmark source in sqlserver/manual-count-100m/ adversarially.
This is independent source-readiness review BEFORE actual execution, NOT final acceptance.
The owner is running MySQL/MyISAM now. Do not start Docker, pull images, build, compile, run SQL, connect to a DB, alter files, or run benchmark/offline scripts. Read files only.
Read AGENTS.md but latest explicit user instructions authorize this SQL Server state-matrix extension, Rosetta, and D NOLOCK diagnostic. Author remains separate from reviewer.
Read results/reviews/sqlserver-manual-count-100m/latest-state-request.json, owner-state-matrix-offline-final.json, owner-steering.json, then lab.rb/model.rb/Program.cs/Fixture.cs/Ag.cs and other actual source as necessary. Locate actual filenames with rg --files. Do not dump old huge results.
Find concrete bugs that could make the forthcoming fixed all fail, mismeasure, hang, destroy unrelated data, or mislabel A/B/C/D. Check C# syntax/types and APIs, SQL Server 2025 syntax, AG CLUSTER_TYPE=NONE/cert endpoints/manual restore/connection routes and state checks, Rosetta PID proof, source/path/environment ownership, memory and free-space gates, COUNT timing and data-buffer reset, release/cancellation/cleanup.
Each of six cells B-clustered/B-narrow/B-composite/C-composite/D-composite/A-composite has 3 counts on 100M rows/100 columns. A is real readable synchronized secondary; D is quiet observed_rows only. SQL max memory uniform 2048 MB. Guest-side guarded unlink must reclaim own files. OS/VM/macOS cache remains uncontrolled. No production workload/SLO claim. COUNT(1) is cancelled.
Report ONLY actionable, source-specific findings with severity high/medium/low, exact file:line, triggering condition, consequence, and minimal correction. Distinguish proven defect from uncertainty needing runtime verification; do not demand broad unnecessary experiments or changing scoped user choices. Say if none found. Return JSON acceptedForExecution (source review only), findings, unresolvedRuntimeChecks, filesRead, inputBindings. Do not claim measured success.
Exact input bindings: ''' + json.dumps(inputs)
name = 'temp-sql100m-source-preflight-sol'
base = root / '.private.handover/sql100m-source-preflight'
dest = base / name
(dest / 'nodes').mkdir(parents=True, exist_ok=True)
node = {'id':'review','executionBackend':'codex-agent','model':'gpt-6.1-sol','modelFreeze':True,'effort':'high','agentSandbox':'read-only','sessionPolicy':{'mode':'new'},'variables':{},'promptTemplate':prompt,'output':{'jsonSchema':{'type':'object','required':['acceptedForExecution','findings','unresolvedRuntimeChecks','filesRead','inputBindings'],'properties':{},'additionalProperties':True},'maxValidationAttempts':2}}
wf = {'workflowId':name,'description':'Independent read-only source readiness review, not final acceptance','defaults':{'nodeTimeoutMs':3600000,'maxLoopIterations':1},'entryStepId':'review','nodes':[{'id':'review','nodeFile':'nodes/review.json'}],'steps':[{'id':'review','nodeId':'review','role':'worker'}]}
for target, value in [(dest/'workflow.json',wf),(dest/'nodes/review.json',node),(attempt/'inputs.json',inputs),(attempt/'temporary-payload.json',{'workflow':wf,'nodePayloads':{'nodes/review.json':node},'cliCompatibility':'Installed CLI lacks inline JSON support; explicit definition directory used.'})]:
    target.write_text(json.dumps(value,indent=2)+'\n')
launch = {'startedAt':datetime.now(timezone.utc).isoformat(),'model':'gpt-6.1-sol','purpose':'source readiness only, not final acceptance','inputs':inputs,'attempt':str(attempt.relative_to(root))}
(out/'source-preflight-launch.json').write_text(json.dumps(launch,indent=2)+'\n')
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
        (out/'source-preflight-result.json').write_text(json.dumps(runtime['rootOutput'],indent=2)+'\n')
        (attempt/'result.json').write_text(json.dumps(runtime['rootOutput'],indent=2)+'\n')
for p in [out/'source-preflight-launch.json',attempt/'launch.json']:
    p.write_text(json.dumps(launch,indent=2)+'\n')
(attempt/'manifest.json').write_text(json.dumps({'files':{str(p.relative_to(attempt)):sha(p) for p in attempt.rglob('*') if p.is_file()}},indent=2)+'\n')
print(json.dumps(launch))
raise SystemExit(proc.returncode)
