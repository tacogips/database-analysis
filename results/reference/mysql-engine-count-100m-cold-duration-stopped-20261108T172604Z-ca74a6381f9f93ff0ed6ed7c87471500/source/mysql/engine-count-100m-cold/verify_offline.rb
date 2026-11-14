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
root = Engine100MCold::ROOT
baseline_path = File.join(root, 'results/reviews/mysql-engine-count-100m-cold/baseline.json')
assert.call('authoritative baseline hash') { Digest::SHA256.file(baseline_path).hexdigest == '0e184ba5a0ee10e3e87440295f0e342f9a198066a176f1328e99f4ef4a73a5db' }
baseline = JSON.parse(File.read(baseline_path))
bad = baseline.fetch('protectedFiles').filter_map do |f, h|
  p = File.join(root, f)
  f unless File.file?(p) && Digest::SHA256.file(p).hexdigest == h
end
assert.call("all #{baseline.fetch('protectedFiles').length} protected files exact") { bad.empty? }
assert.call('two engines in fixed order') { Engine100MCold::ENGINES == %w[InnoDB MyISAM] }
assert.call('exactly four planned conditions with twelve preserved slots') do
  Engine100MCold::CONDITIONS == { 'InnoDB' => %w[innodb_primary_only innodb_secondary_present innodb_composite_secondary], 'MyISAM' => %w[myisam_primary_only] } &&
    Engine100MCold::CONDITIONS.values.flatten.sum { |id| Engine100MCold.condition_model(id)[:counts].length } == 12
end
assert.call('runtime source includes executable inputs and frozen manual; final report independently bound') do
  Engine100MCold::SOURCE == %w[lab.rb reference_flow.rb compose.yaml mise.toml README.md verify_offline.rb].map { |p| "mysql/engine-count-100m-cold/#{p}" }.push('results/reviews/mysql-engine-count-100m-cold/manual-sources/mysql-innodb.md', 'results/reviews/mysql-engine-count-100m-cold/manual-sources/source.json')
end
assert.call('copied manual and original capture receipt retain exact original bytes') do
  manual = File.join(root, 'results/reviews/mysql-engine-count-100m-cold/manual-sources/mysql-innodb.md')
  receipt = File.join(root, 'results/reviews/mysql-engine-count-100m-cold/manual-sources/source.json')
  Digest::SHA256.file(manual).hexdigest == 'df439985905ebe9c5e8b8c2c1b8a4761ce683d8d290ae9363c284e9d00e2981e' &&
    Digest::SHA256.file(receipt).hexdigest == 'd4f1c9217493eec79e4df2c1d7a28358a973cb4606ca318ad3c63019ca3a5991'
end
assert.call('dedicated cold namespace, paths, resource labels and exact mise nested exec') do
  Engine100MCold::NAME == 'lab-mysql-engine-count-100m-cold' && Engine100MCold::PROJECT == Engine100MCold::NAME &&
    Engine100MCold::LABEL == 'count.engine100m.cold.owner' && File.read(File.join(__dir__, 'mise.toml')).include?('mise exec -- ruby lab.rb all') &&
    File.read(File.join(__dir__, 'lab.rb')).include?("'.data/engine-count-100m-cold'")
end
assert.call('exact 100M geometry') { Engine100MCold::ROWS == 100_000_000 && Engine100MCold::BATCH == 100_000 && Engine100MCold::BATCHES == 1000 && Engine100MCold::BATCH * Engine100MCold::BATCHES == Engine100MCold::ROWS }
a = Engine100MCold.ddl('InnoDB'); b = Engine100MCold.ddl('MyISAM')
columns = a[/\((.*)\) ENGINE=/, 1].split(',')
assert.call('exact 100 columns, nonnull numeric PK only, 404 payload bytes') do
  columns == ['id BIGINT UNSIGNED NOT NULL PRIMARY KEY'] + (1..99).map { |i| format('c%03d INT NOT NULL', i) } && 8 + 99 * 4 == 404
end
assert.call('DDL differs only in ENGINE') { a.sub('ENGINE=InnoDB', 'ENGINE=MyISAM') == b }
assert.call('1000 nonoverlapping batches, 100 physical values and varied numeric records') do
  (0...1000).all? do |i|
    vals = Engine100MCold.values_for(i)
    vals.length == 100 && vals.first == "#{i * 100_000}+n" && vals.drop(4) == (4..99).map(&:to_s) &&
      Engine100MCold.batch_sql(i).include?("SELECT #{vals.join(',')} FROM lab.seq") && Engine100MCold.batch_sql(i).end_with?('ORDER BY n;SELECT ROW_COUNT();COMMIT;')
  end && (999 * 100_000 + 100_000 == 100_000_000) &&
    (1..100_000).map { |id| id * 104729 % 100_000 }.uniq.length == 100_000
end
assert.call('digits cross join produces exactly 1..100000') do
  values = (0..9).to_a.repeated_permutation(5).map { |d| 1 + d.each_with_index.sum { |x, i| x * 10**i } }.sort
  values == (1..100_000).to_a && Engine100MCold.seed.scan('CROSS JOIN').length == 4
end
source = File.read(File.join(__dir__, 'lab.rb'))
assert.call('only ordinary COUNT(*) query constant; no variant/count validation call') do
  source.scan(/\bCOUNT\([^)]*\)/) == ['COUNT(*)'] && Engine100MCold::COUNT_SQL == 'SELECT COUNT(*) FROM lab.count_target;' &&
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
    parsed_compose['services']['mysql']['image'] == Engine100MCold::IMAGE &&
    parsed_compose['services']['mysql']['volumes'].length == 1 &&
    parsed_compose['services']['mysql']['volumes'][0]['type'] == 'bind'
end
assert.call('pinned image, socket-only, host uid/gid bind, constrained identical server') do
  compose.include?(Engine100MCold::IMAGE) && compose.include?('network_mode: none') && compose.include?('--skip-networking') &&
    compose.include?('--datadir=/labdata/data') && compose.include?('--tmpdir=/labdata/tmp') &&
    compose.include?('--socket=/labdata/mysql.sock') && compose.include?('--secure-file-priv=NULL') &&
    compose.include?('type: bind') && compose.include?('create_host_path: false') && compose.include?('EC_UID') && compose.include?('EC_GID') &&
    compose.include?('mem_limit: 4g') && compose.include?('cpus: 3.0') && !compose.match?(/^\s+(ports|build|networks):/)
end
assert.call('fixed compose startup disables pool load/dump and requests O_DIRECT on every same-ID start') do
  %w[--innodb-buffer-pool-load-at-startup=OFF --innodb-buffer-pool-dump-at-shutdown=OFF --innodb-flush-method=O_DIRECT].all? { |flag| parsed_compose['services']['mysql']['command'].include?(flag) } &&
    parsed_compose['services']['mysql']['container_name'] == Engine100MCold::NAME && parsed_compose['services']['mysql']['labels'].key?(Engine100MCold::LABEL)
end
assert.call('lifecycle caller audit: no build/pull/up/down/prune or root runner calls') do
  !source.match?(/['"](?:build|pull|up|down|prune)['"]/) && source.include?('create --no-recreate --no-build --pull never') &&
    source.include?('guard_stage') && source.include?('refuse data removal while runtime presence unknown') &&
    source.include?('FileUtils.remove_entry_secure(@stage_path)') && File.read(File.join(__dir__, 'reference_flow.rb')).include?("spawn_owned(@cli + ['start', '--attach', @container_id]") &&
    source.include?('project_inventory.values.all?(&:empty?)')
end
# Exercise the stream parser against pipes. These are synthetic text receipts, never DB samples.
class PipeController
  def client_owner = 'synthetic_offline'
  def limit(seconds) = Engine100MCold.ns + (seconds * 1e9).to_i
  def tick(stop, monitor: true) = (raise 'deadline exceeded' if Engine100MCold.ns >= stop)
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
  client = Engine100MCold::Client.allocate
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
    response = client.query(is_scalar ? Engine100MCold::COUNT_SQL : 'SELECT synthetic_offline;', seconds: mode == :timeout ? 0.01 : 1, scalar: is_scalar)
    [response, raw.string, nil]
  rescue StandardError => e
    [nil, raw.string, [e.message, client.last_failure]]
  ensure
    input.close; worker.join; reader.close; writer.close unless writer.closed?; output.close
  end
end
response, raw, err = simulated_response(:valid)
assert.call('stream parser retains tabs/multirow and waits fragmented boundary, ns integer') { err.nil? && response[:lines] == ["alpha\t", '42'] && response[:elapsed_ns].is_a?(Integer) && response[:elapsed_ns].positive? && raw.include?('EC100MCOLD_') }
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
# Exercise the actual boot/reset/client/manual/count orchestration using in-memory
# Docker/SQL receipts. Nothing calls Docker, connects to a DB or generates row data.
class CatalogueFixture
  attr_reader :last_failure, :pair
  def initialize(controller, world)
    @controller = controller; @world = world
    @pair = [world[:pid] += 1, { tag: 'InnoDB-mysql-client', pid: world[:pid], exit_status: nil, boot_ordinal: world[:boot], reaped: false }]
    world[:processes] << @pair[1]
    world[:events] << [:client_open, world[:boot]]
  end
  def active? = false
  def close(**_opts)
    return if @pair[1][:reaped]
    @pair[1].merge!(exit_status: 0, reaped: true)
    @world[:events] << [:client_closed, @world[:boot]]
  end
  def query(sql, **_opts)
    w = @world
    w[:calls] << sql
    w[:events] << [:sql, w[:boot], sql]
    lines = case sql
    when Engine100MCold::TABLE_ROWS_SQL then [w.fetch(:table_rows_raw, '99000000')]
    when Engine100MCold::COUNT_SQL
      w[:count] += 1
      if w[:fail_count] && w[:count] == 2
        @last_failure = { failure: 'synthetic EOF after scalar', raw_lines: ['100000000'], scalar_elapsed_ns: 1000 }
        raise 'synthetic EOF after scalar'
      end
      raise 'synthetic concurrent client during COUNT' unless w[:processes].count { |p| p[:tag].end_with?('-mysql-client') && !p[:reaped] } == 1
      ['100000000']
    when /\ASTART TRANSACTION;/ then ['100000']
    when /\ASELECT @@server_uuid/ then ["#{w[:uuid]}\tsynthetic\t7\t2026-01-01 00:00:00.000000"]
    when /\ASET SESSION autocommit=/ then ["#{w[:uuid]}\tsynthetic\t7\tREAD-COMMITTED\t16384\t86400\t2026-01-01 00:00:00.000000\t1\t3\t1200000"]
    when 'SELECT 1;' then ['1']
    when "SHOW GLOBAL STATUS LIKE 'Uptime';" then ["Uptime\t1"]
    when /\ASHOW GLOBAL VARIABLES/ then Engine100MCold::COLD_GLOBALS.merge('version_compile_machine' => 'aarch64').map { |k, v| "#{k}\t#{w[:bad_flag] == k ? 'ON' : v}" }
    when /\AALTER TABLE/
      if w[:fail_index]
        @last_failure = { failure: 'synthetic index failure' }
        raise 'synthetic index failure'
      end
      w[:secondary] = sql.include?('k_count_v2') ? :composite : :narrow
      []
    when 'SHOW INDEX FROM lab.count_target;'
      extra = case w[:secondary]
      when :narrow then ["count_target\t1\tk_count\t1\tc001"]
      when :composite then (1..3).map { |i| "count_target\t1\tk_count_v2\t#{i}\tc00#{i}" }
      else [] end
      ["count_target\t0\tPRIMARY\t1\tid"] + extra
    when /\AEXPLAIN / then ["1\tSIMPLE\tcount_target\tNULL\tindex\tNULL\tPRIMARY\t8\tNULL\t100000000\t100.00\tUsing index"]
    when /FROM mysql.innodb_index_stats/ then ["PRIMARY\tn_leaf_pages\t2000000\t2026-01-01", "PRIMARY\tsize\t3000000\t2026-01-01"]
    when /FROM information_schema.INNODB_BUFFER_PAGE/ then ["PRIMARY\tINDEX\t#{w[:fully_resident] ? 3000000 : 2}"]
    when /\ASHOW GLOBAL STATUS/ then ["Innodb_buffer_pool_reads\t#{w[:count] * 100}", "Key_reads\t0"]
    else ['synthetic metadata']
    end
    { lines: lines, elapsed_ns: 1500, scalar_elapsed_ns: 1000 }
  end
end
def synthetic_controller(engine, serial: 0, **options)
  stage = { engine: engine, batches: [], boots: [], conditions: Engine100MCold::CONDITIONS.fetch(engine).map { |id| Engine100MCold.condition_model(id) }, completion: 'incomplete' }
  world = { boot: serial, pid: 100, events: [], calls: [], processes: [], running: false, count: 0, secondary: false,
            uuid: engine == 'InnoDB' ? 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa' : 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb' }.merge(options)
  c = Engine100MCold::Controller.allocate
  { stage: stage, result: { image: { 'Id' => 'synthetic-image' } }, owner: 'synthetic-owner', stage_path: '/synthetic-owned-data',
    container_id: "synthetic-#{engine}", cli: ['synthetic-docker'], boot_serial: serial, processes: world[:processes], deadline: Engine100MCold.ns + 10_000_000_000 }.each { |k, v| c.instance_variable_set("@#{k}", v) }
  c.define_singleton_method(:save) {}
  c.define_singleton_method(:note) { |_message| }
  c.define_singleton_method(:capacity) { |*_, **_| 100 * 1024**3 }
  c.define_singleton_method(:guard_stage) {} # Separate real marker/realpath tests below.
  c.define_singleton_method(:open_client) { CatalogueFixture.new(self, world) }
  c.define_singleton_method(:spawn_owned) do |_argv, _tag|
    raise 'synthetic restart with live server/client' if world[:running] || world[:processes].any? { |p| !p[:reaped] }
    raise 'synthetic start failure' if world[:start_failure] && world[:boot] >= 1
    world[:boot] += 1; world[:running] = true
    world[:events] << [:start, world[:boot]]
    pair = [world[:pid] += 1, { tag: 'synthetic-server', exit_status: nil, reaped: false, pid: world[:pid] }]
    world[:processes] << pair[1]
    pair
  end
  c.define_singleton_method(:reap) do |pair, **_opts|
    pair[1][:exit_status]
  end
  c.define_singleton_method(:docker) do |args, _tag, **_opts|
    case args.first
    when 'inspect'
      obj = { 'Id' => "synthetic-#{engine}", 'Image' => 'synthetic-image', 'RestartCount' => 0,
              'Config' => { 'Labels' => { Engine100MCold::LABEL => 'synthetic-owner', 'com.docker.compose.project' => Engine100MCold::PROJECT }, 'User' => "#{Process.uid}:#{Process.gid}", 'Cmd' => %w[--datadir=/labdata/data --tmpdir=/labdata/tmp --socket=/labdata/mysql.sock --skip-networking --innodb-buffer-pool-load-at-startup=OFF --innodb-buffer-pool-dump-at-shutdown=OFF --innodb-flush-method=O_DIRECT] },
              'HostConfig' => { 'NetworkMode' => 'none', 'Memory' => 4 * 1024**3, 'NanoCpus' => 3_000_000_000, 'PortBindings' => {} },
              'Mounts' => [{ 'Type' => 'bind', 'Source' => '/synthetic-owned-data', 'Destination' => '/labdata' }],
              'State' => { 'Running' => world[:running], 'ExitCode' => world[:nonzero_stop] && !world[:running] ? 137 : 0, 'OOMKilled' => false, 'Pid' => 1000 + world[:boot], 'StartedAt' => "2026-01-01T00:00:#{format('%02d', world[:boot])}.000000000Z" } }
      [JSON.generate([obj]), 0]
    when 'exec'
      raise 'synthetic readiness failure' if world[:readiness_failure] && world[:boot] > 1
      if args.include?('mysqladmin')
        world[:events] << [:ping, world[:boot]]
        if world[:pending_ping].to_i.positive?
          world[:pending_ping] -= 1
          ['', 1]
        else
          ["mysqld is alive\n", 0]
        end
      else
        ["mysqld\n", 0]
      end
    when 'top' then ['synthetic PID inventory', 0]
    when 'stop'
      raise 'synthetic stop with unclosed client' if world[:processes].any? { |p| p[:tag].end_with?('-mysql-client') && !p[:reaped] }
      raise 'synthetic stop failure' if world[:refuse_stop]
      world[:events] << [:stop, world[:boot]]
      world[:running] = false
      unless world[:not_reaped]
        instance_variable_get(:@server)[1].merge!(reaped: true, exit_status: world[:nonzero_attach] ? 7 : 0)
      end
      ['', 0]
    else raise "unexpected synthetic Docker argv #{args}"
    end
  end
  if options[:not_reaped]
    c.define_singleton_method(:limit) { |seconds| Engine100MCold.ns + (seconds == 5 ? 10_000_000 : 5_000_000_000) }
  end
  c.start_boot('initial-load')
  c.restart_client('fixture-loader')
  [c, stage, world]
end
def simulated_stages(**options)
  stages = []; worlds = []; serial = 0
  Engine100MCold::ENGINES.each do |engine|
    c, stage, w = synthetic_controller(engine, serial: serial, **options)
    stages << stage; worlds << w
    begin
      c.measure_stage
      c.stop_boot('stage-final')
      serial = w[:boot]
    rescue StandardError => e
      stage[:error] = e.message
      break
    end
  end
  Engine100MCold.summarize(stages) if Engine100MCold.run_exact?(stages)
  [stages, worlds]
end
stages, worlds = simulated_stages
successful_worlds = worlds
assert.call('real orchestration loads 1000 batches each, PK3 before narrow/composite, 9+3 counts; optimizer actual key unforced') do
  a = worlds[0][:calls]; b = worlds[1][:calls]; index = a.index { |sql| sql.start_with?('ALTER TABLE') }
  Engine100MCold.run_exact?(stages) && a.count(Engine100MCold::COUNT_SQL) == 9 && b.count(Engine100MCold::COUNT_SQL) == 3 &&
    a.take(index).count(Engine100MCold::COUNT_SQL) == 3 && a.count { |sql| sql.start_with?('START TRANSACTION;') } == 1000 &&
    b.count { |sql| sql.start_with?('START TRANSACTION;') } == 1000 && b.none? { |sql| sql.start_with?('ALTER TABLE') } &&
    stages[0][:conditions].last[:manual_reference][:selected_index] == 'PRIMARY' && Engine100MCold.ratios(stages).length == 5
end
samples = stages.flat_map { |stage| stage[:conditions].flat_map { |condition| condition[:counts] } }
assert.call('initial, after each index build, and every repeat reset: 12 distinct count boots, 14 total, all stopped/reaped') do
  stages.map { |stage| stage[:boots].length } == [10, 4] && stages.all? { |stage| Engine100MCold.stage_cold?(stage) } &&
    samples.map { |sample| sample[:boot_ordinal] }.uniq.length == 12 &&
    worlds.all? { |w| w[:processes].all? { |process| process[:reaped] && process[:exit_status] == 0 } }
end
assert.call('same connection ID across new boots valid, composite boot/UUID/connection identity unique') do
  samples.map { |sample| sample[:count_session][:connection_id] }.uniq == [7] && samples.map { |sample| sample[:count_session][:identity] }.uniq.length == 12
end
assert.call('each sample has current manual/SHOW INDEX/EXPLAIN, SQL/raw/ns, two exact-target buffer metadata snapshots') do
  samples.all? { |sample| sample[:index_catalogue][:sql] == 'SHOW INDEX FROM lab.count_target;' && sample[:manual_reference][:explain][:elapsed_ns].positive? && sample[:plan].one? && sample[:buffer_at_start] && sample[:buffer_before_count] } &&
    worlds[0][:calls].count { |sql| sql.include?('FROM information_schema.INNODB_BUFFER_PAGE') && sql.include?("TABLE_NAME='`lab`.`count_target`'") } == 18
end
assert.call('ordering: closed loader/DDL/previous CLI, orderly stop/reap/start, own current prechecks, COUNT once, close') do
  worlds.all? do |w|
    w[:events].select { |event| event[0] == :sql && event[2] == Engine100MCold::COUNT_SQL }.all? do |event|
      boot = event[1]; start = w[:events].index([:start, boot]); stop = w[:events].index([:stop, boot - 1])
      start && stop && stop < start && w[:events].count { |e| e[0] == :sql && e[1] == boot && e[2] == Engine100MCold::COUNT_SQL } == 1
    end
  end
end
copy_model = -> { Marshal.load(Marshal.dump(stages)) }
missing = copy_model.call; missing[0][:conditions].pop
assert.call('missing condition cannot pass or produce ratios') { !Engine100MCold.run_exact?(missing) && Engine100MCold.ratios(missing).empty? }
missing = copy_model.call; missing[0][:conditions].last[:counts].pop
assert.call('missing one of twelve cannot pass or produce ratios') { !Engine100MCold.run_exact?(missing) && Engine100MCold.ratios(missing).empty? }
wrong = copy_model.call; wrong[1][:conditions].first[:counts].last[:value] = 0
assert.call('wrong scalar cannot pass or produce ratios') { !Engine100MCold.run_exact?(wrong) && Engine100MCold.ratios(wrong).empty? }
wrong = copy_model.call; wrong[0][:conditions].last[:counts].first[:caller_elapsed_ns] = 0
assert.call('zero timing cannot produce ratios') { Engine100MCold.ratios(wrong).empty? }
[:reset, :boot_ordinal, :count_session].each do |field|
  wrong = copy_model.call; wrong[0][:conditions].first[:counts].first.delete(field)
  assert.call("missing #{field} fails cold proof including first count") { !Engine100MCold.stage_cold?(wrong[0]) && Engine100MCold.ratios(wrong).empty? }
end
wrong = copy_model.call; wrong[0][:conditions].first[:counts][1][:boot_ordinal] = wrong[0][:conditions].first[:counts][0][:boot_ordinal]
assert.call('two counts in one boot cannot pass cold policy') { !Engine100MCold.stage_cold?(wrong[0]) }
%w[innodb_buffer_pool_load_at_startup innodb_buffer_pool_dump_at_shutdown innodb_flush_method].each do |key|
  actual = Engine100MCold::COLD_GLOBALS.merge('version_compile_machine' => 'aarch64'); actual.delete(key)
  assert.call("missing actual #{key} fails globals verification") { !Engine100MCold.cold_settings?(actual) }
end
[:nonzero_stop, :nonzero_attach, :not_reaped, :refuse_stop, :start_failure].each do |mode|
  c, stage, w = synthetic_controller('InnoDB', **{ mode => true })
  begin
    c.measure_stage
    raise 'bad reset accepted'
  rescue RuntimeError
    assert.call("#{mode}: first reset fails, no restart/COUNT and failed original sample retained") do
      w[:boot] == 1 && w[:count] == 0 && stage[:conditions].first[:counts].map { |sample| sample[:status] } == %w[incomplete not_started not_started] && !Engine100MCold.stage_cold?(stage)
    end
  end
end
c, stage, w = synthetic_controller('InnoDB', readiness_failure: true)
begin
  c.measure_stage
  raise 'failed readiness accepted'
rescue RuntimeError => e
  assert.call('readiness failure preserves second boot and failed first sample, never sends COUNT') do
    e.message.include?('readiness failure') && w[:count] == 0 && stage[:boots].length == 2 &&
      stage[:conditions].first[:counts].first[:status] == 'incomplete' && stage[:boots].last[:failure] && !Engine100MCold.stage_cold?(stage)
  end
end
c, stage, w = synthetic_controller('InnoDB', fully_resident: true)
begin
  c.measure_stage
  raise 'fully resident sample accepted'
rescue RuntimeError => e
  assert.call('fully resident target/index metadata fails cold proof before COUNT') { e.message.include?('fully resident') && w[:count] == 0 && stage[:conditions].first[:counts].first[:status] == 'incomplete' }
end
%w[innodb_buffer_pool_load_at_startup innodb_buffer_pool_dump_at_shutdown].each do |flag|
  begin
    synthetic_controller('InnoDB', bad_flag: flag)
    raise 'bad actual flags accepted'
  rescue RuntimeError => e
    assert.call("actual ON #{flag} aborts boot") { e.message == 'actual boot globals mismatch' }
  end
end
partial, worlds = simulated_stages(fail_index: true)
assert.call('failed index retains PK3 and all pending slots, no another condition or summaries') do
  partial.length == 1 && partial[0][:completion] == 'incomplete' && partial[0][:index_build][:status] == 'incomplete' &&
    partial[0][:conditions].first[:counts].all? { |sample| sample[:status] == 'valid' } &&
    partial[0][:conditions].last[:counts].map { |sample| sample[:status] } == ['not_started'] * 3 &&
    worlds[0][:count] == 3 && Engine100MCold.ratios(partial).empty? && partial[0][:conditions].none? { |condition| condition[:caller_ns_summary] }
end
partial, worlds = simulated_stages(fail_count: true)
assert.call('EOF after value stays failed in its slot, no retry/omission/statistical success') do
  counts = partial[0][:conditions].first[:counts]
  counts.map { |sample| sample[:status] } == %w[valid incomplete not_started] &&
    counts[1][:failure][:client][:raw_lines] == ['100000000'] && counts[1][:failure][:client][:scalar_elapsed_ns] == 1000 &&
    worlds[0][:count] == 2 && Engine100MCold.ratios(partial).empty?
end
# Capacity parser/guard uses the same safe argv and never substitutes guest free space.
guard = Engine100MCold::Controller.allocate
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
  c = Engine100MCold::Controller.allocate
  c.instance_variable_set(:@run, tmp)
  c.instance_variable_set(:@log, StringIO.new)
  c.instance_variable_set(:@env, { 'PATH' => ENV.fetch('PATH') })
  c.instance_variable_set(:@sequence, 0)
  c.instance_variable_set(:@processes, [])
  c.instance_variable_set(:@result, { commands: [] })
  c.instance_variable_set(:@deadline, Engine100MCold.ns + 10_000_000_000)
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
  c.define_singleton_method(:docker) { |*_args, **_opts| [JSON.generate([{ 'Config' => { 'Labels' => { Engine100MCold::LABEL => 'other' } } }]), 0] }
  begin
    c.owned_inspect
    raise 'unowned container was accepted'
  rescue RuntimeError => e
    assert.call('unowned container refuses lifecycle mutation') { e.message == 'refusing unowned container' }
  end
end
# Simulate successful and failed stage cleanup with owned temporary host fixtures; Docker calls are stubs.
# This checks failure recovery ordering without creating any container or MySQL data.
[:normal, :incomplete, :stop_timeout, :unowned].each do |mode|
  Dir.mktmpdir('engine100m-cleanup-offline-') do |tmp|
    tmp = File.realpath(tmp)
    c = Engine100MCold::Controller.allocate
    owner = 'offline-owner'
    owner_root = File.join(tmp, owner); Dir.mkdir(owner_root, 0o700)
    marker = File.join(owner_root, '.owner.json')
    File.write(marker, JSON.generate(nonce: owner, root: owner_root, uid: Process.uid))
    engine_root = File.join(owner_root, 'InnoDB'); Dir.mkdir(engine_root, 0o700)
    stage_path = File.join(engine_root, 'stage'); Dir.mkdir(stage_path, 0o700)
    File.write(File.join(stage_path, 'offline-placeholder'), 'synthetic fixture only')
    stage = { engine: 'InnoDB', completion: 'incomplete', conditions: Engine100MCold::CONDITIONS['InnoDB'].map { |id| Engine100MCold.condition_model(id) }, batches: [] }
    unless mode == :incomplete
      stage[:batches] = (1..1000).map { |i| { batch: i, rows: 100000, status: 'valid' } }
      stage[:conditions].each { |condition| condition[:counts].each { |sample| sample.merge!(status: 'valid', value: 100000000, caller_elapsed_ns: 1000, full_request_elapsed_ns: 1500) } }
      stage[:completion] = 'passed'
    end
    { run: tmp, owner: owner, owner_root: owner_root, marker: marker, data_root: tmp,
      stage_path: stage_path, stage: stage, may_have_container: true,
      result: { stages: [stage] }, deadline: Engine100MCold.ns + 10_000_000_000 }.each { |k, v| c.instance_variable_set("@#{k}", v) }
    calls = []
    present = true; running = true
    c.define_singleton_method(:inventory) { { 'container' => present ? [Engine100MCold::NAME] : [], 'volume' => [], 'network' => [] } }
    c.define_singleton_method(:project_inventory) { { 'container' => [], 'volume' => [], 'network' => [] } }
    c.define_singleton_method(:capacity) { |*args, **_opts| calls << [:capacity, args]; 100 * 1024**3 }
    c.define_singleton_method(:docker) do |args, _tag, **_opts|
      calls << args
      case args.first
      when 'inspect'
        labels = { Engine100MCold::LABEL => mode == :unowned ? 'other-owner' : owner, 'com.docker.compose.project' => Engine100MCold::PROJECT }
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
      assert.call('all counts complete: cleanup removes owned stopped runtime then guarded stage') { error.nil? && !present && !File.exist?(stage_path) && stage[:completion] == 'passed' && stage[:cleanup].start_with?('passed') }
    elsif mode == :incomplete
      assert.call('incomplete counts: stop/remove owned runtime, retain stage payload for owner, cannot pass') { error&.include?('all counts not complete') && !present && File.exist?(stage_path) && stage[:cleanup] == 'failed_or_unknown' }
    elsif mode == :stop_timeout
      assert.call('stop timeout retains failure, kills only owned runtime, completed-count guarded data only') { error&.include?('stop failure') && calls.any? { |a| a.first == 'kill' } && !present && !File.exist?(stage_path) && stage[:cleanup] == 'failed_or_unknown' }
    else
      assert.call('unowned runtime prevents stop/rm and guarded data deletion') { error&.include?('unowned') && present && File.exist?(stage_path) && calls.none? { |a| %w[stop kill rm].include?(a.first) } }
    end
  end
end
interrupted = JSON.parse(File.read(File.join(root, 'results/reviews/mysql-engine-count-100m-cold/interrupted-reference.json')))
assert.call('interrupted uncontrolled-cache reference stays incomplete, exit1, three samples; all original artifacts unchanged') do
  interrupted['status'] == 'incomplete' && interrupted['exit_status'] == 1 && interrupted['completed_valid_counts_total'] == 3 &&
    interrupted['resultSha256'] == '754c156304c6ae7084f9bd4f7a0a7788d0c455909327c18ff87e2f553a110101' &&
    interrupted['artifactSha256'].all? { |path, hash| Digest::SHA256.file(File.join(root, path)).hexdigest == hash }
end
assert.call('all twelve samples capture TABLE_ROWS value/error and scalar/full timing separately from COUNT') do
  samples.all? do |sample|
    metadata = sample.dig(:manual_reference, :table_rows)
    metadata && metadata[:value] == 99_000_000 && metadata[:signed_error_pct] == -1.0 &&
      metadata[:caller_elapsed_ns] == 1000 && metadata[:full_request_elapsed_ns] == 1500 &&
      metadata[:sql] == Engine100MCold::TABLE_ROWS_SQL && sample[:value] == 100_000_000
  end && successful_worlds.sum { |w| w[:calls].count(Engine100MCold::TABLE_ROWS_SQL) } == 12
end
['NULL', '0'].each do |raw|
  c, stage, world = synthetic_controller('InnoDB', table_rows_raw: raw)
  metadata = c.table_rows_reference
  assert.call("TABLE_ROWS #{raw} preserved without false empty/count truth") do
    raw == 'NULL' ? metadata[:value].nil? && metadata[:signed_error_pct].nil? && metadata[:status] == 'unknown_not_zero' :
      metadata[:value] == 0 && metadata[:signed_error_pct] == -100.0 && metadata[:status] == 'observed_zero_reference_not_empty_proof'
  end
  c.stop_boot('offline-only')
end
c, stage, world = synthetic_controller('InnoDB', pending_ping: 1)
assert.call('failed nonmeasurement ping retries before first persistent CLI; no latent client exit failure after load') do
  events = world[:events]
  first_client = events.index { |event| event[0] == :client_open }
  events.take(first_client).count { |event| event[0] == :ping } == 2 &&
    stage[:boots].first[:readiness_failures] == [{ ping_exit_status: 1 }] &&
    world[:processes].select { |item| item[:tag].end_with?('-mysql-client') }.all? { |item| item[:exit_status].nil? || item[:exit_status] == 0 }
end
c.stop_boot('offline-only')
source_hashes = Engine100MCold::SOURCE.to_h { |p| [p, Digest::SHA256.file(File.join(root, p)).hexdigest] }
puts JSON.pretty_generate({ status: 'offline_checks_passed_no_runtime_started', checks: checks.length, baseline_sha256: Digest::SHA256.file(baseline_path).hexdigest,
                           protected_files: baseline.fetch('protectedFiles').length, source_sha256: source_hashes,
                           standalone_report_sha256: Digest::SHA256.file(File.join(root, 'docs/mysql-engine-count-100m.md')).hexdigest })
puts 'OFFLINE_VERIFICATION_FINAL_EXIT_STATUS=0'
