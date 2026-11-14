#!/usr/bin/env python3
"""Cache provenance must track indirect imports without invalidating unrelated scripts."""
import tempfile, json
from pathlib import Path
from dependencies import local_dependencies

with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)/'bench'
    (root/'requested').mkdir(parents=True)
    script = root/'requested'/'probe.py'
    script.write_text('import json, helper\n')
    (root/'helper.py').write_text('from nested import value\n')
    (root/'nested.py').write_text('value = 1\n')
    (root/'unrelated.py').write_text('value = 1\n')
    first = local_dependencies(script,root)
    (root/'unrelated.py').write_text('value = 2\n')
    assert first == local_dependencies(script,root)
    (root/'nested.py').write_text('value = 2\n')
    second = local_dependencies(script,root)
    assert first != second
    assert len(second)==3
    (root/'nested.py').write_text('not valid Python!!!\n')
    try:
        local_dependencies(script,root)
    except SyntaxError:
        pass
    else:
        raise AssertionError('syntax errors must not become valid cached dependencies')
print(json.dumps({'unrelated_change_preserves_cache':True,'indirect_import_change_invalidates_cache':True,'invalid_source_rejected':True}))
