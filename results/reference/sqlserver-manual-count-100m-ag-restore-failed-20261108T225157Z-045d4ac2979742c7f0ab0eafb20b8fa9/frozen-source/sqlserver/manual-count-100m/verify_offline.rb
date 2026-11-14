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
  %w[Program.cs Fixture.cs State.cs].each do |file|
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

check('exact latest frozen input binding and all four manual SQL branches') do
  bindings = JSON.parse(File.read(File.join(__dir__, 'input-bindings.json')))
  baseline = File.join(ManualLab::ROOT, 'results/reviews/sqlserver-manual-count-100m/baseline.json')
  expect(Digest::SHA256.file(baseline).hexdigest == bindings.fetch('baselineSha256'))
  manual = File.read(File.join(__dir__, 'manual/sql-server.md'))
  expect(Digest::SHA256.hexdigest(manual) == bindings.fetch('manualSha256'))
  receipt = JSON.parse(File.read(File.join(__dir__, 'manual/source.json')))
  expect(receipt['sha256'] == bindings['manualSha256'])
  expect(bindings['manualSha256'] == '9a66b40420396c8481d55b68de3781fe3712993beaeca9f711e6ba0605922682')
  expect(bindings['promptSha256'] == '8ef7bec1a3c82c7d9d972f74c66d6d0fa965485e395b2b7ae86512d7b6aa86c5')
  snapshot = File.join(ManualLab::ROOT, receipt.fetch('snapshot'))
  expect(File.binread(snapshot) == File.binread(File.join(__dir__, 'manual/sql-server.md')))
  %w[a b c d].each do |branch|
    sql = File.read(File.join(__dir__, 'sql', "branch-#{branch}.sql"))
    expect(sql.end_with?((branch == 'd' ? ManualModel::D_SQL : ManualModel::PLAIN_SQL) + "\n"))
  end
  protected = JSON.parse(File.read(baseline)).fetch('protectedFiles')
  { 'ManualCount100m.csproj' => 'CountIssue3.csproj', 'NuGet.Config' => 'NuGet.Config', 'packages.lock.json' => 'packages.lock.json' }.each do |local, original|
    expect(Digest::SHA256.file(File.join(__dir__, local)).hexdigest == protected.fetch('sqlserver/issue3/' + original), 'copied locked dependency graph drift')
  end
  blocks = manual.scan(/```sql\n(.*?)```/m).flatten
  { 'inventory' => 0, 'guard' => 1, 'role' => 2, 'showplan' => 3, 'indexes' => 4, 'pages' => 5, 'branch-a' => 6, 'branch-b' => 7, 'branch-c' => 8, 'branch-d' => 9 }.each do |name, index|
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
  runtime_cells=f.scan(/new\("([ABCD]-[^\"]+)","([ABCD])","([^\"]+)",(\d)\)/).map { |c,r,i,n| [c,r,i,Integer(n)] }
  expect(runtime_cells == ManualModel::CELLS, 'runtime six cells differ from acceptance model')
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

def actual_state(route)
  { 'is_read_committed_snapshot_on' => route == 'B' ? 1 : 0, 'snapshot_isolation_state' => route == 'C' ? 1 : 0,
    'is_ag_primary' => route == 'A' ? 0 : nil, 'role_desc' => route == 'A' ? 'SECONDARY' : nil,
    'secondary_role_allow_connections_desc' => 'READ_ONLY', 'redo_queue_size' => 0,
    'synchronization_state_desc' => 'SYNCHRONIZED', 'synchronization_health_desc' => 'HEALTHY', 'active_user_writes' => 0 }
end
check('all eighteen slots/six cells + 1000 commit receipts; no incomplete success') do
  cold = { 'verified' => true, 'cached_target_pages' => 0, 'dirty_target_pages' => 0, 'method' => 'CHECKPOINT_then_DBCC' }
  sample = { 'completed' => true, 'completion_unknown' => false, 'error' => nil, 'value' => 100_000_000,
             'first_result_ns' => 500, 'full_completion_ns' => 600, 'cold_buffer_reset_verified' => true, 'cold_buffer_proof' => cold }
  cells = ManualModel::CELLS
  probe = { 'conditions' => cells.map { |c,r,i,_| { 'condition' => c, 'route' => r, 'index' => i, 'actual_state' => actual_state(r),
      'selected_sql' => r == 'D' ? ManualModel::D_SQL : ManualModel::PLAIN_SQL, 'structure_sha256' => 'a'*64 } },
    'samples' => cells.flat_map { |c,r,idx,iso| (1..3).map { |i| sample.merge('condition' => c, 'sample' => i, 'route' => r, 'index' => idx,
      'id' => { 'Isolation' => iso }, 'sql' => r == 'D' ? ManualModel::D_SQL : ManualModel::PLAIN_SQL,
      'precision' => r == 'D' ? 'observed_rows_quiet_match_only' : 'exact_rows_static_fixture') } },
    'ag_setup' => 'actual_secondary_readable_manual_full_log_restore_same_committed_fixture',
    'secondary_gate' => { 'secondary_rosetta_verified' => true },
    'preload_monitor_smoke' => { 'status' => 'passed', 'accepted' => true, 'errors' => [], 'before_load' => true,
      'open_session_release_verified' => true, 'session_disposed_release_verified' => true, 'admin_disposed_release_verified' => true },
    'commits' => 1000.times.map { |i| { 'batch' => i, 'committed_total' => (i + 1) * 100_000,
      'raw' => [[{ 'committed_rows' => 100_000, 'first_id' => i * 100_000 + 1, 'last_id' => (i + 1) * 100_000, 'transaction_count' => 0 }]] } } }
  expect(ManualModel.complete?(probe))
  [:missing_sample, :missing_condition, :pending, :timeout_after_value, :eof_after_value, :unknown, :wrong_value, :missing_receipt,
   :rollback, :inverted_time, :missing_cold_reset, :dirty_target, :warm_target, :reset_failure, :zero_time,
   :wrong_c_identity, :wrong_rcsi, :wrong_snapshot, :d_missing_hint, :d_exact_alias, :false_a_role, :false_a_local_role, :null_a_role,
   :a_not_readable, :a_redo, :a_not_rosetta, :a_backup_failed, :changed_composite,
   :missing_smoke, :failed_smoke, :smoke_not_accepted, :smoke_error, :smoke_after_load,
   :smoke_open_not_idle, :smoke_session_survived, :smoke_admin_survived].each do |failure|
    bad = clone_json(probe)
    case failure
    when :missing_smoke then bad.delete('preload_monitor_smoke')
    when :failed_smoke then bad['preload_monitor_smoke']['status'] = 'failed'
    when :smoke_not_accepted then bad['preload_monitor_smoke']['accepted'] = false
    when :smoke_error then bad['preload_monitor_smoke']['errors'] = ['monitor SQL failed']
    when :smoke_after_load then bad['preload_monitor_smoke']['before_load'] = false
    when :smoke_open_not_idle then bad['preload_monitor_smoke']['open_session_release_verified'] = false
    when :smoke_session_survived then bad['preload_monitor_smoke']['session_disposed_release_verified'] = false
    when :smoke_admin_survived then bad['preload_monitor_smoke']['admin_disposed_release_verified'] = false
    when :missing_sample then bad['samples'].pop
    when :missing_condition then bad['conditions'].pop
    when :pending then bad['samples'][17]['completed'] = false
    when :timeout_after_value then bad['samples'][0]['error'] = 'TimeoutException'
    when :eof_after_value then bad['samples'][0]['error'] = 'unexpected EOF during command completion'
    when :unknown then bad['samples'][0]['completion_unknown'] = true
    when :wrong_value then bad['samples'][0]['value'] -= 1
    when :missing_receipt then bad['commits'].pop
    when :rollback then bad['commits'][999]['raw'][0][0]['transaction_count'] = 1
    when :inverted_time then bad['samples'][0]['full_completion_ns'] = 499
    when :missing_cold_reset then bad['samples'][0].delete('cold_buffer_proof')
    when :dirty_target then bad['samples'][17]['cold_buffer_proof']['dirty_target_pages'] = 1
    when :warm_target then bad['samples'][17]['cold_buffer_proof']['cached_target_pages'] = 1
    when :reset_failure then bad['samples'][0]['cold_buffer_proof']['verified'] = false
    when :zero_time then bad['samples'][0]['first_result_ns'] = 0
    when :wrong_c_identity then bad['samples'][9]['id']['Isolation'] = 2
    when :wrong_rcsi then bad['conditions'][3]['actual_state']['is_read_committed_snapshot_on'] = 1
    when :wrong_snapshot then bad['conditions'][4]['actual_state']['snapshot_isolation_state'] = 1
    when :d_missing_hint then bad['samples'][12]['sql'] = ManualModel::PLAIN_SQL
    when :d_exact_alias then bad['samples'][12]['precision'] = 'exact_rows_static_fixture'
    when :false_a_role then bad['conditions'][5]['actual_state']['is_ag_primary'] = 1
    when :false_a_local_role then bad['conditions'][5]['actual_state']['role_desc'] = 'PRIMARY'
    when :null_a_role then bad['conditions'][5]['actual_state']['is_ag_primary'] = nil
    when :a_not_readable then bad['conditions'][5]['actual_state']['secondary_role_allow_connections_desc'] = 'NO'
    when :a_redo then bad['conditions'][5]['actual_state']['redo_queue_size'] = 1
    when :a_not_rosetta then bad['secondary_gate']['secondary_rosetta_verified'] = false
    when :a_backup_failed then bad.delete('ag_setup')
    when :changed_composite then bad['conditions'][5]['structure_sha256'] = 'b'*64
    end
    expect(!ManualModel.complete?(bad), "accepted #{failure}")
  end
end

check('C applies prefix before immutable identity, D exact hint plan, no route fallback') do
  program = File.read(File.join(__dir__, 'Program.cs'))
  guard = program[program.index('static async Task<Identity> Guard')...program.index('const string Match')]
  expect(guard.index('manual-prefix-') < guard.index('manual-guard-final'))
  expect(guard.index('manual-guard-final') < guard.index('var id=Id'))
  expect(guard.include?('id.Isolation==cell.Isolation') && guard.include?('id==expected'))
  expect(program.include?('batches[1]=cell.Sql') && program.include?('new SqlCommand(cell.Sql,c)'))
  expect(program.include?('Connection(secondary:cell.Secondary)') && program.include?('id.Isolation'))
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

check('two instance stage/mount/ID/gate guards refuse incomplete or foreign resources') do
  Dir.mktmpdir('manual100m-topology-') do |base|
    base = File.realpath(base); owner = 'a'*32
    directory = File.join(base, owner); Dir.mkdir(directory)
    stage = File.join(directory, 'stage'); Dir.mkdir(stage)
    File.write(File.join(directory, 'owner.json'), JSON.generate({owner: owner, stage: stage, uid: Process.uid}))
    %w[primary secondary shared primary/data primary/log primary/secrets secondary/data secondary/log secondary/secrets].each { |n| Dir.mkdir(File.join(stage,n)) }
    expect(ManualModel.topology_stage!(base,owner,stage))
    File.rename(File.join(stage,'secondary'),File.join(stage,'foreign'))
    File.symlink(File.join(stage,'foreign'),File.join(stage,'secondary'))
    refuses { ManualModel.topology_stage!(base,owner,stage) }
    expect(File.directory?(File.join(stage,'foreign')))
  end
  obj = { 'Mounts' => [{ 'Type'=>'bind','RW'=>true,'Destination'=>'/var/opt/mssql','Source'=>'/owned/secondary' },
    { 'Type'=>'bind','RW'=>true,'Destination'=>'/backup','Source'=>'/owned/shared' }],
    'Config'=> { 'User'=>'501:0', 'Hostname'=>'countsecondary', 'Env'=>['MSSQL_ENABLE_HADR=1'] },
    'HostConfig'=> { 'PortBindings'=>{},'Memory'=>4*ManualModel::GIB,'NanoCpus'=>3_000_000_000 } }
  expect(ManualModel.server_mounts!(obj,'/owned','secondary',501))
  [[:source,'/foreign'],[:volume,'volume'],[:readonly,false],[:extra,nil],[:port,nil],[:root,nil]].each do |failure,_|
    bad=clone_json(obj)
    case failure
    when :source then bad['Mounts'][0]['Source']='/owned/primary'
    when :volume then bad['Mounts'][0]['Type']='volume'
    when :readonly then bad['Mounts'][1]['RW']=false
    when :extra then bad['Mounts'] << clone_json(bad['Mounts'][0])
    when :port then bad['HostConfig']['PortBindings']={'1433/tcp'=>[{}]}
    when :root then bad['Config']['User']='0:0'
    end
    refuses { ManualModel.server_mounts!(bad,'/owned','secondary',501) }
  end
  gate={'owner'=>'mine','secondary_container_id'=>'pinned','secondary_rosetta_verified'=>true,'mounts_verified'=>true,'free_bytes'=>70*ManualModel::GIB}
  expect(ManualModel.secondary_gate!(gate,'mine','pinned'))
  %w[owner secondary_container_id secondary_rosetta_verified mounts_verified free_bytes].each do |key|
    bad=clone_json(gate); bad.delete(key)
    refuses { ManualModel.secondary_gate!(bad,'mine','pinned') }
  end
end

check('both real started servers require exit zero, including start-CLI failure cases') do
  good={'Running'=>false,'ExitCode'=>0,'OOMKilled'=>false,'StartedAt'=>'2026-11-09T00:00:00Z'}
  expect(ManualModel.terminal_server!(good,true))
  [good.merge('Running'=>true),good.merge('ExitCode'=>5),good.merge('ExitCode'=>137),good.merge('OOMKilled'=>true)].each do |bad|
    refuses { ManualModel.terminal_server!(bad,true) }
    refuses { ManualModel.terminal_server!(bad,false) }
  end
  expect(ManualModel.terminal_server!({'Running'=>false,'ExitCode'=>0,'OOMKilled'=>false,'StartedAt'=>'0001-01-01T00:00:00Z'},false))
end

check('actual cleanup caller never removes unowned/replaced resources or their stage') do
  # Exercise the real cleanup method with a command recorder, without Docker or real data.
  fake=Class.new(ManualLab) do
    attr_reader :removals
    def initialize(directory)
      @run=directory; @cli=['fake-docker']; @preflight=true; @mutations=true; @owner='a'*32
      @ids={}; @result={}; @deadline=now+120; @capacity_path=directory; @client_image='owned-test'
      @stage_created=true; @stage=File.join(directory,'stage'); Dir.mkdir(@stage); @base=directory
      @removals=[]
    end
    def docker(*args, **_opts)
      @removals << args if args.include?('rm')
      return ['',{}] if args[0]=='image'
      ['exists',{}]
    end
    def inspect(kind, id, **_opts)
      { 'Id'=>id, 'Config'=> { 'Labels'=>{ ManualModel::LABEL=>'foreign' } }, 'Labels'=>{ ManualModel::LABEL=>'foreign' } }
    end
    def inventory(_kind, **_opts) = [{ 'Id'=>'foreign' }]
    def host_free = 100 * ManualModel::GIB
    def vm(*) = ''
  end
  Dir.mktmpdir('manual100m-cleanup-') do |directory|
    runner=fake.new(directory); runner.cleanup
    expect(runner.removals.empty?, 'cleanup attempted removal of unowned resource')
    expect(File.directory?(File.join(directory,'stage')), 'cleanup deleted stage after ownership failure')
    result=runner.instance_variable_get('@result')
    expect(!result['cleanup_errors'].empty? && result['cleanup']=='failed_or_unknown_preserved_evidence')
  end
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
  expect(!(program + runtime_sql).match?(/SET\s+CONTEXT_INFO|sp_set_session_context|WITH\s*\(\s*(READPAST)|FOR\s+UPDATE|SET\s+SHOWPLAN_ALL/i))
  expect(!program.include?('ClientConnectionId') && !program.match?(/\bKILL\s+[\d@]/i))
  expect(program.include?('Pooling=false') && program.include?('ConnectRetryCount=0'))
  expect(program.include?('ApplicationIntent.ReadOnly') && program.include?('tcp:secondary,1433'))
  expect(program.include?('command.Cancel()') && program.include?('count.WaitAsync(TimeSpan.FromSeconds(15))'))
  expect(program.include?('Sql("branch-"+cell.Route.ToLowerInvariant())') && program.include?('finally {\n            using var cleanup='.gsub('\\n', "\n")))
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
  expect(lab.include?('ManualModel.sqlserver_pids!(top)') && lab.include?('ManualModel.sqlserver_pids!(confirmed_top)'))
  expect(lab.include?('ManualModel.owned!(\'container\', server, @owner, server_id)'))
  expect(lab.include?('final_sqlserver_rosetta_verified_after_18_counts'))
  header = "PID COMMAND COMMAND\n"
  shell = "101 launch_sqlservr /bin/bash /opt/mssql/bin/launch_sqlservr.sh /opt/mssql/bin/sqlservr\n"
  wrapper = "102 bash /bin/bash -c exec /opt/mssql/bin/sqlservr\n"
  actual = "103 sqlservr /opt/mssql/bin/sqlservr\n"
  expect(ManualModel.sqlserver_pids!(header + shell + wrapper).empty?)
  expect(ManualModel.sqlserver_pids!(header + shell + wrapper + actual) == ['103'])
  expect(ManualModel.sqlserver_pids!(header + "106 sqlservr /mnt/lima-rosetta/rosetta /opt/mssql/bin/sqlservr\n") == ['106'])
  expect(ManualModel.sqlserver_pids!(header + "107 sqlservr /mnt/lima-rosetta/rosetta /bin/bash /opt/mssql/bin/sqlservr\n").empty?)
  expect(ManualModel.sqlserver_pids!(header + "104 sqlservr /bin/bash /opt/mssql/bin/sqlservr\n").empty?)
  expect(ManualModel.sqlserver_pids!(header + "105 qemu-x86_64 /opt/mssql/bin/sqlservr\n").empty?)
  refuses { ManualModel.sqlserver_pids!(header + "bad sqlservr /opt/mssql/bin/sqlservr\n") }
  require 'yaml'
  services = YAML.safe_load(File.read(File.join(__dir__, 'compose.yaml')), aliases: true).fetch('services')
  entrypoint = services.fetch('server').fetch('entrypoint')
  expect(entrypoint == services.fetch('secondary').fetch('entrypoint'))
  expect(entrypoint.size == 3 && entrypoint.first(2) == ['/bin/bash', '-c'])
  raw_script = entrypoint.fetch(2)
  expect(!raw_script.match?(/(?<!\$)\$(?!\$)/), 'inline shell dollar missing Compose escaping')
  script = raw_script.gsub('$$', '$')
  expect(script.include?('set -m') && script.include?("trap 'on_signal TERM' TERM") && script.include?("trap 'on_signal INT' INT"))
  expect(script.include?("/opt/mssql/bin/sqlservr &\n        sql_pid=$!".gsub('        ', '')))
  expect(script.include?('replay_signals "$sql_pid"') && script.include?('replay_signals "$setup_pid"'))
  expect(script.include?('wait -f "$target"') && script.include?('epoch == signal_epoch'))
  expect(script.include?('source /opt/mssql/bin/init_custom_setup.sh') && script.include?('/opt/mssql/bin/run_custom_setup.sh &'))
  expect(script.include?('/opt/mssql/bin/permissions_check.sh') && script.include?('standarddeveloper) export MSSQL_PID=DeveloperStandard'))
  expect(script.include?('enterprisedeveloper) export MSSQL_PID=Developer'))
  expect(script.include?('kill -s TERM -- "$sql_pid"') && script.include?('exit "$sql_status"') && script.include?('exit "$setup_status"'))
  expect(lab.include?('stop_seconds = name == "#{PREFIX}-client" ? 20 : 60'))
  expect(lab.include?('seconds: stop_seconds + 5') && lab.include?('@cleanup_deadline = now + 240'))
  # Lexical/YAML checks only: actual signal delivery and SQL exit remain owner fresh-all checks.
  expect(lab.include?("'--no-recreate', '--no-build', '--pull', 'never'"))
  expect(lab.include?('Process.waitpid2') && lab.include?("Process.kill('KILL', -pid)"))
  expect(ManualLab::SOURCE.all? { |f| File.file?(File.join(__dir__, f)) })
  expect(Dir.glob(File.join(__dir__, 'sql/*.sql')).all? { |p| ManualLab::SOURCE.include?('sql/'+File.basename(p)) })
  expect(Dir.glob(File.join(__dir__, '*.cs')).all? { |p| ManualLab::SOURCE.include?(File.basename(p)) })
  expect(!ManualLab::SOURCE.include?('../../docs/sqlserver-manual-count-100m.md'))
  expect(Dir.glob(File.join(__dir__, '*.rb')).all? { |p| ManualLab::SOURCE.include?(File.basename(p)) })
  expect(File.read(File.join(__dir__, '.dockerignore')).include?('!packages.lock.json'))
end

check('actual AG manual seeding, finite setup, unchanged composite and cold buffer proof source') do
  state=File.read(File.join(__dir__,'State.cs')); program=File.read(File.join(__dir__,'Program.cs'))
  primary=File.read(File.join(__dir__,'sql/ag-primary-setup.sql'))
  secondary=File.read(File.join(__dir__,'sql/ag-secondary-setup.sql'))
  expect(primary.include?('CLUSTER_TYPE=NONE') && primary.scan('SEEDING_MODE=MANUAL').size==2 && primary.scan('FAILOVER_MODE=MANUAL').size==2)
  expect(primary.scan('WITH COMPRESSION,CHECKSUM').size==2 && !primary.include?('AUTOMATIC'))
  expect(secondary.scan('WITH NORECOVERY,CHECKSUM').size==2 && secondary.include?('SET HADR AVAILABILITY GROUP=ManualCountAg'))
  expect(state.include?('TimeSpan.FromHours(2)') && state.include?('TimeSpan.FromSeconds(120)'))
  expect(state.include?('"SECONDARY"') && state.include?('"SYNCHRONIZED"') && state.include?('"redo_queue_size"'))
  expect(state.include?('NoDatabaseSessions') && !state.include?('ROLLBACK IMMEDIATE'))
  expect(program.include?('secondary-dirty-proof') && program.include?('CompositeStructure==structure'))
  expect(program.index('if(cell.Secondary) {
            await PrimaryStatic'.gsub('\n',"\n")))
  expect(!state.include?('INSERT INTO') && !state.include?('FixtureSetup()'))
  lab=File.read(File.join(__dir__,'lab.rb'))
  expect(lab.include?('["#{PREFIX}-client", "#{PREFIX}-secondary", PREFIX]'))
  expect(lab.include?("ManualModel.terminal_server!"))
  expect(lab.include?('ManualModel.topology_stage!') && lab.include?('ManualModel.server_mounts!'))
  expect(lab.include?('secondary gate/probe mismatch') && lab.include?('readiness: 90'))
end

check('SQL bit states serialized as explicit numeric 0/1, NULL retained for non-AG') do
  state_source = File.read(File.join(__dir__, 'State.cs'))
  expect(state_source.include?('CONVERT(int,d.is_read_committed_snapshot_on) is_read_committed_snapshot_on'))
  expect(state_source.include?('CONVERT(int,sys.fn_hadr_is_primary_replica(d.name)) is_ag_primary'))
  expect(!state_source.include?('ISNULL(sys.fn_hadr_is_primary_replica(d.name),0)'))
end
check('manual A priority accepts readable secondary with overlapping RCSI/SNAPSHOT flags, rejects unknown') do
  state = { 'rcsi'=>1, 'snapshot'=>1, 'ag_primary'=>0, 'local_role'=>'SECONDARY', 'read_allow'=>'READ_ONLY',
    'redo_queue'=>0, 'synchronized'=>true, 'healthy'=>true, 'active_writes'=>0, 'secondary_rosetta'=>true }
  expect(ManualModel.route_valid?(state,'A',2,ManualModel::PLAIN_SQL))
  expect(!ManualModel.route_valid?(state.merge('rcsi'=>nil),'A',2,ManualModel::PLAIN_SQL))
  expect(!ManualModel.route_valid?(state.merge('local_role'=>'PRIMARY'),'A',2,ManualModel::PLAIN_SQL))
end
check('guest-side deletion receives only exact guarded nonce directory; foreign marker refuses before VM') do
  Dir.mktmpdir('manual100m-guest-delete-') do |base|
    base=File.realpath(base); owner='c'*32; directory=File.join(base,owner); Dir.mkdir(directory)
    stage=File.join(directory,'stage'); Dir.mkdir(stage)
    %w[primary secondary shared primary/data primary/log primary/secrets secondary/data secondary/log secondary/secrets].each { |part| Dir.mkdir(File.join(stage,part)) }
    marker=File.join(directory,'owner.json'); File.write(marker,JSON.generate({'owner'=>owner,'stage'=>stage,'uid'=>Process.uid}))
    runner=ManualLab.new
    {'@base'=>base,'@owner'=>owner,'@stage'=>stage,'@result'=>{}}.each { |k,v| runner.instance_variable_set(k,v) }
    events=[]
    runner.define_singleton_method(:vm) do |script,name,**opts|
      events << [script,name,opts]
      expect(script=='sudo -n -- rm -rf -- '+Shellwords.escape(directory))
      FileUtils.remove_entry_secure(directory) # Stub the exact guest effect, never invoke VM/Docker.
      ''
    end
    File.write(marker,JSON.generate({'owner'=>'d'*32,'stage'=>stage,'uid'=>Process.uid}))
    refuses { runner.remove_owned_stage_via_guest }; expect(events.empty? && File.directory?(stage))
    File.write(marker,JSON.generate({'owner'=>owner,'stage'=>stage,'uid'=>Process.uid}))
    runner.remove_owned_stage_via_guest
    expect(events.size==1 && events[0][1]=='remove-owned-stage' && events[0][2][:cleanup]==true && !File.exist?(directory))
    expect(runner.instance_variable_get('@result')['stage_absent']==true)
  end
end
check('two-node memory budget is uniform 2048MB in fixture, configuration and actual-state guard') do
  compose=File.read(File.join(__dir__,'compose.yaml'))
  program=File.read(File.join(__dir__,'Program.cs'))
  state=File.read(File.join(__dir__,'State.cs'))
  expect(compose.scan('MSSQL_MEMORY_LIMIT_MB: "2048"').size==2)
  expect(program.include?("'max server memory (MB)',2048") && state.include?("'max server memory (MB)',2048"))
  expect(state.include?('Convert.ToInt32(r["value_in_use"])==2048'))
end
check('Rosetta startup fork retries full proof without extending readiness or accepting QEMU') do
  fake = lambda do |changing: false, interpreter: '/mnt/lima-rosetta/rosetta', running: true|
    lab = ManualLab.allocate
    lab.instance_variable_set(:@ids, { ['container', ManualLab::PREFIX] => 'pinned' })
    lab.instance_variable_set(:@owner, 'owner')
    lab.instance_variable_set(:@deadline, 100.0)
    lab.instance_variable_set(:@result, {})
    lab.instance_variable_set(:@clock, 0.0)
    lab.instance_variable_set(:@tops, 0)
    lab.define_singleton_method(:now) { @clock }
    lab.define_singleton_method(:sleep) { |seconds| @clock += seconds }
    lab.define_singleton_method(:vm) { |*args, **kwargs| interpreter }
    lab.define_singleton_method(:docker) do |*args, **kwargs|
      if args.first == 'container'
        [JSON.generate([{ 'Id' => 'pinned', 'Config' => { 'Labels' => { ManualModel::LABEL => 'owner' } },
                          'State' => { 'Running' => running } }]), nil]
      else
        @tops += 1
        extra = changing ? @tops.even? : @tops >= 2
        ["PID COMMAND COMMAND\n101 sqlservr /opt/mssql/bin/sqlservr\n" +
         (extra ? "102 sqlservr /opt/mssql/bin/sqlservr\n" : ''), nil]
      end
    end
    lab
  end
  lab = fake.call
  lab.runtime_rosetta(readiness: 3)
  proof = lab.instance_variable_get(:@result).fetch('actual_sqlserver_rosetta').fetch(ManualLab::PREFIX)
  expect(lab.instance_variable_get(:@tops) == 4)
  expect(proof.fetch('pid_executables').keys.sort == %w[101 102])
  expect(proof['docker_top'] == proof['docker_top_confirmed'])
  unstable = fake.call(changing: true)
  refuses { unstable.runtime_rosetta(readiness: 3) }
  expect(unstable.instance_variable_get(:@clock) == 3)
  expect(unstable.instance_variable_get(:@result).empty?)
  refuses { fake.call(interpreter: '/usr/bin/qemu-x86_64').runtime_rosetta(readiness: 3) }
  refuses { fake.call(running: false).runtime_rosetta(readiness: 3) }
end
puts JSON.generate({ status: 'passed_offline_only', checks: CHECKS.size, names: CHECKS,
                     excluded: ['Docker', 'DB', 'C# compilation', 'downloads', 'measurements'], exit_status: 0 })
