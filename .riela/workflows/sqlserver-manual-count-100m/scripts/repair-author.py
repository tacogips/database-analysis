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
attempt = out / ('runtime-repair-author-' + stamp)
attempt.mkdir()
inputs = {'sourceHashes': offline['sourceHashes'], 'offlineReceiptSha256': sha(out / 'owner-state-matrix-offline-final.json')}
prompt = 'You author a narrowly scoped repair proposal for the current SQL Server 100M-row benchmark. READ ONLY. Do not edit source, start Docker/DB/build/tests/measurements, connect to SQL, or delete anything. Owner will apply exact replacements only after the failed run is archived and owned data cleaned.\nRead AGENTS.md (latest user explicitly authorizes SQL Server/Rosetta and manual A/B/C/D extension), sqlserver/manual-count-100m/Program.cs, Fixture.cs, State.cs, lab.rb, model.rb, verify_offline.rb, README.md, and failed results/sqlserver/manual-count-100m/runs/20261108T213706Z-b0798be83c64c6dc5992bed4adae8ed5/{result.json,probe.json} using bounded summaries, not dumping1000receipts. Existing source fingerprints are exact owner18 offline inputs.\nActual failed owner run: all1000commits/100M rows completed in945.88sec, zero measured COUNTs, then ObserveSql SQLsyntax error near WHEREs. C# raw string ends WHERE with no trailing newline; concat Match begins s.session_id. Release in finally encounters same bad SQL. Cleanup docker stop --time20 forced primary exit137; raw runexit1/cleanupfailed/owned stage retained. Never relabel these as success.\nReturn smallest coherent exact-text replacements [{path,old,new}] for:\n1. Fix raw-string token-boundary ObserveSql defect; audit analogous actual SQL joins and fix only concrete defects.\n2. Add meaningful fail-fast actual monitor/identity/release smoke on the new EMPTY100-column fixture BEFORE expensive 100M insertion, using independent dedicated connections and existing Guard/Observe/Release/Idle machinery. No SELECT COUNT on Target, no sample/condition added, same18count slots. Persist smoke outcome; propagate failure. This is actual SQL regression coverage at owner all execution, not a lexical mirror test. Ensure smoke connections disposed/released before load.\n3. Normal orderly SQL shutdown: allow each server60seconds rather than20, CLI bounded65seconds, overall independent cleanup240seconds for2nodes+guards+guestunlink. Keep forced/nonzero/OOM as FAILURE, retain ownership checks and stage-preservation rules. If adding a bounded <=30sec capacity-reclamation wait after successful guest unlink, keep original within1GiB requirement and record each observation; no purge/restart or threshold relaxation.\n4. Actual first fixture PK allocated65,222,975,488bytes; 2nodeAG restore on ~154GiB available plusindexes/logs/tempdb/backups has narrow margin. InsertSql digit-crossjoin currently has no ORDER BY id and generates nonmonotonic keys. For new canonical fixture, prefer ORDER BY id OPTION(MAXDOP1) on the INSERT SELECT to keep PK-ordered load and reduce split/space overhead, preserving EXACT100M rows/100storedcolumns/identical formulas/1000x100kreceipts. This changes fixture load only, not COUNT SQL/hints/plans/cold protocol. Disclose ordered-loader change as unmeasured improvement until actual newrun; retain actual resulting pages/plan checks. Do not add repair/rebuild after loading or change index/data geometry or count-case resource limits.\nUpdate README and existing offline tests only where needed for these exact changes; provide replacements for existing files, no new broad suites. Keep count20min/load2h/global6h/host70-index24-floor12/state6cells18/default2GB both nodes/Rosetta proofs/guestguarded cleanup/no context-tag writes intact. Compiler and actual DB execution remain OWNER only. Author must not run anything. Owner reruns existing offline suite and fresh all.\nOutput JSON status=proposal_ready, replacements (every old string must occur exactly once in its file), rationale, offlineChecksRequired, unresolvedIssues, inputBindings. No irrelevant feature or scope expansion. Each path limited sqlserver/manual-count-100m/{Program.cs,Fixture.cs,lab.rb,README.md,verify_offline.rb,model.rb}. Distinguish actual measured failure from predicted improvement.\nExact bindings: ' + json.dumps(inputs)
name = 'temp-sql100m-runtime-repair-author-sol'
base = root / '.private.handover/sql100m-runtime-repair-author'
dest = base / name
(dest / 'nodes').mkdir(parents=True, exist_ok=True)
node = {'id':'author','executionBackend':'codex-agent','model':'gpt-6.1-sol','modelFreeze':True,'effort':'high','agentSandbox':'read-only','sessionPolicy':{'mode':'new'},'variables':{},'promptTemplate':prompt,'output':{'jsonSchema':{'type':'object','required':['status','replacements','rationale','offlineChecksRequired','unresolvedIssues','inputBindings'],'properties':{},'additionalProperties':True},'maxValidationAttempts':2}}
wf = {'workflowId':name,'description':'Minimal runtime repair author proposal; no DB or source edits','defaults':{'nodeTimeoutMs':3600000,'maxLoopIterations':1},'entryStepId':'author','nodes':[{'id':'author','nodeFile':'nodes/author.json'}],'steps':[{'id':'author','nodeId':'author','role':'worker'}]}
for target, value in [(dest/'workflow.json',wf),(dest/'nodes/author.json',node),(attempt/'inputs.json',inputs),(attempt/'temporary-payload.json',{'workflow':wf,'nodePayloads':{'nodes/author.json':node},'cliCompatibility':'Installed CLI lacks inline JSON support; explicit definition directory used.'})]:
    target.write_text(json.dumps(value,indent=2)+'\n')
launch = {'startedAt':datetime.now(timezone.utc).isoformat(),'model':'gpt-6.1-sol','purpose':'repair author proposal only, not final acceptance','inputs':inputs,'attempt':str(attempt.relative_to(root))}
(out/'runtime-repair-author-launch.json').write_text(json.dumps(launch,indent=2)+'\n')
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
        (out/'runtime-repair-author-result.json').write_text(json.dumps(runtime['rootOutput'],indent=2)+'\n')
        (attempt/'result.json').write_text(json.dumps(runtime['rootOutput'],indent=2)+'\n')
for p in [out/'runtime-repair-author-launch.json',attempt/'launch.json']:
    p.write_text(json.dumps(launch,indent=2)+'\n')
(attempt/'manifest.json').write_text(json.dumps({'files':{str(p.relative_to(attempt)):sha(p) for p in attempt.rglob('*') if p.is_file()}},indent=2)+'\n')
print(json.dumps(launch))
raise SystemExit(proc.returncode)
