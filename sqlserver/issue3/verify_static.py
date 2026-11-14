#!/usr/bin/env python3
"""Offline author audit only. Never invokes Docker, dotnet, a DB or a workflow."""
import ast
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time
import traceback
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
LOG = HERE / 'author-verification.log'
REPORT = HERE / 'author-verification.json'
SOURCE = ['lab.rb', 'mise.toml', 'compose.yaml', 'Dockerfile', '.dockerignore',
          'CountIssue3.csproj', 'NuGet.Config', 'packages.lock.json', 'Program.cs', 'README.md', 'verify_static.py']
EXPECTED_BASELINE = 'f01ced311ed3f67f12d3c739c9cf1b22528194880e841f0c696f07611fb1e1ea'
EXPECTED_ISSUE = 'cbfd6402c7e52f5e7eedc862fdf143bf4fce725155ac888f6103d6926f540684'

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def sources():
    return {str((HERE / p).relative_to(ROOT)): sha(HERE / p) for p in SOURCE}

def check(ok, message):
    if not ok:
        raise AssertionError(message)
    print('PASS:', message, flush=True)

def run(argv):
    print('ARGV:', json.dumps(argv), flush=True)
    process = subprocess.run(argv, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, timeout=30, check=False)
    print(process.stdout, end='' if process.stdout.endswith('\n') else '\n', flush=True)
    print('EXIT_STATUS:', process.returncode, flush=True)
    check(process.returncode == 0, 'foreground command reached successful terminal exit')
    return process.stdout

def section(source, start, end):
    return source[source.index(start):source.index(end, source.index(start))]

def audit():
    baseline_path = ROOT / 'results/reviews/issue-3/baseline.json'
    issue_path = ROOT / 'results/reviews/issue-3/issue.json'
    check(sha(baseline_path) == EXPECTED_BASELINE and sha(issue_path) == EXPECTED_ISSUE,
          'authoritative baseline and issue hashes')
    baseline = json.loads(baseline_path.read_text())
    changed = [p for p, h in baseline['protectedFiles'].items() if sha(ROOT / p) != h]
    check(not changed, f'all {len(baseline["protectedFiles"])} protected files unchanged: {changed}')
    archive_path = ROOT / baseline['oldDocumentSnapshot']
    archive = json.loads(archive_path.read_text())
    for path, digest in archive['files'].items():
        check(sha(archive_path.parent / path) == digest, 'archived old document/receipt: ' + path)
    run(['git', 'diff', '--check'])
    status = run(['git', 'diff', '--name-only']).splitlines()
    check(set(status).issubset(set(baseline['mutableDocuments'])), 'tracked edits limited to exactly mutable document allowlist')
    lab = (HERE / 'lab.rb').read_text()
    listed = re.search(r'SOURCE = %w\[([^]]+)\]', lab).group(1).split()
    check(len(listed) == len(set(listed)) and set(listed) == set(SOURCE), 'complete duplicate-free runtime source list')
    actual = {p.name for p in HERE.iterdir() if p.is_file()} - {'author-verification.log', 'author-verification.json', '.lab.lock'}
    check(actual == set(SOURCE), 'all executable/build/config/README/lock sources covered; no stale example inputs/verifier')
    run(['ruby', '-c', str(HERE / 'lab.rb')])
    config = json.loads(run(['ruby', '-ryaml', '-rjson', '-e',
        'puts JSON.generate(YAML.safe_load(File.read(ARGV.fetch(0))))', str(HERE / 'compose.yaml')]))
    check(config['name'] == 'lab-sqlserver-issue3', 'Issue3 compose project')
    check(set(config['services']) == {'server', 'client'}, 'only owned server/client services')
    for name, service in config['services'].items():
        check(not any(x in service for x in ['ports', 'privileged', 'network_mode', 'env_file']), 'no published ports/privilege/host network/external config: ' + name)
        check(service['labels'] == {'count.issue3.owner': '${ISSUE3_OWNER:?controller only}'}, 'unique Issue3 owner label: ' + name)
    check(config['networks']['runtime']['internal'] is True, 'runtime network internal')
    check(config['services']['server']['volumes'] == ['data:/var/opt/mssql'] and
          'volumes' not in config['services']['client'], 'only owned volume; no host/socket mounts')
    check(config['services']['server']['image'].endswith('@sha256:245f6b1c8903edae52cb20fa31419cefcde1a49938bb8c3447099011fc871ca3') and
          config['services']['server']['platform'] == 'linux/amd64', 'fixed SQL2025 CU10 amd64 image')
    dockerfile = (HERE / 'Dockerfile').read_text()
    check('sdk:10.0@sha256:212fe4cb74627d5adf9817a4088906ae3edff1bd853bb27565d4b46731efe61e' in dockerfile and
          'dotnet restore --locked-mode' in dockerfile and 'CountIssue3.dll' in dockerfile, 'fixed SDK, locked restore, Issue3 DLL')
    check((HERE / 'packages.lock.json').read_bytes() == (ROOT / 'sqlserver/issue1/packages.lock.json').read_bytes(), 'proven dependency lock copied unchanged')
    lock = json.loads((HERE / 'packages.lock.json').read_text())['dependencies']['net10.0']
    project = ET.parse(HERE / 'CountIssue3.csproj').getroot()
    refs = {x.attrib['Include']: x.attrib['Version'] for x in project.findall('.//PackageReference')}
    check(refs == {'Microsoft.Data.SqlClient': '[7.1.1]', 'System.Security.Cryptography.Pkcs': '[10.0.14]'} and
          lock['Microsoft.Data.SqlClient']['resolved'] == '7.1.1' and lock['System.Security.Cryptography.Pkcs']['resolved'] == '10.0.14', 'exact direct SqlClient/Pkcs pins and lock')
    warnings = project.find('.//WarningsAsErrors').text
    check(all(f'NU190{i}' in warnings for i in range(1, 5)), 'NU190x audit warnings are build errors')
    ET.parse(HERE / 'NuGet.Config')
    mise = (HERE / 'mise.toml').read_text()
    check('[tools]' in mise and 'ruby = "4.0.9"' in mise and '[tasks.all]' in mise and
          'run = "mise exec -- ruby lab.rb all"' in mise, 'fixed mise entrypoint and Ruby pin (textual config check)')
    ast.parse(Path(__file__).read_text())
    check(all(x in lab for x in ["File::LOCK_EX | File::LOCK_NB", "uri.scheme == 'unix'", "File.socket?(uri.path)",
          "unsetenv_others: true", 'Process.waitpid2(pid)', "'--no-recreate'", "'count.issue3.owner'", "ensure\n",
          "'results/sqlserver/issue3/runs'", "'source_after'", 'CONTROLLER_FINAL_EXIT_STATUS=', 'cleanup incomplete client image']), 'owned local foreground/reaped lifecycle and terminal evidence paths retained')
    source = (HERE / 'Program.cs').read_text()
    initializer = section(source, '    static async Task<Identity> Initialize(', '    static async Task<long> GuardedCandidateCount(')
    wrapper = section(source, '    static async Task<long> GuardedCandidateCount(', '    static async Task<(Identity Identity, long Count)> PrepareAndCount(')
    entry = section(source, '    static async Task<(Identity Identity, long Count)> PrepareAndCount(', '    static async Task Preflight(')
    check(initializer.index('Approve(contract, target)') < initializer.index('c.OpenAsync') < initializer.index('SET CONTEXT_INFO'), 'Initialize gate precedes connection open/context SQL')
    check(wrapper.index('Approve(contract, target)') < wrapper.index('Leases.TryGetValue') < wrapper.index('counters.CandidateCountDispatches++') < wrapper.index('SELECT COUNT_BIG'), 'independent candidate gate + lease/identity guard before dispatch')
    check(entry.index('Approve(contract, target)') < entry.index('Initialize(') < entry.index('GuardedCandidateCount('), 'shared candidate preparation/count route')
    check(source.count('static async Task<Identity> Initialize(') == 1 and source.count('SELECT COUNT_BIG(*) FROM dbo.Target;') == 1,
          'one contract-required initializer and one raw Target COUNT inside guarded wrapper')
    check('ContextUsage Usage = ContextUsage.UNKNOWN' in source and 'contract == null || contract.Usage != ContextUsage.JOB_EXCLUSIVE' in source and
          'initialize once only; never retag' in source and 'lease.Contract != contract' in source, 'missing/unknown/business default deny and immutable approved lease')
    negative = section(source, '    static async Task RlsNegativeCase(', '    static async Task RlsHoldProbe(')
    check(all(x in negative for x in ['PrepareAndCount(owner, contract, CandidateTarget.RlsTarget, counters)',
          'GuardedCandidateCount(owner, contract, CandidateTarget.RlsTarget, counters)',
          'PrepareAndCount(owner, contract, CandidateTarget.Target, counters)',
          'GuardedCandidateCount(owner, contract, CandidateTarget.Target, counters)',
          'contractOnlyRefused && directContractOnlyRefused',
          'counters.TagWrites == 0', 'counters.CandidateCountDispatches == 0', 'Same(observerAfter, before)',
          'originalContext == contextAfter', 'BusinessContext(owner) == null', 'owner.Dispose()', 'PollRelease(observer, actual, txids, true)']), 'negative calls same real candidate route + direct wrapper; records zero dispatch, both identities and actual Dispose release')
    check('await GuardedKill' not in negative and 'Initialize(owner' not in negative,
          'negative path never overwrites business context or kills with invented expected tag')
    rls = section(source, '    static async Task RlsHoldProbe(', '    static async Task<List<Dictionary<string, object?>>> Settings(')
    check(all(x in rls for x in ['VALUES(1,1),(2,1),(3,2),(4,2),(5,2)', 'WITH SCHEMABINDING',
          'CONVERT(int,SUBSTRING(CONTEXT_INFO(),1,4))', 'STATE=ON', '(1,2L), (2,3L), (99,0L)',
          'SELECT COUNT_BIG(*) FROM dbo.RlsTarget;', 'fixture_control_count', 'new(ContextUsage.BUSINESS_USE), 1',
          'new(), null, 1', 'new(), 2, 1', 'null, null, 2']), 'tiny real CONTEXT_INFO RLS fixture/control and business/unknown/missing/later-init rejection cases')
    check(not re.search(r'(ALTER|DROP) SECURITY POLICY|SESSION_CONTEXT\(', source), 'no RLS policy alteration/disable or alternate remote SESSION_CONTEXT claim')
    old = (ROOT / 'sqlserver/issue1/Program.cs').read_text()
    for oldname, newname in [('issue1','issue3'), ('Issue1','Issue3')]:
        old = old.replace(oldname, newname)
    for start, end in [('    static async Task Preflight(', '    static async Task<State> Observe('),
                       ('    static async Task<State> Observe(', '    static async Task<string> GuardedKill('),
                       ('    static async Task PollRelease(', '    static async Task Fixture(')]:
        check(section(source, start, end) == section(old, start, end), 'proven ownership/observation/cleanup section preserved: ' + start.strip())
    check(all(x in source for x in ['q.Cancel();', 'name == "command_timeout" ? 1 : 16', 'new CancellationTokenSource(TimeSpan.FromSeconds(30))',
          'LoginTime = id.LoginTime.AddSeconds(-1)', 'ConnectionId = Guid.NewGuid()', 'wrong.Tag', 'await PositiveCase(admin, master, name)',
          'await RlsHoldProbe(admin)', 'await StatisticsProbe(admin)']), 'five functional cases, mismatch checks, independent cleanup and original stats diagnostic retained')
    for name in SOURCE:
        check(not re.search(r'issue1|Issue1|ISSUE1|issue-1', (HERE / name).read_text()) or name in ['README.md', 'verify_static.py'], 'runtime names migrated: ' + name)
    for path in baseline['mutableDocuments']:
        doc = (ROOT / path).read_text()
        check('results/reviews/issue-3/acceptance.json' in doc and 'issue-3-pre-fix-16cc9b3' in doc,
              'current receipt + historical archive routing: ' + path)
        check('作成予定' not in doc and 'results/reviews/issue-2/acceptance.json)' not in doc.replace('issue-3-pre-fix-16cc9b3/results/reviews/issue-2/acceptance.json)', ''),
              'no old current receipt/future acceptance phrasing: ' + path)
    print('LIMIT: textual/caller/config audit only; no C# compile, Docker, DB, measurement or runtime RLS assertion.', flush=True)

if __name__ == '__main__':
    started = time.monotonic()
    before = sources()
    previous = sys.stdout
    status = 1
    with LOG.open('w') as log:
        sys.stdout = log
        try:
            audit()
            check(before == sources(), 'source unchanged during offline checks')
            status = 0
        except Exception:
            traceback.print_exc(file=log)
        finally:
            print(f'AUTHOR_STATIC_FINAL_EXIT_STATUS={status}', flush=True)
            sys.stdout = previous
    evidence = {'status': 'passed_offline_only' if status == 0 else 'failed', 'exit_status': status,
                'complete_log': str(LOG.relative_to(ROOT)), 'log_sha256': sha(LOG),
                'elapsed_monotonic_seconds': time.monotonic() - started,
                'source_before': before, 'source_after': sources(),
                'document_hashes': {p: sha(ROOT / p) for p in json.loads((ROOT / 'results/reviews/issue-3/baseline.json').read_text())['mutableDocuments']},
                'not_run': ['C# build/restore', 'Docker', 'DB', 'measurements'],
                'pending': ['owner fixed all + semantic/source/cleanup audit', 'two independent final Sol reviews']}
    REPORT.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'exit_status': status, 'verification': str(REPORT.relative_to(ROOT)), 'complete_log': str(LOG.relative_to(ROOT))}))
    sys.exit(status)
