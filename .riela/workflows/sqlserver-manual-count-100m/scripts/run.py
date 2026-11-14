from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,subprocess,sys,shutil
root=Path.cwd();role=sys.argv[1];assert role in ['author','engineer','validation'];out=root/'results/reviews/sqlserver-manual-count-100m'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
b=json.loads((out/'baseline.json').read_text())
for p,h in b['protectedFiles'].items():assert sha(root/p)==h,p
prompt=(root/'.riela/workflows/sqlserver-manual-count-100m'/('author-prompt.txt' if role=='author' else 'review-prompt.txt')).read_text()
inputs={'baselineSha256':sha(out/'baseline.json'),'manualSha256':sha(root/'sqlserver/manual-count-100m/manual/sql-server.md'),'promptSha256':sha(root/'.riela/workflows/sqlserver-manual-count-100m'/('author-prompt.txt' if role=='author' else 'review-prompt.txt')),'role':role}
if role!='author':
 for file in ['owner-proof.json','owner-audit.json']:inputs[file]=sha(out/file)
 proof=json.loads((out/'owner-proof.json').read_text())
 for p,h in proof['sourceHashes'].items():assert sha(root/p)==h,p
 for p,h in proof['documentHashes'].items():assert sha(root/p)==h,p
 inputs['sourceHashes']=proof['sourceHashes'];inputs['documentHashes']=proof['documentHashes']
 if role=='validation':
  peer=json.loads((out/'final-engineer-launch.json').read_text())
  peer_result=json.loads((out/'final-engineer-result.json').read_text())
  assert peer['exitCode']==0 and peer['runtimeStatus']=='completed'
  assert peer['inputs']['owner-proof.json']==sha(out/'owner-proof.json')
  assert peer_result['documentHashes']==proof['documentHashes']
  inputs['final-engineer-result.json']=sha(out/'final-engineer-result.json')
prompt=prompt.replace('ROLE',role).replace('FOLLOWUP','Then read results/reviews/sqlserver-manual-count-100m/final-engineer-result.json and assess it in independentFindingAssessment.' if role=='validation' else '')
prompt+='\nExact input bindings: '+json.dumps(inputs)+'\nRole: '+role
name='temp-sqlserver-manual100m-'+role+'-sol';base=root/'.private.handover/sqlserver-manual100m-temp-workflows';dest=base/name;(dest/'nodes').mkdir(parents=True,exist_ok=True)
required=['status','files','unresolvedIssues'] if role=='author' else ['accepted','decision','documentHashes','findings','requestedExperiments','reviewEvidence']
node={'id':role,'executionBackend':'codex-agent','model':'gpt-6.1-sol','modelFreeze':True,'effort':'high','agentSandbox':'workspace-write' if role=='author' else 'read-only','sessionPolicy':{'mode':'new'},'variables':{},'promptTemplate':prompt,'output':{'jsonSchema':{'type':'object','required':required,'properties':{},'additionalProperties':True},'maxValidationAttempts':2}}
wf={'workflowId':name,'description':'SQL Server Rosetta manual 100M '+role,'defaults':{'nodeTimeoutMs':600000,'maxLoopIterations':1},'entryStepId':role,'nodes':[{'id':role,'nodeFile':'nodes/'+role+'.json'}],'steps':[{'id':role,'nodeId':role,'role':'worker'}]}
(dest/'workflow.json').write_text(json.dumps(wf,indent=2)+'\n');(dest/'nodes'/f'{role}.json').write_text(json.dumps(node,indent=2)+'\n')
attempt=out/(role+'-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'));attempt.mkdir();(attempt/'inputs.json').write_text(json.dumps(inputs,indent=2)+'\n');shutil.copyfile(dest/'workflow.json',attempt/'workflow.json');shutil.copyfile(dest/'nodes'/f'{role}.json',attempt/'node.json')
(attempt/'temporary-payload.json').write_text(json.dumps({'workflow':wf,'nodePayloads':{f'nodes/{role}.json':node},'cliCompatibility':'Installed CLI lacks --workflow-json-file/--dry-run: validate explicit definition dir then execute one step; exact embedded payload preserved.'},indent=2)+'\n')
key=role if role=='author' else 'final-'+role
launch={'startedAt':datetime.now(timezone.utc).isoformat(),'role':role,'model':'gpt-6.1-sol','inputs':inputs,'attempt':str(attempt.relative_to(root))};(out/(key+'-launch.json')).write_text(json.dumps(launch,indent=2)+'\n')
with (attempt/'validation.json').open('w') as f:subprocess.run(['riela','workflow','validate',name,'--workflow-definition-dir',str(base),'--output','json'],check=True,stdout=f)
with (attempt/'run.log').open('w') as f:r=subprocess.run(['riela','workflow','run',name,'--workflow-definition-dir',str(base),'--working-dir',str(root),'--session-store',str(root/'.riela/sessions'),'--artifact-root',str(root/'.riela/artifacts'),'--max-steps','1','--output','jsonl'],stdout=f,stderr=subprocess.STDOUT)
launch.update(exitCode=r.returncode,finishedAt=datetime.now(timezone.utc).isoformat())
for l in (attempt/'run.log').read_text().splitlines():
 try:e=json.loads(l)
 except ValueError:continue
 if e.get('type')=='session_started':launch['sessionId']=e['sessionId'];break
if launch.get('sessionId'):
 runtime=json.loads((root/'.riela/artifacts'/launch['sessionId']/'runtime-snapshot.json').read_text());launch['runtimeStatus']=runtime['session']['status'];result=runtime.get('rootOutput')
 if result is not None:
  (out/(key+'-result.json')).write_text(json.dumps(result,indent=2)+'\n');(attempt/'result.json').write_text(json.dumps(result,indent=2)+'\n')
(out/(key+'-launch.json')).write_text(json.dumps(launch,indent=2)+'\n');(attempt/'launch.json').write_text(json.dumps(launch,indent=2)+'\n');(attempt/'manifest.json').write_text(json.dumps({'files':{str(p.relative_to(attempt)):sha(p) for p in attempt.rglob('*') if p.is_file()}},indent=2)+'\n');print(json.dumps(launch))
