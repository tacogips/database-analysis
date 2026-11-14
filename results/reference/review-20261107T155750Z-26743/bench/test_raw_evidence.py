"""Pure telemetry serialization regressions; no DB imports or connections."""
import copy
import gzip
import hashlib
import json
from pathlib import Path
import tempfile
from raw_evidence import CHUNK_BYTES, read_samples, write_samples


def fails(fn):
    try:
        fn()
    except (ValueError, FileNotFoundError):
        return
    raise AssertionError('invalid evidence accepted')


with tempfile.TemporaryDirectory(prefix='raw-evidence-test-') as directory:
    root = Path(directory)
    rows = [[1700000000.1234567 + i, 'write', i / 7, 'queued' if i % 2 else 'ok', None, 0.0000001, 2**63+i]
            for i in range(1000)] + [{'t': 1.23456789, 'name': '改行\n文字', 'value': -2**63}]
    manifest = write_samples(root, 'synthetic', 'service-0', iter(rows), chunk_bytes=1024)
    tagged=write_samples(root,'synthetic','service-0',rows,chunk_bytes=1024,clock={'format':'test-clock'},row_format='arrival-tuple-v2')
    assert tagged['clock']=={'format':'test-clock'} and tagged['row_format']=='arrival-tuple-v2'
    assert tagged['chunks']==manifest['chunks'] and list(read_samples(root,tagged))==rows
    assert len(manifest['chunks']) > 1
    assert list(read_samples(root, manifest)) == rows
    assert manifest['samples'] == sum(chunk['samples'] for chunk in manifest['chunks']) == len(rows)
    for chunk in manifest['chunks']:
        packed = (root/chunk['path']).read_bytes()
        raw = gzip.decompress(packed)
        assert len(raw) <= 1024 and len(raw) == chunk['uncompressed_bytes']
        assert hashlib.sha256(raw).hexdigest() == chunk['uncompressed_sha256']
        assert hashlib.sha256(packed).hexdigest() == chunk['sha256'] and len(packed) == chunk['bytes']
        assert packed[:2] == b'\x1f\x8b'
    # Repeat representation, not a new measurement: byte-identical gzip/hash.
    assert write_samples(root, 'synthetic', 'service-0', rows, 1024) == manifest
    empty = write_samples(root, 'synthetic', 'empty', [])
    assert empty['samples'] == 0 and empty['chunks'] == [] and list(read_samples(root, empty)) == []
    line = [1, None, 1.2345678901234567]
    exact = len((json.dumps(line, separators=(',', ':'))+'\n').encode())
    boundary = write_samples(root, 'synthetic', 'boundary', [line]*3, exact)
    assert len(boundary['chunks']) == 3 and list(read_samples(root, boundary)) == [line]*3
    fails(lambda: write_samples(root, 'synthetic', 'oversize', [line], exact-1))
    fails(lambda: write_samples(root, '../outside', 'bad', [], 1024))
    fails(lambda: write_samples(root, 'synthetic', 'bad', [], CHUNK_BYTES+1))
    for field in ['samples', 'uncompressed_bytes', 'bytes']:
        wrong = copy.deepcopy(manifest)
        wrong['chunks'][0][field] += 1
        fails(lambda: list(read_samples(root, wrong)))
    for field in ['sha256', 'uncompressed_sha256']:
        wrong = copy.deepcopy(manifest)
        wrong['chunks'][0][field] = '0'*64
        fails(lambda: list(read_samples(root, wrong)))
    wrong = copy.deepcopy(manifest)
    wrong['samples'] += 1
    fails(lambda: list(read_samples(root, wrong)))
    wrong = copy.deepcopy(manifest)
    wrong['chunk_uncompressed_byte_cap'] = 1
    fails(lambda: list(read_samples(root, wrong)))
    path = root/manifest['chunks'][0]['path']
    path.write_bytes(path.read_bytes()+b'changed')
    fails(lambda: list(read_samples(root, manifest)))
    path.unlink()
    fails(lambda: list(read_samples(root, manifest)))
print(json.dumps(dict(db_access=False, lossless_numeric_roundtrip=True, standard_gzip_jsonl=True,
                      chunk_count_hash_and_byte_bounds=True, deterministic_representation=True,
                      boundary_and_empty_streams=True, corruption_and_missing_chunk_rejected=True)))
