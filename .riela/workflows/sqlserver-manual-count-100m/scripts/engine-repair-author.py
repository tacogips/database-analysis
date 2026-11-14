"""Read-only Sol engine remediation proposal after terminal SQLPal failure; not final acceptance."""
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
attempt = out / ('engine-repair-author-' + stamp)
attempt.mkdir()
inputs = {'sourceHashes': offline['sourceHashes'], 'offlineReceiptSha256': sha(out / 'owner-state-matrix-offline-final.json')}
prompt = 'Author a minimal READ-ONLY remediation proposal for one proven lab failure. Do not edit source, run DB/Docker/tests/build/cleanup/network/delegation or claim success. Read AGENTS.md, sqlserver/manual-count-100m/{lab.rb,compose.yaml,model.rb,verify_offline.rb,README.md,Program.cs,State.cs}, .riela/workflows/sqlserver-manual-count-100m/scripts/execute.py, results/reviews/sqlserver-manual-count-100m/{ag-restore-failure-analysis.json,entrypoint-repair-owner-application.json,owner-state-matrix-offline-final.json,ag-restore-failure-owner-cleanup.json}, server2022-entrypoint-inspection/*, and actual failedrun225157 result/probe (summarize bigarrays ratherthan dump) +00682/00687 stderr logs.\nLatest actual SQL2025 pinnedall: same100M100columns loaded, 15 valid counts B-clustered/narrow/composite C/D, actualsecondary started throughRosetta, backup done, then BOTH SQL servers fatal SQLPal seenByMonitor<NonYieldThreshold sosschedmon219 atsame23:23:28UTC duringrestore. Containerexit1/OOMfalse. Full run failed, A3unmeasured, cleanupfailed due servernonzero. Independent owner deletedexactnamespace+stage but physicalcapacityrestoration1GiB predicate failed (helperexit1 preserved), currenthostfree~137GiB vsbefore201; noVMrestart/resize/globalOSdropcache/otherresourcecleanup allowed. No postfailure DB running. 15 counts remain historical validobservations not completed18.\nUser requires SQLServer throughRosetta, same full100M100cols sixcells×3=18 eachDBbuffer0 actualAGsecondary, finalnormalserverexit0 andownedcleanup; do not shrink/dropA, mixversions, reuse2025backup on2022, fakeAG/QEMU/native, altertables/countSQL/indexes/budgets/memory/cpu/hostVM, suppressassert viaundocumentedflags, disablemonitor, normalizeexit1/137 or claimrootcauseproven. SameMySQLacceptedsource/docs untouched. SQL versionwas implementationchoice, useronlyRuby askedlatest; SQL2022 supported product lifecycle through2033, but Rosetta stillunsupported.\nOwner registry inspection resolved current SQL2022 image EXACT mcr.microsoft.com/mssql/server:2022-latest@sha256:cf8f0ab2ebe61feec2eed2629061a07d247f791c2a8ddda74a4ac9f06a291718. Owner pulledandread vendor scripts ONLY, no2022DBstart. See capturedscripts allpresent/exit0. Vendor2022launcher backgroundsSQLwithoutTERMforwarding as2025, noDeveloperPIDnormalization but fixedMSSQL_PID=Developer valid; existing inlinewrapper shouldretaincorrectpermission/init/setup/signal/wait/actualexit semantics. Similarassertreported inmicrosoft/mssql-docker issue959 forSQL2025includingx86 and restoredDB, commentersreport2022works; NOTMicrosoftconfirmedfix nor establishedcause. Treat2022pinnedfreshallasoneevidence-basedcontrolledretry, neverguaranteedremediation; noautomaticunboundedretries.\nPropose exact minimal replacements [{path,old,new}] allowed sqlserver/manual-count-100m/{lab.rb,compose.yaml,verify_offline.rb,README.md} and .riela/workflows/sqlserver-manual-count-100m/scripts/execute.py only. Changefinalenginepinconsistently controller/twoservices/ownerpull, documentruntimeversion+failed2025historicalresults/unsupportedRosetta/currentnotproven. PreserveSDK/dependencies/timers/SQL all18/orderedload/all19checks+startupPIDretry/realpreloadsmoke/actualshutdown0/spacegates70GiBbeforesecondary24beforeindex12floor/cleanup240. Keepcapacityrestore separate, do notchangegatesjusttogreen. ownerexecute mustbind NEW engineauthorlaunch andownerapplication+offlinefinalreceipt withoutoverwritingoldauthorchainorfailedarchive. Suggest minimal integration of new application receipt, preserve earlier entrypointapplication as historical. Exact applyowneronlyafterterminalarchive/ownednamespaceabsence; freshsource-boundoffline requiredbeforeDB. PostrunfullSol2freshrolesremainrequired. If no indispensable sourcefix beyondpin/readme/gateprovenance, keepchange minimal. Return JSON status=proposal_ready,replacements,rationale,offlineChecksRequired,unresolvedIssues,inputBindings. Everyoldsnippet occurs exactlyonce. State limitations not falselyblockjustbecauseunsupportedarchitecturewhichuserexplicitlyrequested. No newexperiments broadmatrix. Exactsourcebindings: ' + json.dumps(inputs)
name = 'temp-sql100m-engine-repair-author-sol'
base = root / '.private.handover/sql100m-engine-repair-author'
dest = base / name
(dest / 'nodes').mkdir(parents=True, exist_ok=True)
node = {'id':'author','executionBackend':'codex-agent','model':'gpt-6.1-sol','modelFreeze':True,'effort':'high','agentSandbox':'read-only','sessionPolicy':{'mode':'new'},'variables':{},'promptTemplate':prompt,'output':{'jsonSchema':{'type':'object','required':['status','replacements','rationale','offlineChecksRequired','unresolvedIssues','inputBindings'],'properties':{},'additionalProperties':True},'maxValidationAttempts':2}}
wf = {'workflowId':name,'description':'Minimal runtime repair author proposal; no DB or source edits','defaults':{'nodeTimeoutMs':3600000,'maxLoopIterations':1},'entryStepId':'author','nodes':[{'id':'author','nodeFile':'nodes/author.json'}],'steps':[{'id':'author','nodeId':'author','role':'worker'}]}
for target, value in [(dest/'workflow.json',wf),(dest/'nodes/author.json',node),(attempt/'inputs.json',inputs),(attempt/'temporary-payload.json',{'workflow':wf,'nodePayloads':{'nodes/author.json':node},'cliCompatibility':'Installed CLI lacks inline JSON support; explicit definition directory used.'})]:
    target.write_text(json.dumps(value,indent=2)+'\n')
launch = {'startedAt':datetime.now(timezone.utc).isoformat(),'model':'gpt-6.1-sol','purpose':'repair author proposal only, not final acceptance','inputs':inputs,'attempt':str(attempt.relative_to(root))}
(out/'engine-repair-author-launch.json').write_text(json.dumps(launch,indent=2)+'\n')
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
        (out/'engine-repair-author-result.json').write_text(json.dumps(runtime['rootOutput'],indent=2)+'\n')
        (attempt/'result.json').write_text(json.dumps(runtime['rootOutput'],indent=2)+'\n')
for p in [out/'engine-repair-author-launch.json',attempt/'launch.json']:
    p.write_text(json.dumps(launch,indent=2)+'\n')
(attempt/'manifest.json').write_text(json.dumps({'files':{str(p.relative_to(attempt)):sha(p) for p in attempt.rglob('*') if p.is_file()}},indent=2)+'\n')
print(json.dumps(launch))
raise SystemExit(proc.returncode)
