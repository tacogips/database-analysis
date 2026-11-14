"""Owner's offline document/source/navigation audit. Does not launch DB/Docker/bench."""
from pathlib import Path
from datetime import datetime,timezone
from collections import deque
import hashlib,json,re,subprocess
root=Path(__file__).resolve().parents[4];out=root/'results/reviews/issue-2'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def read(p):return json.loads(p.read_text())
def write(p,x):p.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n')
b=read(out/'baseline.json');docs={f:sha(root/f) for f in b['mutableDocuments']};checks=[]
def check(n,ok,detail=None):
 checks.append({'name':n,'passed':bool(ok),'detail':detail});assert ok,n
check('all_preexisting_sources_and_evidence_unchanged',all(sha(root/f)==h for f,h in b['protectedFiles'].items()),len(b['protectedFiles']))
old=root/b['oldDocumentSnapshot'];m=read(old)
check('previous_documents_and_receipt_archived',all(sha(old.parent/f)==h for f,h in m['files'].items()),len(m['files']))
changed=subprocess.check_output(['git','diff','HEAD','--name-only'],text=True).splitlines()
check('tracked_mutation_scope',set(changed)<=set(docs),changed)
q=subprocess.run(['git','diff','--check'],capture_output=True,text=True);check('diff_whitespace',q.returncode==0,q.stdout+q.stderr)
missing=[];graph={};pending='results/reviews/issue-2/acceptance.json'
for f in docs:
 p=root/f;links=[]
 for target in re.findall(r'\]\(([^)]+)\)',p.read_text()):
  if '://' in target or target.startswith('#'):continue
  t=target.split('#')[0]
  if not t:continue
  path=(p.parent/t).resolve();rel=str(path.relative_to(root));links.append(rel)
  if not path.exists() and rel!=pending:missing.append([f,target])
 graph[f]=links
check('local_links_except_pending_new_receipt',not missing,missing)
dist={'README.md':0};queue=deque(['README.md'])
while queue:
 f=queue.popleft()
 for target in graph.get(f,[]):
  if target not in dist:dist[target]=dist[f]+1;queue.append(target)
targets=['docs/README.md','docs/guidelines/mysql.md','docs/guidelines/sql-server.md','docs/guidelines/operations-runbook.md',pending]
check('github_entry_within_two_clicks',all(dist.get(f,999)<=2 for f in targets),{f:dist.get(f) for f in targets})
check('current_receipt_link_in_each_document',all('results/reviews/issue-2/acceptance.json' in (root/f).read_text() for f in docs))
# Owner separately inspects the actual final prose and records concrete anchors below, not a SQL runtime claim.
report={'status':'passed_offline_owner_audit','checkedAt':datetime.now(timezone.utc).isoformat(),'documents':docs,'checks':checks,'navigationGraph':graph,'itemAssessment':{'1':'Root entry explicitly separates current five documents and new receipt from frozen historical lab guides/acceptance. Link graph verifies <=2 clicks.','2':'Owner reads replica data equivalence gate separately from freshness: source/channel/table generation, filters/history, complete seed/reseed provenance, skip/empty GTID/repair/local writes; unknown means this-copy-only and original exact request HOLD, without automatic production checksums.','3':'Owner reads topology/role/routing epoch and currency source, role invalidation cancellation/recovery/requalification, fresh gate per backend/session, HA stop coordination with safe failover priority, and independent restore fallback; no poll-only race-free claim.'},'notExecuted':['DB/Docker/bench','replication filter/drift experiment','HA promotion/network failure experiment'],'measurementMeaning':'documentation-only qualification and routing correction; no new counts, accuracy/performance claims or production SLO certification; existing benchmark source hashes unchanged'}
write(out/'owner-audit.json',report)
proof={'status':'owner_verified_documentation_only','documents':docs,'baselineSha256':sha(out/'baseline.json'),'protectedFileCount':len(b['protectedFiles']),'ownerAuditSha256':sha(out/'owner-audit.json'),'auditScriptSha256':sha(Path(__file__)),'oldSnapshotManifest':b['oldDocumentSnapshot'],'oldSnapshotManifestSha256':sha(old),'officialSourcesSha256':sha(out/'official-sources.json'),'historicalIssue1ProofSha256':sha(root/'results/reviews/issue-1/owner-proof.json'),'scope':'Only navigation and operator eligibility documentation changed. Existing all/runtime evidence remains historical; no benchmark rerun warranted by these source-unchanged, non-measurement changes. HA/lineage operational evidence is required from each service owner; missing => HOLD.'}
write(out/'owner-proof.json',proof);print(json.dumps({'status':proof['status'],'checks':len(checks),'protected':len(b['protectedFiles'])}))
