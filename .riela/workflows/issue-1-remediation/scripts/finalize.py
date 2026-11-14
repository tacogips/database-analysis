"""Create a new Issue 1 receipt only after both independent final reviews accept exact bytes."""
from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,re
root=Path(__file__).resolve().parents[4];out=root/'results/reviews/issue-1'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def read(p):return json.loads(p.read_text())
def write(p,x):p.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n')
b=read(out/'baseline.json');proof=read(out/'owner-proof.json');audit=read(out/'owner-audit.json')
assert proof['status']=='owner_verified' and audit['status']=='passed'
assert proof['ownerAuditSha256']==sha(out/'owner-audit.json')
for f,h in {**b['protectedFiles'],**proof['sourceHashes']}.items():assert sha(root/f)==h,f
docs={f:sha(root/f) for f in ['docs/README.md','docs/guidelines/mysql.md','docs/guidelines/sql-server.md','docs/guidelines/operations-runbook.md']}
reviews={};sessions=[read(out/'author-result.json')['sessionId']]
for role in ['engineer','validation']:
 launch=read(out/f'final-{role}-launch.json');result=read(out/f'final-{role}-result.json')
 assert launch['exitCode']==0 and launch['runtimeStatus']=='completed' and launch['model']=='gpt-6.1-sol'
 assert result['accepted'] and result['decision']=='accepted' and result['documentHashes']==docs
 assert not result['requestedExperiments']
 assert not [f for f in result['findings'] if f.get('severity') in ['high','middle','medium']]
 assert launch['inputs']['documents']==docs and launch['inputs']['ownerProofSha256']==sha(out/'owner-proof.json')
 sessions.append(launch['sessionId']);attempt=root/launch['attempt'];manifest=read(attempt/'manifest.json')
 for f,h in manifest['files'].items():assert sha(attempt/f)==h,f
 node=read(attempt/'review.json');assert node['model']=='gpt-6.1-sol' and node['sessionPolicy']['mode']=='new' and node['agentSandbox']=='read-only'
 reviews[role]={'sessionId':launch['sessionId'],'launchSha256':sha(out/f'final-{role}-launch.json'),'resultSha256':sha(out/f'final-{role}-result.json'),'attemptManifest':str((attempt/'manifest.json').relative_to(root)),'manifestSha256':sha(attempt/'manifest.json')}
assert len(set(sessions))==3
archive=root/proof['runtimeArchive'];assert sha(archive/'manifest.json')==proof['archiveManifestSha256']
for f,h in read(archive/'manifest.json')['files'].items():assert sha(archive/f)==h,f
old=root/b['oldDocumentSnapshot'];old=old/'manifest.json' if old.is_dir() else old
m=read(old)
for f,h in m['files'].items():
 path=old.parent/f
 if isinstance(h,dict):h=h['sha256']
 assert sha(path)==h,str(path)
completion={'status':'passed','checkedAt':datetime.now(timezone.utc).isoformat(),'documents':docs,'protectedFileCount':len(b['protectedFiles']),'oldReceiptPreserved':True,'sourceHashes':proof['sourceHashes'],'ownerProofSha256':sha(out/'owner-proof.json'),'issueSha256':sha(out/'issue.json'),'reviews':reviews,'issueItems':{'1':'CONTEXT_INFO + full server identity, permissions, 2 connections and 3 mismatch refusals','2':'driver finally, fresh cleanup deadline, own rollback/guarded KILL, captured active TX IDs, unknown escalation','3':'existing settings and old OFF lab distinguished, 3 profiles/plans/stats/windows, no universal update claim','4':'fillable and 2 filled fictional examples, resource controls/observability/headroom/recovery, incomplete arrivals retained'},'scripts':{str(p.relative_to(root)):sha(p) for p in Path(__file__).parent.glob('*.py')},'limitations':proof['scope']}
write(out/'completion-audit.json',completion)
receipt={'status':'accepted','issue':1,'issueUrl':'https://github.com/tacogips/database-analysis/issues/1','acceptedAt':datetime.now(timezone.utc).isoformat(),'documents':docs,'sourceHashes':proof['sourceHashes'],'ownerProofSha256':sha(out/'owner-proof.json'),'completionAuditSha256':sha(out/'completion-audit.json'),'reviews':reviews,'historicalReceipt':'results/reference/issue-1-pre-fix-c6396cc/manifest.json','limits':proof['scope'],'meaning':'Issue1 corrections accepted for these exact bytes; not production SLO certification; changed source needs new dedicated all and new final reviews.'}
write(out/'acceptance.json',receipt)
for f in list(docs)+['sqlserver/issue1/README.md']:
 p=root/f
 for target in re.findall(r'\]\(([^)]+)\)',p.read_text()):
  if '://' not in target and not target.startswith('#'):assert (p.parent/target.split('#')[0]).exists(),(f,target)
print(json.dumps({'status':'accepted','sessions':sessions,'documents':len(docs),'protectedFiles':len(b['protectedFiles'])}))
