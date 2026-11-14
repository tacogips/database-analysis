"""Actual owner inventory against isolated missing, optional and cached evidence; no DB."""
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import tempfile

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('scope_audit', ROOT/'audit/audit-final-results.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)
scope = json.loads((ROOT/'bench/nonlocking-scope.json').read_text())

with tempfile.TemporaryDirectory(prefix='scope-audit-') as tmp:
    root = Path(tmp)
    (root/'bench/requested').mkdir(parents=True)
    (root/'results/requested').mkdir(parents=True)
    (root/'audit').mkdir()
    shutil.copyfile(ROOT/'audit/audit-arrival-windows.py', root/'audit/audit-arrival-windows.py')
    for name in ('experiments.py', 'count_shapes.py', 'lock_factors.py', 'nonlocking-scope.json'):
        shutil.copyfile(ROOT/'bench'/name, root/'bench'/name)
    for p in (ROOT/'bench/requested').iterdir():
        if p.is_file() and p.suffix in ('.py', '.versions', '.outputs', '.encrypted'):
            shutil.copyfile(p, root/'bench/requested'/p.name)
    shutil.copyfile(ROOT/'mysql-images.json', root/'mysql-images.json')
    audit.ROOT = root

    def report():
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            audit.main()
        return json.loads(out.getvalue())

    r = report()
    assert set(r['missing']['requested']) == {s['id'] for s in scope['requestedScripts']}
    assert set(r['missing']['impact']) == set(scope['baseImpactNames'])
    assert r['expected']['requestedProcesses'] == scope['requestedProcessCount']
    assert not r['scopeMetadataProblems'] and not r['summaryScopeMatchesCurrent']
    assert len(r['skippedByUserScope']['requested']) == len(scope['skippedRequestedScripts'])
    selected = scope['requestedScripts'][1]
    optional = scope['skippedRequestedScripts'][0]['id']
    artifact = root/selected['requiredOutputs'][0]
    artifact.write_text('{"complete":false}')
    sha = hashlib.sha256(artifact.read_bytes()).hexdigest()
    summary = dict(status='completed', scope_sha256=r['scope']['sha256'],
                   planned_scripts=[s['id'] for s in scope['requestedScripts']],
                   skipped_by_user_scope=scope['skippedRequestedScripts'],
                   ran=[dict(id=selected['id'], ok=True, reused=True,
                             runs=[dict(exit_code=0,version=v) for v in selected['versions']],
                             artifacts=[dict(path=selected['requiredOutputs'][0],sha256=sha,bytes=artifact.stat().st_size)]),
                        dict(id=optional,ok=False,runs=[dict(exit_code=1)])])
    summary_path = root/'results/requested/last_run.json'
    summary_path.write_text(json.dumps(summary))
    skipped_impact = scope['skippedBaseImpacts'][0]['name']
    (root/f'results/impact_{skipped_impact}.json').write_text('{"errors":["old error"]}')
    r = report()
    assert r['summaryScopeMatchesCurrent'] and r['summaryPlannedScriptsMatchCurrent'] and r['summarySkipInventoryMatchesCurrent']
    assert not r['unexpected']['requested'] and not r['unexpected']['impact']
    assert r['historicalOrOptionalPresentNotCredited'] == dict(impact=[skipped_impact],requested=[optional])
    assert optional not in r['requestedNotSuccessful'] and not r['requestedArtifactProblems']
    assert r['requestedReused'] == [selected['id']]
    assert selected['requiredOutputs'][0] in r['semanticFlagsToClassify']
    assert f'results/impact_{skipped_impact}.json' not in r['semanticFlagsToClassify']
    artifact.write_text('{}')
    assert report()['requestedArtifactProblems'], 'cached artifact mutation must remain visible'
    artifact.unlink()
    assert report()['requestedArtifactProblems'], 'missing cached artifact must remain visible'
    summary['scope_sha256'] = 'old-scope'
    summary['ran'][0]['runs'][0]['version'] = 'wrong-version'
    summary_path.write_text(json.dumps(summary))
    r = report()
    assert not r['summaryScopeMatchesCurrent'] and selected['id'] in r['requestedVersionMismatches']

print(json.dumps(dict(db_access=False,actual_owner_audit=True,selected_missing_only=True,
                     optional_evidence_uncredited=True,scope_provenance=True,version_mismatch=True,
                     cached_artifact_hash_size_and_presence=True)))
