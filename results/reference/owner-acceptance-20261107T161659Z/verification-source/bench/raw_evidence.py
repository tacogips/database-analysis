"""Lossless post-collection JSONL/gzip storage for lab telemetry, never table data.

Paths in manifests are relative to the results directory. Each decompressed chunk
is bounded, including newline bytes; a sample is never split or discarded.
"""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

CHUNK_BYTES = 8 * 1024 * 1024


def _path(root, relative):
    root = Path(root).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError("raw evidence path escapes results")
    return path


def write_samples(root, dataset, stream, samples, chunk_bytes=CHUNK_BYTES, clock=None, row_format=None):
    """Serialize only after workload collection/analysis; preserve every sample."""
    if chunk_bytes <= 0 or chunk_bytes > CHUNK_BYTES:
        raise ValueError("chunk cap must be positive and at most CHUNK_BYTES")
    if any(not re.fullmatch(r"[A-Za-z0-9_.-]+", value) or value in ('.', '..')
           for value in (dataset, stream)):
        raise ValueError("invalid dataset/stream name")
    chunks, lines, size, total = [], [], 0, 0

    def flush():
        nonlocal lines, size
        if not lines:
            return
        raw = b''.join(lines)
        relative = f"raw/{dataset}/{stream}/{len(chunks):05d}.jsonl.gz"
        path = _path(root, relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.chunk-')
        try:
            with os.fdopen(fd, 'wb') as out:
                with gzip.GzipFile(filename='', fileobj=out, mode='wb', mtime=0) as zipped:
                    zipped.write(raw)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        chunks.append(dict(path=relative, samples=len(lines), uncompressed_bytes=len(raw),
                           uncompressed_sha256=hashlib.sha256(raw).hexdigest(),
                           bytes=path.stat().st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
        lines, size = [], 0

    for sample in samples:
        line = (json.dumps(sample, ensure_ascii=False, separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')
        if len(line) > chunk_bytes:
            raise ValueError("single sample exceeds chunk cap; refusing to discard/truncate it")
        if size + len(line) > chunk_bytes:
            flush()
        lines.append(line)
        size += len(line)
        total += 1
    flush()
    manifest = dict(format='jsonl+gzip-v1', samples=total, chunk_uncompressed_byte_cap=chunk_bytes, chunks=chunks)
    if clock is not None:
        manifest.update(clock=clock, row_format=row_format)
    return manifest


def read_samples(root, manifest):
    """Verify sizes, both hashes and counts before yielding each bounded chunk."""
    if manifest['format'] != 'jsonl+gzip-v1':
        raise ValueError('unknown format')
    cap = manifest['chunk_uncompressed_byte_cap']
    if not 0 < cap <= CHUNK_BYTES:
        raise ValueError('invalid chunk cap')
    total = 0
    for chunk in manifest['chunks']:
        path = _path(root, chunk['path'])
        packed = path.read_bytes()
        if len(packed) != chunk['bytes'] or hashlib.sha256(packed).hexdigest() != chunk['sha256']:
            raise ValueError('compressed chunk size/hash mismatch')
        with gzip.open(path, 'rb') as zipped:
            raw = zipped.read(cap + 1)
        if (len(raw) > cap or len(raw) != chunk['uncompressed_bytes'] or
                hashlib.sha256(raw).hexdigest() != chunk['uncompressed_sha256']):
            raise ValueError('uncompressed chunk bound/size/hash mismatch')
        if not raw.endswith(b'\n'):
            raise ValueError('missing JSONL newline')
        rows = [json.loads(line) for line in raw.splitlines()]
        if len(rows) != chunk['samples']:
            raise ValueError('chunk sample count mismatch')
        total += len(rows)
        yield from rows
    if total != manifest['samples']:
        raise ValueError('stream sample count mismatch')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('result', type=Path)
    parser.add_argument('--results-root', type=Path, default=Path('results'))
    parser.add_argument('--emit', action='store_true', help='emit all verified samples as JSONL')
    args = parser.parse_args()
    windows = json.loads(args.result.read_text())['sections']['windows']
    manifests = windows['raw_service_samples'] + [windows['raw_server_samples']]
    counts = []
    for stream, manifest in enumerate(manifests):
        count = 0
        for row in read_samples(args.results_root, manifest):
            count += 1
            if args.emit:
                print(json.dumps(dict(stream=stream, sample=row), ensure_ascii=False))
        counts.append(count)
    if not args.emit:
        print(json.dumps(dict(verified=True, samples_by_stream=counts)))


if __name__ == '__main__':
    main()
