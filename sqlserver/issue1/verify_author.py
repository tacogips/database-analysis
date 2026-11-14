#!/usr/bin/env python3
"""Offline author checks only. Does not invoke Docker, DB, a workflow, or an agent."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
LOG = HERE / 'author-verification.log'
REPORT = HERE / 'author-verification.json'
checks = []
exit_status = 1

def check(name, condition, detail=None):
    checks.append({'name': name, 'passed': bool(condition), 'detail': detail})
    log.write(json.dumps(checks[-1], ensure_ascii=False) + '\n')
    log.flush()
    if not condition:
        raise AssertionError(name)

def command(args):
    p = subprocess.run(args, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    log.write('argv=' + json.dumps(args) + '\n' + p.stdout + '\nexit_status=' + str(p.returncode) + '\n')
    log.flush()
    check('command_exit', p.returncode == 0, args)
    return p.stdout

with LOG.open('w') as log:
    try:
        ruby = Path.home() / '.local/share/mise/installs/ruby/4.0.9/bin/ruby'
        check('ruby_4_available', ruby.is_file())
        command([str(ruby), '-c', str(HERE / 'lab.rb')])
        config = json.loads(command([str(ruby), '-rjson', '-ryaml', '-e',
                                    'puts JSON.generate(YAML.load_file(ARGV[0]))', str(HERE / 'compose.yaml')]))
        check('fixed_project', config['name'] == 'lab-sqlserver-issue1')
        server, client = config['services']['server'], config['services']['client']
        check('pinned_server', server['image'] == 'mcr.microsoft.com/mssql/server:2025-latest@sha256:245f6b1c8903edae52cb20fa31419cefcde1a49938bb8c3447099011fc871ca3')
        check('fixed_runtime_names', server['container_name'] == 'lab-sqlserver-issue1' and
              client['container_name'] == 'lab-sqlserver-issue1-client' and
              config['volumes']['data']['name'] == 'lab-sqlserver-issue1-data' and
              config['networks']['runtime']['name'] == 'lab-sqlserver-issue1-network')
        check('internal_no_published_ports', config['networks']['runtime']['internal'] is True and
              all('ports' not in s and 'network_mode' not in s and 'privileged' not in s
                  for s in [server, client]))
        check('resource_limits', server['mem_limit'] == '4g' and server['cpus'] == 2 and client['mem_limit'] == '2g' and
              server['environment']['MSSQL_MEMORY_LIMIT_MB'] == '3072')
        project = ET.parse(HERE / 'CountIssue1.csproj')
        check('net10_locked_restore_enabled', project.find('.//TargetFramework').text == 'net10.0' and
              project.find('.//RestorePackagesWithLockFile').text == 'true')
        check('sqlclient_exact_version', project.find('.//PackageReference').attrib ==
              {'Include': 'Microsoft.Data.SqlClient', 'Version': '[7.1.1]'})
        nuget = ET.parse(HERE / 'NuGet.Config')
        check('nuget_official_only', [e.attrib['value'] for e in nuget.findall('.//packageSources/add')] ==
              ['https://api.nuget.org/v3/index.json'])
        examples = json.loads((HERE / 'runbook-example-inputs.json').read_text())
        check('fiction_marked', examples['classification'] == 'illustrative_not_measured')
        for key in ['independent_copy', 'dedicated_replica']:
            e = examples[key]
            m = e['monitor']
            total = sum(m[k] for k in ['sample_s','aggregate_s','delivery_s','decision_s','cancel_s','rollback_s','jitter_s'])
            check(key + '_response_budget_arithmetic', total == m['total_response_budget_s'])
            a = e['arrivals']
            check(key + '_arrival_accounting', a['completed'] + a['pending'] == a['planned'])
        a = examples['independent_copy']['thresholds']
        check('fiction_space_headroom_arithmetic', (a['free_space_stop_GiB'] - a['free_space_floor_GiB']) /
              a['adverse_growth_upper_GiB_per_s'] == a['space_headroom_s'])
        baseline = json.loads((ROOT / 'results/reviews/issue-1/baseline.json').read_text())
        changed = [f for f, h in baseline['protectedFiles'].items()
                   if not (ROOT / f).is_file() or hashlib.sha256((ROOT / f).read_bytes()).hexdigest() != h]
        check('old_measurement_and_acceptance_hashes_unchanged', not changed,
              {'protected_count': len(baseline['protectedFiles']), 'changed': changed})
        allowed_modified = set(baseline['mutableDocuments'])
        command(['git', 'diff', '--check'])
        tracked = command(['git', 'diff', '--name-only']).splitlines()
        check('tracked_write_scope', set(tracked) <= allowed_modified, tracked)
        # Local link targets may intentionally point at the future owner-authored current receipt only.
        missing = []
        for file in [ROOT / p for p in baseline['mutableDocuments']] + [ROOT / 'docs/guidelines/operations-runbook.md', HERE / 'README.md']:
            for target in re.findall(r'\]\(([^)]+)\)', file.read_text()):
                if '://' in target or target.startswith('#'):
                    continue
                path = target.split('#')[0]
                if path and not (file.parent / path).exists() and not path.endswith('results/reviews/issue-1/acceptance.json'):
                    missing.append([str(file.relative_to(ROOT)), target])
        check('local_document_links_except_owner_receipt', not missing, missing)
        check('current_receipt_in_all_guides', all('results/reviews/issue-1/acceptance.json' in (ROOT / p).read_text()
              for p in baseline['mutableDocuments'] + ['docs/guidelines/operations-runbook.md']))
        compiler = {k: shutil.which(k) for k in ['dotnet', 'csc', 'mcs']}
        log.write('host_compilers=' + json.dumps(compiler) + '\n')
        exit_status = 0
    except Exception as error:
        log.write(type(error).__name__ + ': ' + str(error) + '\n')
    finally:
        log.write('AUTHOR_STATIC_FINAL_EXIT_STATUS=' + str(exit_status) + '\n')

files = sorted([p for p in HERE.iterdir() if p.is_file() and p.name not in
                ['author-verification.log', 'author-verification.json', '.lab.lock']] +
               [ROOT / 'docs/README.md', ROOT / 'docs/guidelines/mysql.md', ROOT / 'docs/guidelines/sql-server.md',
                ROOT / 'docs/guidelines/operations-runbook.md'])
report = {'status': 'static_checks_passed' if exit_status == 0 else 'static_checks_failed',
          'exit_status': exit_status, 'complete_log_path': str(LOG),
          'complete_log_sha256': hashlib.sha256(LOG.read_bytes()).hexdigest(), 'checks': checks,
          'source_hashes': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
          'not_executed': ['Docker', 'DB', 'measurements', 'C# compile: host compiler unavailable',
                           'runtime attention/rollback/KILL/stats', 'final owner audit', 'separate final Sol two-role review'],
          'meaning': 'Offline structure/integrity/arithmetic only; not runtime success or Issue acceptance.'}
REPORT.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
print(json.dumps({'report': str(REPORT), 'exit_status': exit_status}))
sys.exit(exit_status)
