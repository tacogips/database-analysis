"""New receipt after owner offline audit and two separate Sol reviews of exact bytes."""
from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,re
root=Path(__file__).resolve().parents[4];out=root/'results/reviews/issue-2'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def read(p):return json.loads(p.read_text())
def write(p,x):p.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n')
b=read(out/'baseline.json');proof=read(out/'owner-proof.json');audit=read(out/'owner-audit.json');docs={f:sha(root/f) for f in b['mutableDocuments']}
assert proof['status']=='owner_verified_documentation_only' and audit['status']=='passed_offline_owner_audit'
assert proof['documents']==audit['documents']==docs and proof['ownerAuditSha256']==sha(out/'owner-audit.json')
assert proof['auditScriptSha256']==sha(Path(__file__).parent/'audit.py') and proof['baselineSha256']==sha(out/'baseline.json')
assert proof['officialSourcesSha256']==sha(out/'official-sources.json')
assert proof['historicalIssue1ProofSha256']==sha(root/'results/reviews/issue-1/owner-proof.json')
for f,h in b['protectedFiles'].items():assert sha(root/f)==h,f
old=root/b['oldDocumentSnapshot'];assert sha(old)==proof['oldSnapshotManifestSha256']
for f,h in read(old)['files'].items():assert sha(old.parent/f)==h,f
sessions=[];reviews={}
for role in ['author','engineer','validation']:
 key='author' if role=='author' else 'final-'+role
 launch=read(out/(key+'-launch.json'));result=read(out/(key+'-result.json'));attempt=root/launch['attempt'];manifest=read(attempt/'manifest.json');node=read(attempt/'node.json')
 assert launch['exitCode']==0 and launch['runtimeStatus']=='completed' and launch['model']=='gpt-6.1-sol'
 assert node['model']=='gpt-6.1-sol' and node['modelFreeze'] and node['sessionPolicy']['mode']=='new'
 for f,h in manifest['files'].items():assert sha(attempt/f)==h,f
 sessions.append(launch['sessionId'])
 if role=='author':assert not result['unresolvedIssues']
 else:
  assert node['agentSandbox']=='read-only'
  assert result['accepted'] and result['decision']=='accepted' and result['documentHashes']==docs
  assert not result['requestedExperiments'] and not [x for x in result['findings'] if x.get('severity') in ['high','middle','medium']]
  assert launch['inputs']['documents']==docs and launch['inputs']['ownerProofSha256']==sha(out/'owner-proof.json') and launch['inputs']['baselineSha256']==sha(out/'baseline.json')
  reviews[role]={'sessionId':launch['sessionId'],'launchSha256':sha(out/(key+'-launch.json')),'resultSha256':sha(out/(key+'-result.json')),'manifest':str((attempt/'manifest.json').relative_to(root)),'manifestSha256':sha(attempt/'manifest.json')}
assert len(set(sessions))==3
pending='results/reviews/issue-2/acceptance.json'
for f in docs:
 p=root/f
 for t in re.findall(r'\]\(([^)]+)\)',p.read_text()):
  if '://' in t or t.startswith('#'):continue
  target=(p.parent/t.split('#')[0]).resolve()
  assert target.exists() or str(target.relative_to(root))==pending,(f,t)
completion={'status':'passed','checkedAt':datetime.now(timezone.utc).isoformat(),'documents':docs,'protectedFileCount':len(b['protectedFiles']),'ownerProofSha256':sha(out/'owner-proof.json'),'issueSha256':sha(out/'issue.json'),'reviews':reviews,'authorSession':sessions[0],'items':audit['itemAssessment'],'navigation':next(x['detail'] for x in audit['checks'] if x['name']=='github_entry_within_two_clicks'),'scripts':{str(p.relative_to(root)):sha(p) for p in Path(__file__).parent.glob('*.py')},'scope':proof['scope'],'notExecuted':audit['notExecuted']}
write(out/'completion-audit.json',completion)
receipt={'status':'accepted','issue':2,'issueUrl':'https://github.com/tacogips/database-analysis/issues/2','acceptedAt':datetime.now(timezone.utc).isoformat(),'documents':docs,'ownerProofSha256':sha(out/'owner-proof.json'),'completionAuditSha256':sha(out/'completion-audit.json'),'reviews':reviews,'historicalReceipt':b['oldDocumentSnapshot'],'scope':proof['scope'],'meaning':'Current five operator documents accepted for these exact hashes; historical measured inputs/receipts unchanged; no new DB/HA test or production SLO certification.'}
write(out/'acceptance.json',receipt)
print(json.dumps({'status':'accepted','sessions':sessions,'documents':len(docs),'protectedFiles':len(b['protectedFiles'])}))
