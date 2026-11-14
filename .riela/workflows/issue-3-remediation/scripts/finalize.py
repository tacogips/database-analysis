"""New receipt after owner runtime/source/cleanup audit and two separate Sol reviews of exact bytes."""
from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,re
root=Path(__file__).resolve().parents[4];out=root/'results/reviews/issue-3'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def read(p):return json.loads(p.read_text())
def write(p,x):p.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n')
b=read(out/'baseline.json');proof=read(out/'owner-proof.json');audit=read(out/'owner-audit.json');docs={f:sha(root/f) for f in b['mutableDocuments']}
assert proof['status']=='owner_verified' and audit['status']=='passed'
assert proof['documents']==audit['documents']==docs and proof['ownerAuditSha256']==sha(out/'owner-audit.json')
assert proof['auditScriptSha256']==sha(Path(__file__).parent/'audit.py') and proof['baselineSha256']==sha(out/'baseline.json')
assert proof['officialSourcesSha256']==sha(out/'official-sources.json')
assert proof['historicalIssue2ProofSha256']==sha(root/'results/reviews/issue-2/owner-proof.json')
assert proof['canonicalInvocationSha256']==sha(out/'canonical-invocation.json') and proof['canonicalLogSha256']==sha(out/'canonical-invocation.log')
assert read(out/'canonical-invocation.json')['exitCode']==proof['actualExitCode']==0
for f,h in {**b['protectedFiles'],**proof['sourceHashes']}.items():assert sha(root/f)==h,f
archive=root/proof['runtimeArchive'];assert sha(archive/'manifest.json')==proof['archiveManifestSha256']
for f,h in read(archive/'manifest.json')['files'].items():assert sha(archive/f)==h,f
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
  assert launch['inputs']['sourceHashes']==proof['sourceHashes']
  assert launch['inputs']['documents']==docs and launch['inputs']['ownerProofSha256']==sha(out/'owner-proof.json') and launch['inputs']['baselineSha256']==sha(out/'baseline.json')
  reviews[role]={'sessionId':launch['sessionId'],'launchSha256':sha(out/(key+'-launch.json')),'resultSha256':sha(out/(key+'-result.json')),'manifest':str((attempt/'manifest.json').relative_to(root)),'manifestSha256':sha(attempt/'manifest.json')}
assert len(set(sessions))==3
pending='results/reviews/issue-3/acceptance.json'
for f in docs:
 p=root/f
 for t in re.findall(r'\]\(([^)]+)\)',p.read_text()):
  if '://' in t or t.startswith('#'):continue
  target=(p.parent/t.split('#')[0]).resolve()
  assert target.exists() or str(target.relative_to(root))==pending,(f,t)
completion={'status':'passed','checkedAt':datetime.now(timezone.utc).isoformat(),'documents':docs,'protectedFileCount':len(b['protectedFiles']),'ownerProofSha256':sha(out/'owner-proof.json'),'issueSha256':sha(out/'issue.json'),'reviews':reviews,'authorSession':sessions[0],'items':audit['itemAssessment'],'navigation':next(x['detail'] for x in audit['checks'] if x['name']=='github_entry_within_two_clicks'),'scripts':{str(p.relative_to(root)):sha(p) for p in Path(__file__).parent.glob('*.py')},'scope':proof['scope'],'runtimeScope':proof['scope'],'sourceHashes':proof['sourceHashes'],'archiveManifestSha256':proof['archiveManifestSha256']}
write(out/'completion-audit.json',completion)
receipt={'status':'accepted','issue':3,'issueUrl':'https://github.com/tacogips/database-analysis/issues/3','acceptedAt':datetime.now(timezone.utc).isoformat(),'documents':docs,'ownerProofSha256':sha(out/'owner-proof.json'),'completionAuditSha256':sha(out/'completion-audit.json'),'reviews':reviews,'historicalReceipt':b['oldDocumentSnapshot'],'scope':proof['scope'],'sourceHashes':proof['sourceHashes'],'meaning':'Current five docs plus guarded lab accepted for these exact hashes; CONTEXT_INFO-RLS remains HOLD before candidate COUNT. Functional emulated lab is not production RLS COUNT/cancel, HA or SLO certification.'}
write(out/'acceptance.json',receipt)
print(json.dumps({'status':'accepted','sessions':sessions,'documents':len(docs),'protectedFiles':len(b['protectedFiles'])}))
