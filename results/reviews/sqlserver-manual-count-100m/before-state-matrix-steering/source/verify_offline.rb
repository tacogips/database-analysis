# frozen_string_literal: true
# Offline stdlib only. This file never invokes Docker, SQL, a compiler, a package manager or the network.
require 'tmpdir'
require 'open3'
require 'rbconfig'
require 'digest'
require_relative 'lab'

abort 'Ruby 4.0.9 required for offline syntax checks' unless RUBY_VERSION == '4.0.9'
CHECKS = []
def check(name)
  yield
  CHECKS << name
  puts "PASS #{name}"
end
def expect(value, message = 'assertion failed') = (raise(message) unless value)
def refuses
  raised = false
  begin
    yield
  rescue RuntimeError, URI::InvalidURIError
    raised = true
  end
  expect(raised, 'unsafe case was not refused')
end
def clone_json(obj) = JSON.parse(JSON.generate(obj))

check('Ruby stdlib syntax: all runtime Ruby files') do
  %w[lab.rb model.rb verify_offline.rb].each do |name|
    out, status = Open3.capture2e(RbConfig.ruby, '-c', File.join(__dir__, name))
    expect(status.success?, out)
  end
end

# A lexical delimiter check, explicitly NOT a C# compiler or type/API check.
check('C# lexical string/comment/delimiter structure') do
  %w[Program.cs Fixture.cs].each do |file|
    source = File.read(File.join(__dir__, file))
    i = 0; stack = []
    while i < source.size
      rest = source[i..]
      if rest.start_with?('//')
        i = source.index("\n", i) || source.size
      elsif rest.start_with?('/*')
        ending = source.index('*/', i + 2); expect(ending, 'unclosed C# comment'); i = ending + 2
      elsif rest.start_with?('"""')
        ending = source.index('"""', i + 3); expect(ending, 'unclosed raw string'); i = ending + 3
      elsif source[i] == '"' || source[i] == "'"
        quote = source[i]; i += 1
        loop do
          expect(i < source.size, 'unclosed C# string')
          if source[i] == '\\'
            i += 2
          elsif source[i] == quote
            i += 1; break
          else
            i += 1
          end
        end
      else
        c = source[i]
        stack << c if '([{'.include?(c)
        if ')]}'.include?(c)
          expect(stack.pop == { ')' => '(', ']' => '[', '}' => '{' }.fetch(c), "C# delimiter at #{file}:#{i}")
        end
        i += 1
      end
    end
    expect(stack.empty?, 'C# delimiters left open')
    expect(!source.include?('!=='), 'non-C# comparison token')
  end
end

check('exact frozen input binding and manual SQL, B-only dispatch') do
  bindings = JSON.parse(File.read(File.join(__dir__, 'input-bindings.json')))
  baseline = File.join(ManualLab::ROOT, 'results/reviews/sqlserver-manual-count-100m/baseline.json')
  expect(Digest::SHA256.file(baseline).hexdigest == bindings.fetch('baselineSha256'))
  manual = File.read(File.join(__dir__, 'manual/sql-server.md'))
  expect(Digest::SHA256.hexdigest(manual) == bindings.fetch('manualSha256'))
  receipt = JSON.parse(File.read(File.join(__dir__, 'manual/source.json')))
  expect(receipt['sha256'] == bindings['manualSha256'])
  protected = JSON.parse(File.read(baseline)).fetch('protectedFiles')
  { 'ManualCount100m.csproj' => 'CountIssue3.csproj', 'NuGet.Config' => 'NuGet.Config', 'packages.lock.json' => 'packages.lock.json' }.each do |local, original|
    expect(Digest::SHA256.file(File.join(__dir__, local)).hexdigest == protected.fetch('sqlserver/issue3/' + original), 'copied locked dependency graph drift')
  end
  blocks = manual.scan(/```sql\n(.*?)```/m).flatten
  { 'inventory' => 0, 'guard' => 1, 'showplan' => 3, 'indexes' => 4, 'pages' => 5, 'branch-b' => 7 }.each do |name, index|
    expect(File.read(File.join(__dir__, 'sql', name + '.sql')) == blocks.fetch(index).gsub("N'db'", "N'ManualCount100m'"), "manual drift: #{name}")
  end
  batches = File.read(File.join(__dir__, 'sql/showplan.sql')).strip.split(/^GO\s*$/).map(&:strip).reject(&:empty?)
  expect(batches == ['SET SHOWPLAN_XML ON;', 'SELECT COUNT_BIG(*) AS exact_rows FROM [dbo].[Target];', 'SET SHOWPLAN_XML OFF;'])
  b = File.read(File.join(__dir__, 'sql/branch-b.sql'))
  expect(b.end_with?(batches[1] + "\n"))
  expect(b[0...-(batches[1].size + 1)].include?('SET TRANSACTION ISOLATION LEVEL READ COMMITTED;'))
end

check('100M x 100 model and overflow-safe SQL value expressions') do
  f = File.read(File.join(__dir__, 'Fixture.cs'))
  expect(f.include?('Rows = 100_000_000') && f.include?('Columns = 100, BatchRows = 100_000, Batches = 1000'))
  expect(f.include?('Enumerable.Range(1, 99)') && f.include?('Enumerable.Range(4, 96)'))
  expect(f.include?('id BIGINT NOT NULL CONSTRAINT PK_Target PRIMARY KEY CLUSTERED'))
  expect(f.include?('CAST(a.n AS BIGINT)') && f.include?('CAST(e.n AS BIGINT)*10000 + CAST('))
  expect(f.include?('(long)batch * BatchRows') && f.include?('DECLARE @inserted BIGINT = @@ROWCOUNT;\nCOMMIT TRANSACTION;'))
  %w[104729 99991 1000003].each { |n| expect(f.include?(n)) }
  expect(8 + 99 * 4 == 404)
  digits = (0..9).to_a
  seq = digits.product(digits, digits, digits, digits).map { |a, b, c, d, e| a + b * 10 + c * 100 + d * 1000 + e * 10_000 + 1 }
  expect(seq.size == 100_000 && seq.uniq.size == 100_000 && seq.min == 1 && seq.max == 100_000)
  receipts = 1000.times.map { |i| [i * 100_000 + 1, (i + 1) * 100_000] }
  expect(receipts.first.first == 1 && receipts.last.last == 100_000_000)
  expect(receipts.each_cons(2).all? { |a, b| a.last + 1 == b.first })
  [1, 99_999, 100_000, 799_999, 800_000, 99_999_999, 100_000_000].each do |id|
    a = id * 104729 % 100_000
    b = id / 100_000 % 10 < 8 ? 0 : id / 100_000 % 1000 + 10
    c = id * 99991 % 1_000_003
    expect([a, b, c].all? { |v| v >= 0 && v <= 2**31 - 1 })
    expect(id * 104729 < 2**63)
  end
  expect(100_000_000 * 104729 > 2**31 - 1, 'model must expose int multiplication overflow')
end

check('all nine outcomes + commit truth required; EOF/timeout after value fail') do
  sample = { 'completed' => true, 'completion_unknown' => false, 'error' => nil, 'value' => 100_000_000,
             'first_result_ns' => 500, 'full_completion_ns' => 600, 'cold_buffer_reset_verified' => true }
  probe = { 'conditions' => ManualModel::CONDITIONS.map { |c| { 'condition' => c } },
    'samples' => ManualModel::CONDITIONS.flat_map { |c| (1..3).map { |i| sample.merge('condition' => c, 'sample' => i) } },
    'commits' => 1000.times.map { |i| { 'batch' => i, 'committed_total' => (i + 1) * 100_000,
      'raw' => [[{ 'committed_rows' => 100_000, 'first_id' => i * 100_000 + 1, 'last_id' => (i + 1) * 100_000, 'transaction_count' => 0 }]] } } }
  expect(ManualModel.complete?(probe))
  [:missing_sample, :missing_condition, :pending, :timeout_after_value, :eof_after_value, :unknown, :wrong_value, :missing_receipt, :rollback, :inverted_time, :missing_cold_reset, :zero_time].each do |failure|
    bad = clone_json(probe)
    case failure
    when :missing_sample then bad['samples'].pop
    when :missing_condition then bad['conditions'].pop
    when :pending then bad['samples'][0]['completed'] = false
    when :timeout_after_value then bad['samples'][0]['error'] = 'TimeoutException'
    when :eof_after_value then bad['samples'][0]['error'] = 'unexpected EOF during command completion'
    when :unknown then bad['samples'][0]['completion_unknown'] = true
    when :wrong_value then bad['samples'][0]['value'] -= 1
    when :missing_receipt then bad['commits'].pop
    when :rollback then bad['commits'][999]['raw'][0][0]['transaction_count'] = 1
    when :inverted_time then bad['samples'][0]['full_completion_ns'] = 499
    when :missing_cold_reset then bad['samples'][0]['cold_buffer_reset_verified'] = false
    when :zero_time then bad['samples'][0]['first_result_ns'] = 0
    end
    expect(!ManualModel.complete?(bad), "accepted #{failure}")
  end
end

check('ownership/marker/realpath/symlink refusal without touching real data') do
  Dir.mktmpdir('manual100m-model-') do |base|
    base = File.realpath(base)
    nonce = 'a' * 32; directory = File.join(base, nonce); Dir.mkdir(directory)
    stage = File.join(directory, 'stage'); Dir.mkdir(stage)
    marker = File.join(directory, 'owner.json')
    good = { 'owner' => nonce, 'stage' => stage, 'uid' => Process.uid }
    File.write(marker, JSON.generate(good))
    expect(ManualModel.stage_owned!(base, nonce, stage) == stage)
    File.write(marker, JSON.generate(good.merge('owner' => 'b' * 32)))
    refuses { ManualModel.stage_owned!(base, nonce, stage) }; expect(File.directory?(stage))
    File.write(marker, JSON.generate(good))
    File.rename(stage, stage + '-real'); File.symlink(stage + '-real', stage)
    refuses { ManualModel.stage_owned!(base, nonce, stage) }; expect(File.directory?(stage + '-real'))
    refuses { ManualModel.stage_owned!(base, nonce, base) }
    refuses { ManualModel.stage_owned!(base, '../other', stage) }
  end
  obj = { 'Id' => 'pinned', 'Config' => { 'Labels' => { ManualModel::LABEL => 'mine' } } }
  expect(ManualModel.owned!('container', obj, 'mine', 'pinned'))
  refuses { ManualModel.owned!('container', obj, 'other', 'pinned') }
  refuses { ManualModel.owned!('container', obj, 'mine', 'replaced') }
end

check('endpoint isolation rejects TCP/SSH/remote Unix syntax') do
  expect(ManualModel.local_endpoint!('unix:///private/tmp/docker.sock') == '/private/tmp/docker.sock')
  ['tcp://localhost:2375', 'ssh://host', 'unix://remote/tmp/a', 'http://x', 'unix:///tmp/a?x=1'].each do |x|
    refuses { ManualModel.local_endpoint!(x) }
  end
end

check('foreground command terminal logs/exit and timeout process reaping') do
  Dir.mktmpdir('manual100m-process-') do |directory|
    runner = ManualLab.new
    { '@run' => directory, '@sequence' => 0, '@deadline' => Process.clock_gettime(Process::CLOCK_MONOTONIC) + 30,
      '@result' => { 'commands' => [] }, '@env' => { 'PATH' => ENV.fetch('PATH') } }.each { |k, v| runner.instance_variable_set(k, v) }
    out, rec = runner.execute([RbConfig.ruby, '-e', '$stdout.sync=true; puts "terminal marker"; warn "stderr marker"; exit 7'], check: false)
    expect(rec['exit_status'] == 7 && out.include?('terminal marker') && File.read(rec['stderr']).include?('stderr marker'))
    begin
      runner.execute([RbConfig.ruby, '-e', 'trap("TERM") {}; loop { sleep 0.1 }'], seconds: 0.15)
      raise 'timeout not raised'
    rescue RuntimeError => e
      expect(e.message.include?('deadline'))
    end
    last = runner.instance_variable_get('@result')['commands'].last
    expect(last['timed_out'] && last['terminated_by_controller'] && [137, 143].include?(last['exit_status']))
    expect(last['exit_status'] == 137) if last['forced_process_kill']
    expect(File.file?(last['stdout']) && File.file?(last['stderr']))
    begin
      Process.waitpid(-1, Process::WNOHANG)
      raise 'child not reaped'
    rescue Errno::ECHILD
      # Exact expected lifecycle postcondition.
    end
  end
end

check('scope, dependency graph, timer and Rosetta lifecycle source constraints') do
  program = File.read(File.join(__dir__, 'Program.cs'))
  runtime_sql = Dir.glob(File.join(__dir__, 'sql/*.sql')).map { |p| File.read(p) }.join("\n")
  expect(!(program + runtime_sql).match?(/SET\s+CONTEXT_INFO|sp_set_session_context|WITH\s*\(\s*(NOLOCK|READPAST)|FOR\s+UPDATE|SET\s+SHOWPLAN_ALL/i))
  expect(!program.include?('ClientConnectionId') && !program.match?(/\bKILL\s+[\d@]/i))
  expect(program.include?('Pooling=false') && program.include?('ConnectRetryCount=0'))
  expect(program.include?('command.Cancel()') && program.include?('count.WaitAsync(TimeSpan.FromSeconds(15))'))
  expect(program.include?('Sql("branch-b")') && program.include?('finally {\n            using var cleanup='.gsub('\\n', "\n")))
  expect(program.include?('firstNs=Ns()-start') && program.include?('fullNs=Ns()-start'))
  expect(program.include?('status="pending"') && program.include?('completion_unknown=unknown'))
  expect(program.include?('ReadAsync(countBudget.Token)') && program.include?('NextResultAsync(countBudget.Token)'))
  lock = JSON.parse(File.read(File.join(__dir__, 'packages.lock.json')))
  graph = lock.fetch('dependencies').fetch('net10.0')
  expect(graph.fetch('Microsoft.Data.SqlClient').fetch('resolved') == '7.1.1')
  expect(graph.fetch('System.Security.Cryptography.Pkcs').fetch('resolved') == '10.0.14')
  expect(File.read(File.join(__dir__, 'Dockerfile')).include?('dotnet restore --locked-mode'))
  lab = File.read(File.join(__dir__, 'lab.rb'))
  expect(lab.include?('docker(\'top\', server_id') && lab.include?('readlink /proc/#{pid}/exe'))
  expect(lab.include?('binfmt_misc/rosetta') && lab.include?("exe.include?('rosetta')"))
  expect(lab.include?("'--no-recreate', '--no-build', '--pull', 'never'"))
  expect(lab.include?('Process.waitpid2') && lab.include?("Process.kill('KILL', -pid)"))
  expect(ManualLab::SOURCE.all? { |f| File.file?(File.join(__dir__, f)) })
  expect(!ManualLab::SOURCE.include?('../../docs/sqlserver-manual-count-100m.md'))
  expect(Dir.glob(File.join(__dir__, '*.rb')).all? { |p| ManualLab::SOURCE.include?(File.basename(p)) })
  expect(File.read(File.join(__dir__, '.dockerignore')).include?('!packages.lock.json'))
end

puts JSON.generate({ status: 'passed_offline_only', checks: CHECKS.size, names: CHECKS,
                     excluded: ['Docker', 'DB', 'C# compilation', 'downloads', 'measurements'], exit_status: 0 })
