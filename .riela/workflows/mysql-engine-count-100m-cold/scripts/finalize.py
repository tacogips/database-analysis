"""Bind two successful independent final reviews to the actual audited source/report."""
from pathlib import Path
from datetime import datetime,timezone
import json,hashlib
root=Path(__file__).resolve().parents[4];out=root/'results/reviews/mysql-engine-count-100m-cold'
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
for path,h in proof['historicalManifestHashes'].items():
 historical_path=root/path;assert sha(historical_path)==h,path
 for name,digest in read(historical_path)['files'].items():assert sha(historical_path.parent/name)==digest,name
for path,h in read(out/'baseline.json')['protectedFiles'].items():assert sha(root/path)==h,path
for key,file in [('loadDurationAuthorLaunchSha256','load-duration-author-launch.json'),('loadDurationApplicationSha256','load-duration-owner-application.json'),('loadDurationStopAuditSha256','load-duration-stop-audit.json')]:assert proof[key]==sha(out/file)
correction=out/'before-cache-scope-correction/manifest.json'
assert proof['cacheScopeCorrectionManifestSha256']==sha(correction)
for name,h in read(correction)['files'].items():assert sha(correction.parent/name)==h,name
assert proof['physicalCacheStatus']==read(out/'host-deleted-descriptor-cleanup.json')['physicalCacheStatus']
reviews=[];sessions=[read(out/'author-launch.json')['sessionId'],read(out/'load-duration-author-launch.json')['sessionId']]
for role in ['engineer','validation']:
 launch=read(out/f'final-{role}-launch.json');review=read(out/f'final-{role}-result.json')
 assert launch['exitCode']==0 and launch['runtimeStatus']=='completed' and launch['model']=='gpt-6.1-sol'
 assert launch['inputs']['owner-proof.json']==sha(out/'owner-proof.json')
 assert launch['inputs']['owner-audit.json']==sha(out/'owner-audit.json')
 assert review['accepted'] is True and review['decision']=='accepted',review
 assert review['documentHashes']==proof['documentHashes'],review.get('documentHashes')
 assert not review['requestedExperiments'],review['requestedExperiments']
 assert not any(x.get('severity','').lower() in ['high','middle','medium','critical'] for x in review['findings']),review['findings']
 node=read(root/launch['attempt']/'node.json');assert node['model']=='gpt-6.1-sol' and node['modelFreeze'] is True and node['sessionPolicy']['mode']=='new' and node['agentSandbox']=='read-only'
 sessions.append(launch['sessionId']);reviews.append({'role':role,'sessionId':launch['sessionId'],'launchSha256':sha(out/f'final-{role}-launch.json'),'resultSha256':sha(out/f'final-{role}-result.json'),'findings':review['findings']})
assert len(set(sessions))==4
receipt={'status':'accepted_four_conditions_twelve_counts_cold_buffer','finalizedAt':datetime.now(timezone.utc).isoformat(),'ownerProofSha256':sha(out/'owner-proof.json'),'ownerAuditSha256':sha(out/'owner-audit.json'),'sourceHashes':proof['sourceHashes'],'documentHashes':proof['documentHashes'],'reviews':reviews,'precision':'100000000 all12samples','serviceBudget':'UNAVAILABLE_static_catalogue','cleanup':'passed_owned_runtime_and_hostdata_absent','gitCommitPush':'pending_separate_terminal_git_verification','coldBufferPolicy':'passed_all_12_fresh_process_boots_terminal_clean','tableRows':'all12 approximate values and scalar/full timings audited','sqlServer':'separate Rosetta A/B/C/D six-cell eighteen-count experiment, not certified by this MySQL acceptance'}
receipt['physicalCacheStatus']=proof['physicalCacheStatus']
receipt['cleanupMeaning']='Owned DB/runtime/paths absent; physical shared-VZ file cache separately disclosed, not claimed purged.'
(out/'acceptance.json').write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps({'status':receipt['status'],'sessions':sessions}))
