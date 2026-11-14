# frozen_string_literal: true
# No Docker, DB, network, image build, or benchmark invocation. Simulations are not measurements.
require_relative 'lab'
require 'tmpdir'
require 'stringio'
require 'yaml'
abort 'Ruby 4.0.9 required for offline verification' unless RUBY_VERSION == '4.0.9'
checks = []
assert = lambda do |name, &block|
  raise "FAILED: #{name}" unless block.call
  checks << name
  puts "PASS #{name}"
end
root = Engine100M::ROOT
baseline_path = File.join(root, 'results/reviews/mysql-engine-count-100m/baseline.json')
assert.call('authoritative baseline hash') { Digest::SHA256.file(baseline_path).hexdigest == '2a5d8ae6d31847aa6a900191d81efa078fe4a4ad06bd5e3c4a6b7a703b6bac5e' }
baseline = JSON.parse(File.read(baseline_path))
bad = baseline.fetch('protectedFiles').filter_map do |f, h|
  p = File.join(root, f)
  f unless File.file?(p) && Digest::SHA256.file(p).hexdigest == h
end
assert.call("all #{baseline.fetch('protectedFiles').length} protected files exact") { bad.empty? }
assert.call('two engines in fixed order') { Engine100M::ENGINES == %w[InnoDB MyISAM] }
assert.call('exactly four planned conditions with twelve preserved slots') do
  Engine100M::CONDITIONS == { 'InnoDB' => %w[innodb_primary_only innodb_secondary_present innodb_composite_secondary], 'MyISAM' => %w[myisam_primary_only] } &&
    Engine100M::CONDITIONS.values.flatten.sum { |id| Engine100M.condition_model(id)[:counts].length } == 12
end
assert.call('runtime source includes executable inputs and frozen manual; final report independently bound') do
  Engine100M::SOURCE == %w[lab.rb reference_flow.rb compose.yaml mise.toml README.md verify_offline.rb].map { |p| "mysql/engine-count-100m/#{p}" }.push('results/reviews/mysql-engine-count-100m/manual-sources/mysql-innodb.md', 'results/reviews/mysql-engine-count-100m/manual-sources/source.json')
end
assert.call('exact 100M geometry') { Engine100M::ROWS == 100_000_000 && Engine100M::BATCH == 100_000 && Engine100M::BATCHES == 1000 && Engine100M::BATCH * Engine100M::BATCHES == Engine100M::ROWS }
a = Engine100M.ddl('InnoDB'); b = Engine100M.ddl('MyISAM')
columns = a[/\((.*)\) ENGINE=/, 1].split(',')
assert.call('exact 100 columns, nonnull numeric PK only, 404 payload bytes') do
  columns == ['id BIGINT UNSIGNED NOT NULL PRIMARY KEY'] + (1..99).map { |i| format('c%03d INT NOT NULL', i) } && 8 + 99 * 4 == 404
end
assert.call('DDL differs only in ENGINE') { a.sub('ENGINE=InnoDB', 'ENGINE=MyISAM') == b }
assert.call('1000 nonoverlapping batches, 100 physical values and varied numeric records') do
  (0...1000).all? do |i|
    vals = Engine100M.values_for(i)
    vals.length == 100 && vals.first == "#{i * 100_000}+n" && vals.drop(4) == (4..99).map(&:to_s) &&
      Engine100M.batch_sql(i).include?("SELECT #{vals.join(',')} FROM lab.seq") && Engine100M.batch_sql(i).end_with?('ORDER BY n;SELECT ROW_COUNT();COMMIT;')
  end && (999 * 100_000 + 100_000 == 100_000_000) &&
    (1..100_000).map { |id| id * 104729 % 100_000 }.uniq.length == 100_000
end
assert.call('digits cross join produces exactly 1..100000') do
  values = (0..9).to_a.repeated_permutation(5).map { |d| 1 + d.each_with_index.sum { |x, i| x * 10**i } }.sort
  values == (1..100_000).to_a && Engine100M.seed.scan('CROSS JOIN').length == 4
end
source = File.read(File.join(__dir__, 'lab.rb'))
assert.call('only ordinary COUNT(*) query constant; no variant/count validation call') do
  source.scan(/\bCOUNT\([^)]*\)/) == ['COUNT(*)'] && Engine100M::COUNT_SQL == 'SELECT COUNT(*) FROM lab.count_target;' &&
    source.scan('@client.query(COUNT_SQL, seconds: 1200, scalar: true)').length == 1 && source.include?('condition[:counts].each do |sample|') && File.read(File.join(__dir__, 'reference_flow.rb')).include?("reference_query('EXPLAIN FORMAT=TRADITIONAL ' + COUNT_SQL)")
end
assert.call('foreground fixed all, no shell DSN/scale override, sanitized spawn') do
  source.include?("ARGV == ['all']") && source.include?('unsetenv_others: true') &&
    source.include?("File.join(Dir.home, '.docker', 'config.json')") && source.include?("uri.scheme == 'unix'") &&
    source.include?('File.socket?(uri.path)') && !source.match?(/ENV\[(?:'|")(?:DOCKER|COMPOSE|MYSQL|EC_|ROWS|HOST|DSN)/) &&
    !source.match?(/Process\.detach|nohup|setsid|\bsystem\(|\bexec\(/)
end
compose = File.read(File.join(__dir__, 'compose.yaml'))
parsed_compose = YAML.safe_load(compose)
assert.call('offline Compose YAML parses; only fixed mysql service and no named volumes/networks') do
  parsed_compose.keys == ['services'] && parsed_compose['services'].keys == ['mysql'] &&
    parsed_compose['services']['mysql']['image'] == Engine100M::IMAGE &&
    parsed_compose['services']['mysql']['volumes'].length == 1 &&
    parsed_compose['services']['mysql']['volumes'][0]['type'] == 'bind'
end
assert.call('pinned image, socket-only, host uid/gid bind, constrained identical server') do
  compose.include?(Engine100M::IMAGE) && compose.include?('network_mode: none') && compose.include?('--skip-networking') &&
    compose.include?('--datadir=/labdata/data') && compose.include?('--tmpdir=/labdata/tmp') &&
    compose.include?('--socket=/labdata/mysql.sock') && compose.include?('--secure-file-priv=NULL') &&
    compose.include?('type: bind') && compose.include?('create_host_path: false') && compose.include?('EC_UID') && compose.include?('EC_GID') &&
    compose.include?('mem_limit: 4g') && compose.include?('cpus: 3.0') && !compose.match?(/^\s+(ports|build|networks):/)
end
assert.call('lifecycle caller audit: no build/pull/up/down/prune or root runner calls') do
  !source.match?(/['"](?:build|pull|up|down|prune)['"]/) && source.include?('create --no-recreate --no-build --pull never') &&
    source.include?('guard_stage') && source.include?('refuse data removal while runtime presence unknown') &&
    source.include?('FileUtils.remove_entry_secure(@stage_path)') && source.include?("spawn_owned(@cli + ['start', '--attach', @container_id]") &&
    source.include?('project_inventory.values.all?(&:empty?)')
end
# Exercise the stream parser against pipes. These are synthetic text receipts, never DB samples.
class PipeController
  def client_owner = 'synthetic_offline'
  def limit(seconds) = Engine100M.ns + (seconds * 1e9).to_i
  def tick(stop, monitor: true) = (raise 'deadline exceeded' if Engine100M.ns >= stop)
  def count_preflight(stop)
    sleep 0.08 # synthetic synchronous df cost, outside timer
    tick(stop)
  end
end
class SlowRawLog < StringIO
  def write(chunk)
    sleep 0.08 # deliberately slow raw logging must not move the scalar arrival timestamp
    super
  end
end
def simulated_response(mode)
  client = Engine100M::Client.allocate
  reader, input = IO.pipe
  output, writer = IO.pipe
  requests = StringIO.new
  raw = mode == :delayed_boundary ? SlowRawLog.new : StringIO.new
  client.instance_variable_set(:@controller, PipeController.new)
  client.instance_variable_set(:@input, input)
  client.instance_variable_set(:@output, output)
  client.instance_variable_set(:@requests, requests)
  client.instance_variable_set(:@raw, raw)
  client.instance_variable_set(:@buffer, +'')
  client.instance_variable_set(:@serial, 0)
  worker = Thread.new do
    reader.gets
    boundary_sql = reader.gets
    marker = boundary_sql[/SELECT '([^']+)'/, 1]
    case mode
    when :valid
      writer.write("alpha\t\n42\n#{marker[0, 5]}")
      writer.write("#{marker[5..]}\n")
    when :eof
      writer.write("partial\n")
    when :delayed_boundary
      writer.write("100000000\n")
      sleep 0.15
      writer.write("#{marker}\n")
    when :scalar_eof
      writer.write("100000000\n")
    when :timeout
      # Reader remains owned and joined; the controller timeout is immediate.
    end
  ensure
    writer.close unless mode == :timeout
  end
  begin
    is_scalar = [:delayed_boundary, :scalar_eof].include?(mode)
    response = client.query(is_scalar ? Engine100M::COUNT_SQL : 'SELECT synthetic_offline;', seconds: mode == :timeout ? 0.01 : 1, scalar: is_scalar)
    [response, raw.string, nil]
  rescue StandardError => e
    [nil, raw.string, [e.message, client.last_failure]]
  ensure
    input.close; worker.join; reader.close; writer.close unless writer.closed?; output.close
  end
end
response, raw, err = simulated_response(:valid)
assert.call('stream parser retains tabs/multirow and waits fragmented boundary, ns integer') { err.nil? && response[:lines] == ["alpha\t", '42'] && response[:elapsed_ns].is_a?(Integer) && response[:elapsed_ns].positive? && raw.include?('EC100M_') }
response, raw, err = simulated_response(:eof)
assert.call('EOF before boundary fails and retains partial raw/error') { response.nil? && raw == "partial\n" && err[0].include?('EOF before boundary') && err[1][:raw_lines] == ['partial'] }
response, _, err = simulated_response(:timeout)
assert.call('missing boundary timeout is failure, never fabricated scalar') { response.nil? && err[0].include?('deadline exceeded') && err[1][:elapsed_ns].positive? }
response, _, err = simulated_response(:delayed_boundary)
assert.call('delayed boundary affects full request but not scalar arrival; preflight df and slow raw log excluded') do
  err.nil? && response[:lines] == ['100000000'] && response[:scalar_elapsed_ns].positive? &&
    response[:scalar_elapsed_ns] < 60_000_000 && response[:elapsed_ns] - response[:scalar_elapsed_ns] >= 120_000_000
end
response, raw, err = simulated_response(:scalar_eof)
assert.call('EOF after exact scalar still fails and preserves value and arrival timing') do
  response.nil? && raw == "100000000\n" && err[0].include?('EOF before boundary') &&
    err[1][:raw_lines] == ['100000000'] && err[1][:scalar_elapsed_ns].positive?
end
# Exercise the real condition orchestration with synthetic SQL receipts, never a DB.
class CatalogueFixture
  attr_reader :calls, :last_failure
  def initialize(fail_index: false, fail_count: false)
    @calls = []; @secondary = false; @fail_index = fail_index; @fail_count = fail_count
  end
  def active? = false
  def close(**_opts); end
  def query(sql, **_opts)
    @calls << sql
    lines = case sql
    when Engine100M::COUNT_SQL
      if @fail_count && @calls.count(Engine100M::COUNT_SQL) == 2
        @last_failure = { failure: 'synthetic EOF after scalar', raw_lines: ['100000000'], scalar_elapsed_ns: 1000 }
        raise 'synthetic EOF after scalar'
      end
      ['100000000']
    when /\ASTART TRANSACTION;/ then ['100000']
    when /\AALTER TABLE/
      if @fail_index
        @last_failure = { failure: 'synthetic index failure' }
        raise 'synthetic index failure'
      end
      @secondary = sql.include?('k_count_v2') ? :composite : :narrow
      []
    when 'SHOW INDEX FROM lab.count_target;'
      extra = case @secondary
      when :narrow then ["count_target\t1\tk_count\t1\tc001"]
      when :composite then (1..3).map { |i| "count_target\t1\tk_count_v2\t#{i}\tc00#{i}" }
      else [] end
      ["count_target\t0\tPRIMARY\t1\tid"] + extra
    when /\AEXPLAIN / then ["synthetic plan: PRIMARY remains chosen even if secondary present"]
    else ['synthetic metadata']
    end
    { lines: lines, elapsed_ns: 1500, scalar_elapsed_ns: 1000 }
  end
end
def simulated_stages(fail_index: false, fail_count: false)
  stages = []; clients = []
  Engine100M::ENGINES.each do |engine|
    stage = { engine: engine, batches: [], conditions: Engine100M::CONDITIONS.fetch(engine).map { |id| Engine100M.condition_model(id) }, completion: 'incomplete' }
    stages << stage
    client = CatalogueFixture.new(fail_index: fail_index, fail_count: fail_count); clients << client
    c = Engine100M::Controller.allocate
    c.instance_variable_set(:@stage, stage); c.instance_variable_set(:@client, client)
    c.instance_variable_set(:@deadline, Engine100M.ns + 10_000_000_000)
    c.define_singleton_method(:save) {}
    c.define_singleton_method(:note) { |_message| }
    c.define_singleton_method(:capacity) { |*_, **_| 100 * 1024**3 }
    c.define_singleton_method(:status_counters) { ['synthetic status'] }
    c.define_singleton_method(:restart_client) { |*_, **_| instance_variable_set(:@client, client) }
    c.define_singleton_method(:capture_manual_reference) { |_| { explain: { raw: ['synthetic plan: PRIMARY remains chosen even if secondary present'] }, selected_index: 'PRIMARY' } }
    begin
      c.measure_stage
    rescue StandardError => e
      stage[:error] = e.message
      break
    end
  end
  [stages, clients]
end
stages, clients = simulated_stages
assert.call('orchestration loads each engine once, measures 9+3, builds only after PK3, retains optimizer choice') do
  a = clients[0].calls; b = clients[1].calls; index = a.index { |sql| sql.start_with?('ALTER TABLE') }
  Engine100M.run_exact?(stages) && a.count(Engine100M::COUNT_SQL) == 9 && b.count(Engine100M::COUNT_SQL) == 3 &&
    a.take(index).count(Engine100M::COUNT_SQL) == 3 && a.count { |s| s.start_with?('START TRANSACTION;') } == 1000 &&
    b.count { |s| s.start_with?('START TRANSACTION;') } == 1000 && b.none? { |s| s.start_with?('ALTER TABLE') } &&
    stages[0][:conditions].last[:plan].first.include?('PRIMARY remains chosen') && Engine100M.ratios(stages).length == 5
end
copy_model = -> { Marshal.load(Marshal.dump(stages)) }
missing = copy_model.call; missing[0][:conditions].pop
assert.call('missing required condition cannot pass or produce ratios') { !Engine100M.run_exact?(missing) && Engine100M.ratios(missing).empty? }
missing = copy_model.call; missing[0][:conditions].last[:counts].pop
assert.call('missing one of twelve counts cannot pass or produce ratios') { !Engine100M.run_exact?(missing) && Engine100M.ratios(missing).empty? }
wrong = copy_model.call; wrong[1][:conditions].first[:counts].last[:value] = 0
assert.call('count mismatch cannot pass or produce ratios') { !Engine100M.run_exact?(wrong) && Engine100M.ratios(wrong).empty? }
wrong = copy_model.call; wrong[0][:conditions].last[:counts].first[:caller_elapsed_ns] = 0
assert.call('zero timing cannot produce ratios') { Engine100M.ratios(wrong).empty? }
partial, clients = simulated_stages(fail_index: true)
assert.call('failed index retains PK3 and all pending secondary slots, never measures another condition') do
  partial.length == 1 && partial[0][:completion] == 'incomplete' && partial[0][:index_build][:status] == 'incomplete' &&
    partial[0][:conditions].first[:counts].all? { |s| s[:status] == 'valid' } &&
    partial[0][:conditions].last[:counts].map { |s| s[:status] } == ['not_started'] * 3 &&
    clients[0].calls.count(Engine100M::COUNT_SQL) == 3 && Engine100M.ratios(partial).empty?
end
partial, clients = simulated_stages(fail_count: true)
assert.call('failed boundary after value remains incomplete in its original slot, no retry or omitted samples') do
  counts = partial[0][:conditions].first[:counts]
  counts.map { |s| s[:status] } == %w[valid incomplete not_started] &&
    counts[1][:failure][:client][:raw_lines] == ['100000000'] && counts[1][:failure][:client][:scalar_elapsed_ns] == 1000 &&
    clients[0].calls.count(Engine100M::COUNT_SQL) == 2 && Engine100M.ratios(partial).empty?
end
# Capacity parser/guard uses the same safe argv and never substitutes guest free space.
guard = Engine100M::Controller.allocate
guard.instance_variable_set(:@data_root, '/synthetic-host-bind')
guard.instance_variable_set(:@result, { capacities: [] })
free_bytes = 70 * 1024**3
df_calls = []
guard.define_singleton_method(:command) do |argv, _tag, **opts|
  df_calls << [argv, opts]
  ["Filesystem 1024-blocks Used Available Capacity Mounted on\nsynthetic 200000000 0 #{free_bytes / 1024} 0% /synthetic-host-bind\n", 0]
end
assert.call('host capacity guard parses 70 GiB and uses fixed df argv without shell or recursive monitoring') do
  guard.capacity('preflight', minimum: 70 * 1024**3) == free_bytes &&
    df_calls.last[0] == ['/bin/df', '-Pk', '/synthetic-host-bind'] && df_calls.last[1][:monitor] == false
end
[[23, 24, 'index working headroom'], [11, 12, 'ongoing host floor']].each do |available_gib, minimum_gib, label|
  free_bytes = available_gib * 1024**3
  begin
    guard.capacity('synthetic', minimum: minimum_gib * 1024**3)
    raise 'low capacity accepted'
  rescue RuntimeError => e
    assert.call("#{label} violation fails and retains actual host capacity") do
      e.message.start_with?('host capacity below') && guard.result[:capacities].last[:available_bytes] == free_bytes
    end
  end
end
# Exercise real foreground local Ruby children only; no Docker or DB.
Dir.mktmpdir('engine100m-offline-') do |tmp|
  tmp = File.realpath(tmp)
  c = Engine100M::Controller.allocate
  c.instance_variable_set(:@run, tmp)
  c.instance_variable_set(:@log, StringIO.new)
  c.instance_variable_set(:@env, { 'PATH' => ENV.fetch('PATH') })
  c.instance_variable_set(:@sequence, 0)
  c.instance_variable_set(:@processes, [])
  c.instance_variable_set(:@result, { commands: [] })
  c.instance_variable_set(:@deadline, Engine100M.ns + 10_000_000_000)
  out, code = c.command([RbConfig.ruby, '-e', 'STDOUT.write("x"*262144);STDERR.write("y"*262144);exit 7'], 'synthetic-child', check: false, monitor: false)
  p = c.instance_variable_get(:@processes).last
  assert.call('stdout/stderr file drain without pipe deadlock; actual nonzero exit reaped') { code == 7 && out.bytesize == 262144 && File.size(p[:stderr]) == 262144 && p[:reaped] }
  begin
    c.command([RbConfig.ruby, '-e', 'sleep 10'], 'synthetic-timeout', seconds: 0.05, monitor: false)
    raise 'timeout was accepted'
  rescue RuntimeError => e
    p = c.instance_variable_get(:@processes).last
    assert.call('timed out owned local child terminated and reaped with numeric exit') { e.message == 'deadline exceeded' && p[:timed_out] && p[:reaped] && p[:exit_status].is_a?(Integer) && p[:exit_status] != 0 }
  end
  owner = 'offline-owner'
  owner_root = File.join(tmp, owner); Dir.mkdir(owner_root, 0o700)
  marker = File.join(owner_root, '.owner.json')
  File.write(marker, JSON.generate(nonce: owner, root: owner_root, uid: Process.uid))
  c.instance_variable_set(:@data_root, tmp); c.instance_variable_set(:@owner_root, owner_root)
  c.instance_variable_set(:@owner, owner); c.instance_variable_set(:@marker, marker)
  assert.call('valid private nonce marker accepted offline') { c.guard_stage.nil? }
  File.write(marker, JSON.generate(nonce: 'wrong', root: owner_root, uid: Process.uid))
  begin
    c.guard_stage
    raise 'bad marker was accepted'
  rescue RuntimeError => e
    assert.call('mismatched ownership marker refuses cleanup') { e.message == 'owner marker mismatch' }
  end
  c.define_singleton_method(:docker) { |*_args, **_opts| [JSON.generate([{ 'Config' => { 'Labels' => { Engine100M::LABEL => 'other' } } }]), 0] }
  begin
    c.owned_inspect
    raise 'unowned container was accepted'
  rescue RuntimeError => e
    assert.call('unowned container refuses lifecycle mutation') { e.message == 'refusing unowned container' }
  end
end
# Simulate partial-stage cleanup with owned temporary host fixtures; Docker calls are stubs.
# This checks failure recovery ordering without creating any container or MySQL data.
[:normal, :stop_timeout, :unowned].each do |mode|
  Dir.mktmpdir('engine100m-cleanup-offline-') do |tmp|
    tmp = File.realpath(tmp)
    c = Engine100M::Controller.allocate
    owner = 'offline-owner'
    owner_root = File.join(tmp, owner); Dir.mkdir(owner_root, 0o700)
    marker = File.join(owner_root, '.owner.json')
    File.write(marker, JSON.generate(nonce: owner, root: owner_root, uid: Process.uid))
    engine_root = File.join(owner_root, 'InnoDB'); Dir.mkdir(engine_root, 0o700)
    stage_path = File.join(engine_root, 'stage'); Dir.mkdir(stage_path, 0o700)
    File.write(File.join(stage_path, 'offline-placeholder'), 'synthetic fixture only')
    stage = { engine: 'InnoDB', completion: 'incomplete', conditions: Engine100M::CONDITIONS['InnoDB'].map { |id| Engine100M.condition_model(id) }, batches: [] }
    { run: tmp, owner: owner, owner_root: owner_root, marker: marker, data_root: tmp,
      stage_path: stage_path, stage: stage, may_have_container: true,
      result: { stages: [stage] }, deadline: Engine100M.ns + 10_000_000_000 }.each { |k, v| c.instance_variable_set("@#{k}", v) }
    calls = []
    present = true; running = true
    c.define_singleton_method(:inventory) { { 'container' => present ? [Engine100M::NAME] : [], 'volume' => [], 'network' => [] } }
    c.define_singleton_method(:project_inventory) { { 'container' => [], 'volume' => [], 'network' => [] } }
    c.define_singleton_method(:capacity) { |*args, **_opts| calls << [:capacity, args]; 100 * 1024**3 }
    c.define_singleton_method(:docker) do |args, _tag, **_opts|
      calls << args
      case args.first
      when 'inspect'
        labels = { Engine100M::LABEL => mode == :unowned ? 'other-owner' : owner, 'com.docker.compose.project' => Engine100M::PROJECT }
        [JSON.generate([{ 'Id' => 'synthetic-container-id', 'Config' => { 'Labels' => labels }, 'State' => { 'Running' => running, 'ExitCode' => mode == :stop_timeout ? 137 : 0 } }]), 0]
      when 'stop'
        raise 'synthetic stop deadline exceeded' if mode == :stop_timeout
        running = false; ['', 0]
      when 'kill'
        running = false; ['', 0]
      when 'rm'
        raise 'synthetic attempted live removal' if running
        present = false; ['', 0]
      when 'logs'
        ['', 0]
      else
        raise "unexpected synthetic Docker call #{args}"
      end
    end
    error = nil
    begin
      c.cleanup_stage
    rescue StandardError => e
      error = e.message
    end
    if mode == :normal
      assert.call('partial load cleanup removes owned stopped runtime then guarded stage') { error.nil? && !present && !File.exist?(stage_path) && stage[:completion] == 'incomplete' && stage[:cleanup].start_with?('passed') }
    elsif mode == :stop_timeout
      assert.call('stop timeout retains failure, kills only owned runtime, still removes guarded partial data') { error&.include?('stop failure') && calls.any? { |a| a.first == 'kill' } && !present && !File.exist?(stage_path) && stage[:cleanup] == 'failed_or_unknown' }
    else
      assert.call('unowned runtime prevents stop/rm and guarded data deletion') { error&.include?('unowned') && present && File.exist?(stage_path) && calls.none? { |a| %w[stop kill rm].include?(a.first) } }
    end
  end
end
source_hashes = Engine100M::SOURCE.to_h { |p| [p, Digest::SHA256.file(File.join(root, p)).hexdigest] }
puts JSON.pretty_generate({ status: 'offline_checks_passed_no_runtime_started', checks: checks.length, baseline_sha256: Digest::SHA256.file(baseline_path).hexdigest,
                           protected_files: baseline.fetch('protectedFiles').length, source_sha256: source_hashes,
                           standalone_report_sha256: Digest::SHA256.file(File.join(root, 'docs/mysql-engine-count-100m.md')).hexdigest })
puts 'OFFLINE_VERIFICATION_FINAL_EXIT_STATUS=0'
