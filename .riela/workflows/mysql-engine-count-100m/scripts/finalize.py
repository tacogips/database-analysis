"""Bind two successful independent final reviews to the actual audited source/report."""
from pathlib import Path
from datetime import datetime,timezone
import json,hashlib
root=Path(__file__).resolve().parents[4];out=root/'results/reviews/mysql-engine-count-100m'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
read=lambda p:json.loads(p.read_text())
proof=read(out/'owner-proof.json');audit=read(out/'owner-audit.json')
assert audit['status']=='passed' and proof['ownerAuditSha256']==sha(out/'owner-audit.json')
assert all(x['passed'] for x in audit['checks'])
for path,h in proof['sourceHashes'].items():assert sha(root/path)==h,path
for path,h in proof['documentHashes'].items():assert sha(root/path)==h,path
assert sha(root/proof['rawRun'])==proof['rawRunSha256']
manifest=read(root/proof['archiveManifest']);archive=(root/proof['archiveManifest']).parent
assert sha(root/proof['archiveManifest'])==proof['archiveManifestSha256']
for path,h in manifest['files'].items():assert sha(archive/path)==h,path
for path,h in read(out/'baseline.json')['protectedFiles'].items():assert sha(root/path)==h,path
reviews=[];sessions=[read(out/'author-launch.json')['sessionId']]
for role in ['engineer','validation']:
 launch=read(out/f'final-{role}-launch.json');review=read(out/f'final-{role}-result.json')
 assert launch['exitCode']==0 and launch['runtimeStatus']=='completed' and launch['model']=='gpt-6.1-sol'
 assert launch['inputs']['ownerProofSha256']==sha(out/'owner-proof.json')
 assert launch['inputs']['ownerAuditSha256']==sha(out/'owner-audit.json')
 assert review['accepted'] is True and review['decision']=='accepted',review
 assert review['documentHashes']==proof['documentHashes'],review.get('documentHashes')
 assert not review['requestedExperiments'],review['requestedExperiments']
 assert not any(x.get('severity','').lower() in ['high','middle','medium','critical'] for x in review['findings']),review['findings']
 node=read(root/launch['attempt']/'node.json');assert node['model']=='gpt-6.1-sol' and node['modelFreeze'] is True and node['sessionPolicy']['mode']=='new' and node['agentSandbox']=='read-only'
 sessions.append(launch['sessionId']);reviews.append({'role':role,'sessionId':launch['sessionId'],'launchSha256':sha(out/f'final-{role}-launch.json'),'resultSha256':sha(out/f'final-{role}-result.json'),'findings':review['findings']})
assert len(set(sessions))==3
receipt={'status':'accepted_four_conditions_twelve_counts','finalizedAt':datetime.now(timezone.utc).isoformat(),'ownerProofSha256':sha(out/'owner-proof.json'),'ownerAuditSha256':sha(out/'owner-audit.json'),'sourceHashes':proof['sourceHashes'],'documentHashes':proof['documentHashes'],'reviews':reviews,'precision':'100000000 all12samples','serviceBudget':'UNAVAILABLE_static_catalogue','cleanup':'passed_owned_runtime_and_hostdata_absent','gitCommitPush':'pending_separate_terminal_git_verification','sqlServer':'queued separate Rosetta experiment, not certified by this MySQL acceptance'}
(out/'acceptance.json').write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps({'status':receipt['status'],'sessions':sessions}))
