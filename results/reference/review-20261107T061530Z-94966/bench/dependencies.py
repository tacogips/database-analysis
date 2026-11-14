#!/usr/bin/env python3
"""Hash the static local Python import closure for each requested lab script.
External packages are covered separately by the runner's Dockerfile fingerprint.
No module is imported or executed and no database connection is made.
"""
import ast, hashlib, json, sys
from pathlib import Path


def local_dependencies(script, bench_root):
    bench_root = Path(bench_root).resolve()
    pending = [Path(script).resolve()]
    seen = set()
    while pending:
        path = pending.pop()
        if path in seen:
            continue
        path.relative_to(bench_root)  # refuse an import path outside the lab source
        seen.add(path)
        tree = ast.parse(path.read_bytes(), filename=str(path))
        for node in ast.walk(tree):
            names = []
            base = bench_root
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    base = path.parent
                    for _ in range(node.level-1):
                        base = base.parent
                if node.module:
                    names = [node.module]
                elif node.level:
                    names = [alias.name for alias in node.names]
            for name in names:
                candidate = base.joinpath(*name.split('.'))
                files = [candidate.with_suffix('.py'), candidate/'__init__.py']
                for dependency in files:
                    if dependency.is_file():
                        dependency.resolve().relative_to(bench_root)
                        pending.append(dependency.resolve())
    return {str(p.relative_to(bench_root.parent)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(seen)}


if __name__ == '__main__':
    root = Path(__file__).resolve().parent
    names = sys.argv[1:] or [p.stem for p in sorted((root/'requested').glob('*.py'))]
    print(json.dumps({name: local_dependencies(root/'requested'/f'{name}.py',root) for name in names},sort_keys=True))
