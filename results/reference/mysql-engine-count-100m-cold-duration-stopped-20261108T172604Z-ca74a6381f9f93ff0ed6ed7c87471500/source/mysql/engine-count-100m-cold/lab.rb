# frozen_string_literal: true
# Stdlib only. Requiring this file is offline; only `all` may execute a lifecycle.
require 'json'
require 'digest'
require 'fileutils'
require 'securerandom'
require 'time'
require 'uri'
require 'rbconfig'
require 'timeout'

module Engine100MCold
  ROOT = File.realpath('../..', __dir__)
  IMAGE = 'mysql:8.4.13@sha256:a81d043930e51745522bd5c430961cf8286f0a23f0a9a2a6e0108f7ea71b0902'
  NAME = 'lab-mysql-engine-count-100m-cold'
  PROJECT = 'lab-mysql-engine-count-100m-cold'
  LABEL = 'count.engine100m.cold.owner'
  PASSWORD = 'public-disposable-engine100m-cold'
  ENGINES = %w[InnoDB MyISAM].freeze
  CONDITIONS = { 'InnoDB' => %w[innodb_primary_only innodb_secondary_present innodb_composite_secondary], 'MyISAM' => %w[myisam_primary_only] }.freeze
  ROWS = 100_000_000
  BATCH = 100_000
  BATCHES = 1000
  SOURCE = %w[lab.rb reference_flow.rb compose.yaml mise.toml README.md verify_offline.rb].map { |p| "mysql/engine-count-100m-cold/#{p}" }.push('results/reviews/mysql-engine-count-100m-cold/manual-sources/mysql-innodb.md', 'results/reviews/mysql-engine-count-100m-cold/manual-sources/source.json').freeze
  def self.condition_model(id)
    { id: id, expected_counts: 3, counts: (1..3).map { |i| { index: i, status: 'not_started', expected_value: ROWS, server_elapsed_ns: nil } },
      completion: 'incomplete', precision: 'unverified', completed_valid_counts: 0 }
  end
  def self.condition_exact?(condition)
    condition[:expected_counts] == 3 && condition[:counts].length == 3 &&
      condition[:counts].map { |s| s[:index] } == [1, 2, 3] &&
      condition[:counts].all? { |s| s[:status] == 'valid' && s[:value] == ROWS && s[:caller_elapsed_ns].is_a?(Integer) && s[:caller_elapsed_ns].positive? && s[:full_request_elapsed_ns].is_a?(Integer) && s[:full_request_elapsed_ns] >= s[:caller_elapsed_ns] }
  end
  def self.stage_exact?(stage)
    expected = CONDITIONS[stage[:engine]]
    expected && stage[:batches].length == BATCHES && stage[:batches].each_with_index.all? { |b, i| b[:batch] == i + 1 && b[:status] == 'valid' && b[:rows] == BATCH } &&
      stage[:conditions].map { |c| c[:id] } == expected && stage[:conditions].all? { |c| condition_exact?(c) }
  end
  def self.run_exact?(stages)
    stages.map { |s| s[:engine] } == ENGINES && stages.all? { |s| stage_exact?(s) }
  end
  COLD_GLOBALS = {
    'version' => '8.4.13', 'innodb_buffer_pool_size' => '1073741824', 'key_buffer_size' => '134217728',
    'innodb_redo_log_capacity' => '1073741824', 'innodb_buffer_pool_load_at_startup' => 'OFF',
    'innodb_buffer_pool_dump_at_shutdown' => 'OFF', 'innodb_flush_method' => 'O_DIRECT', 'innodb_fast_shutdown' => '1',
    'skip_networking' => 'ON', 'log_bin' => 'OFF', 'socket' => '/labdata/mysql.sock',
    'datadir' => '/labdata/data/', 'tmpdir' => '/labdata/tmp', 'pid_file' => '/labdata/mysql.pid'
  }.freeze
  def self.cold_settings?(actual)
    COLD_GLOBALS.all? { |k, v| actual[k] == v } && %w[aarch64 arm64].include?(actual['version_compile_machine'])
  end
  def self.boot_terminal?(boot)
    boot && !boot[:failure] && !boot[:stop_failure] && boot.dig(:stop, :completion) == 'clean_stopped_reaped' && boot.dig(:stop, :command_exit_status) == 0 &&
      boot.dig(:container_final_state, 'Running') == false && boot.dig(:container_final_state, 'ExitCode') == 0 &&
      !boot.dig(:container_final_state, 'OOMKilled') && boot.dig(:attach, :reaped) && boot.dig(:attach, :exit_status) == 0 && !boot.dig(:attach, :forced_termination)
  end
  def self.stage_cold?(stage)
    samples = stage[:conditions].flat_map { |c| c[:counts] }
    boots = stage.fetch(:boots, [])
    ids = samples.map { |sample| sample[:boot_ordinal] }
    samples.length == CONDITIONS.fetch(stage[:engine]).length * 3 && ids.compact.length == samples.length && ids.uniq.length == samples.length &&
      boots.length == samples.length + 1 && boots.first[:purpose] == 'initial-load' && boots.all? { |b| boot_terminal?(b) } &&
      samples.all? do |sample|
        boot = boots.find { |b| b[:ordinal] == sample[:boot_ordinal] }
        previous = boots[boots.index(boot).to_i - 1] if boot
        session = sample[:count_session] || {}
        reset = sample[:reset] || {}
        boot && previous && boot[:purpose] == 'count' && boot[:counts_started] == 1 && boot[:cold_globals_verified] &&
          reset[:status] == 'verified' && reset[:previous_boot_terminal] && reset[:previous_boot_ordinal] == previous[:ordinal] &&
          reset[:boot_ordinal] == boot[:ordinal] && reset[:same_container_id] == boot[:container_id] && reset[:same_datadir] == boot[:datadir] && reset[:owner_nonce] == boot[:owner_nonce] &&
          previous[:container_id] == boot[:container_id] && previous[:datadir] == boot[:datadir] && previous[:server_uuid] == boot[:server_uuid] &&
          session[:connection_id].is_a?(Integer) && session[:connection_id].positive? && session[:identity] == [boot[:ordinal], boot[:server_uuid], session[:connection_id]] && session[:boot_ordinal] == boot[:ordinal] &&
          session[:server_uuid] == boot[:server_uuid] && sample[:cold_buffer_policy] == 'verified_fresh_process_preload_off_metadata_pages_disclosed' &&
          sample.dig(:client_terminal, :reaped) && sample.dig(:client_terminal, :exit_status) == 0
      end
  end
  def self.summarize(stages)
    return unless run_exact?(stages) && stages.all? { |stage| stage_cold?(stage) }
    stages.flat_map { |stage| stage[:conditions] }.each do |condition|
      times = condition[:counts].map { |sample| sample.fetch(:caller_elapsed_ns) }.sort
      condition[:caller_ns_summary] = { median: times[1], min: times.first, max: times.last, samples: 3 }
    end
  end
  def self.ratios(stages)
    return {} unless run_exact?(stages) && stages.all? { |stage| stage_cold?(stage) } && stages.flat_map { |s| s[:conditions] }.all? { |c| c[:caller_ns_summary] } && stages.all? { |s| s[:completion] == 'passed' && s[:conditions].all? { |c| c[:completion] == 'passed' } }
    medians = stages.flat_map { |s| s[:conditions] }.to_h { |c| [c[:id], c.fetch(:caller_ns_summary).fetch(:median)] }
    return {} unless medians.values.all?(&:positive?)
    { innodb_primary_over_myisam_primary: medians['innodb_primary_only'].fdiv(medians['myisam_primary_only']),
      innodb_secondary_present_over_myisam_primary: medians['innodb_secondary_present'].fdiv(medians['myisam_primary_only']),
      innodb_primary_over_innodb_secondary_present: medians['innodb_primary_only'].fdiv(medians['innodb_secondary_present']),
      innodb_primary_over_composite: medians['innodb_primary_only'].fdiv(medians['innodb_composite_secondary']),
      composite_over_narrow: medians['innodb_composite_secondary'].fdiv(medians['innodb_secondary_present']) }
  end
  def self.ns = Process.clock_gettime(Process::CLOCK_MONOTONIC, :nanosecond)
  def self.ddl(engine)
    raise 'invalid engine' unless ENGINES.include?(engine)
    columns = ['id BIGINT UNSIGNED NOT NULL PRIMARY KEY'] + (1..99).map { |i| format('c%03d INT NOT NULL', i) }
    "CREATE TABLE lab.count_target (#{columns.join(',')}) ENGINE=#{engine};"
  end
  def self.seed
    digits = '(SELECT 0 AS d UNION ALL SELECT 1 UNION ALL SELECT 2 UNION ALL SELECT 3 UNION ALL SELECT 4 UNION ALL SELECT 5 UNION ALL SELECT 6 UNION ALL SELECT 7 UNION ALL SELECT 8 UNION ALL SELECT 9)'
    number = (0..4).map { |i| "d#{i}.d*#{10**i}" }.join('+')
    from = (0..4).map { |i| "#{digits} d#{i}" }.join(' CROSS JOIN ')
    "CREATE DATABASE lab;CREATE TABLE lab.seq (n INT NOT NULL PRIMARY KEY) ENGINE=InnoDB;INSERT INTO lab.seq SELECT 1+#{number} AS n FROM #{from} ORDER BY n;SELECT ROW_COUNT();"
  end
  def self.values_for(index)
    id = "#{index * BATCH}+n"
    [id, "MOD((#{id})*104729,100000)",
     "CASE WHEN MOD(FLOOR((#{id})/100000),10)<8 THEN 0 ELSE MOD(FLOOR((#{id})/100000),1000)+10 END",
     "MOD((#{id})*99991,1000003)"] + (4..99).map(&:to_s)
  end
  def self.batch_sql(index)
    raise 'invalid batch' unless (0...BATCHES).cover?(index)
    values = values_for(index)
    # ROW_COUNT is read immediately after INSERT, and boundary comes after COMMIT.
    "START TRANSACTION;INSERT INTO lab.count_target SELECT #{values.join(',')} FROM lab.seq ORDER BY n;SELECT ROW_COUNT();COMMIT;"
  end
  COUNT_SQL = 'SELECT COUNT(*) FROM lab.count_target;'
  TABLE_ROWS_SQL = "SELECT TABLE_ROWS FROM information_schema.TABLES WHERE TABLE_SCHEMA='lab' AND TABLE_NAME='count_target';"

  class Controller
    attr_reader :result
    def initialize
      @owner = SecureRandom.hex(16)
      @run = File.join(ROOT, 'results/mysql-engine-count-100m-cold/runs', "#{Time.now.utc.strftime('%Y%m%dT%H%M%SZ')}-#{@owner}")
      FileUtils.mkdir_p(@run)
      @log = File.open(File.join(@run, 'controller.log'), 'a'); @log.sync = true
      @env = { 'PATH' => ENV.fetch('PATH'), 'HOME' => Dir.home, 'LANG' => 'C.UTF-8', 'DOCKER_CONFIG' => File.join(Dir.home, '.docker') }
      @start = Engine100MCold.ns
      @deadline = @start + 6 * 3600 * 1_000_000_000
      @sequence = 0
      @processes = []
      @result = { status: 'incomplete', exit_status: 1, run_id: @owner, started_at: Time.now.utc.iso8601,
                  source_before: hashes, commands: [], processes: @processes, capacities: [], stages: [],
                  planned_stages: ENGINES, planned_conditions: CONDITIONS.values.flatten.map { |id| Engine100MCold.condition_model(id) },
                  expected_counts_per_condition: 3, expected_counts_total: 12, completion: 'incomplete', precision: 'unverified',
                  cold_buffer_policy: 'unverified', cache_scope: 'Fresh owned mysqld before EACH COUNT, preload/dump OFF; metadata/background pages disclosed; OS/VM/macOS/device caches unknown and may remain',
                  service_budget: 'UNAVAILABLE_static_catalogue_no_service_workload', cleanup: 'unverified',
                  timing_scope: 'primary caller_elapsed_ns: before persistent CLI write to first complete scalar line arrival before raw logging; full_request_elapsed_ns also waits literal boundary; includes stream/socket/docker transport',
                  server_timing: 'unavailable_not_collected_no_instrumentation_enabled',
                  order_confounding: 'fixed InnoDB PK/narrow/composite then fresh MyISAM; process reset before every count; OS/VM/host caches and index build warming remain confounded',
                  ruby: RUBY_DESCRIPTION, host_uid: Process.uid, host_gid: Process.gid, host_cpu: RbConfig::CONFIG['host_cpu'] }
    end
    def hashes
      SOURCE.to_h do |f|
        p = File.join(ROOT, f)
        raise "source symlink or missing: #{f}" unless File.file?(p) && !File.symlink?(p)
        [f, Digest::SHA256.file(p).hexdigest]
      end
    end
    def note(message)
      line = "#{Time.now.utc.iso8601} #{message}"
      puts line; $stdout.flush; @log.puts(line)
    end
    def save
      path = File.join(@run, 'result.json')
      File.write(path + '.tmp', JSON.pretty_generate(@result) + "\n")
      File.rename(path + '.tmp', path)
    end
    def limit(seconds)
      [Engine100MCold.ns + (seconds * 1_000_000_000).to_i, @cleanup_deadline || @deadline].min
    end
    def spawn_owned(argv, tag, input: File::NULL, output: nil, error: nil)
      @sequence += 1
      base = File.join(@run, format('%04d-%s', @sequence, tag))
      out = output || File.open(base + '.stdout.log', 'wb')
      err = error || File.open(base + '.stderr.log', 'wb')
      record = { argv: argv, tag: tag, stdout: output ? 'stream_recorded_by_client' : base + '.stdout.log', stderr: error ? error.path : base + '.stderr.log', exit_status: nil }
      pid = Process.spawn(@env, *argv, chdir: __dir__, in: input, out: out, err: err, unsetenv_others: true)
      record[:pid] = pid
      record[:spawned_at] = Time.now.utc.iso8601(6)
      @processes << record
      [pid, record]
    ensure
      out.close if out && !output
      err.close if err && !error
    end
    def reap(pair, nonblock: false)
      return pair[1][:exit_status] unless pair[1][:exit_status].nil?
      waited = Process.waitpid2(pair[0], nonblock ? Process::WNOHANG : 0)
      return nil unless waited
      st = waited[1]
      pair[1][:exit_status] = st.exitstatus || 128 + st.termsig
      pair[1][:signal] = st.termsig
      pair[1][:reaped] = true
      pair[1][:reaped_at] = Time.now.utc.iso8601(6)
      pair[1][:exit_status]
    end
    def terminate(pair)
      return unless pair && reap(pair, nonblock: true).nil?
      pair[1][:forced_termination] = true
      Process.kill('TERM', pair[0]) rescue Errno::ESRCH
      grace = limit(2)
      until Engine100MCold.ns >= grace
        return unless reap(pair, nonblock: true).nil?
        sleep 0.05
      end
      Process.kill('KILL', pair[0]) rescue Errno::ESRCH
      reap(pair)
    end
    def tick(stop, monitor: true)
      raise 'deadline exceeded' if Engine100MCold.ns >= stop
      if monitor && @stage && !@cleaning && Engine100MCold.ns >= (@next_capacity || 0)
        capacity('watch', minimum: 12 * 1024**3)
        @next_capacity = Engine100MCold.ns + 3_000_000_000
        raise 'deadline exceeded during capacity check' if Engine100MCold.ns >= stop
      end
      if monitor && @server && @server_ready && !@cleaning
        raise 'owned server attach exited unexpectedly' unless reap(@server, nonblock: true).nil?
      end
    end
    def command(argv, tag, seconds: 15, check: true, monitor: true, deadline: nil)
      stop = limit(seconds)
      stop = [stop, deadline].min if deadline
      tick(stop, monitor: false)
      pair = spawn_owned(argv, tag)
      @result[:commands] << pair[1]
      begin
        while reap(pair, nonblock: true).nil?
          tick(stop, monitor: monitor)
          sleep 0.05
        end
      rescue Exception => e
        pair[1][:failure] = "#{e.class}: #{e.message}"
        pair[1][:timed_out] = Engine100MCold.ns >= stop
        terminate(pair)
        raise
      ensure
        terminate(pair) if pair[1][:exit_status].nil?
      end
      code = pair[1][:exit_status]
      note("command #{tag} exit=#{code} stdout=#{pair[1][:stdout]} stderr=#{pair[1][:stderr]}")
      raise "#{tag} exit #{code}" if check && code != 0
      [File.binread(pair[1][:stdout]), code]
    end
    def docker(args, tag, **opts) = command(@cli + args, tag, **opts)
    def capacity(tag, minimum: nil)
      out, = command(['/bin/df', '-Pk', @data_root], "host-df-#{tag}", seconds: 5, monitor: false)
      fields = out.lines.last&.split
      raise 'unparseable host df' unless fields && fields.length >= 6 && fields[3].match?(/\A\d+\z/)
      free = fields[3].to_i * 1024
      @result[:capacities] << { at: Time.now.utc.iso8601, engine: @stage&.dig(:engine), tag: tag, available_bytes: free, raw: out }
      raise "host capacity below #{minimum} bytes: #{free}" if minimum && free < minimum
      free
    end
    def count_preflight(stop)
      capacity('before-count', minimum: 12 * 1024**3)
      @next_capacity = Engine100MCold.ns + 3_000_000_000
      tick(stop)
    end
    def inventory
      {
        'container' => docker(%w[container ls -a --format {{.Names}}], 'inventory-containers', monitor: false)[0].lines.map(&:strip),
        'volume' => docker(%w[volume ls --format {{.Name}}], 'inventory-volumes', monitor: false)[0].lines.map(&:strip),
        'network' => docker(%w[network ls --format {{.Name}}], 'inventory-networks', monitor: false)[0].lines.map(&:strip)
      }
    end
    def project_inventory
      %w[container volume network].to_h do |kind|
        flags = kind == 'container' ? %w[ls -a] : ['ls']
        out, = docker([kind] + flags + ['--filter', "label=com.docker.compose.project=#{PROJECT}", '--format', '{{.ID}}'], "project-#{kind}", monitor: false)
        [kind, out.lines.map(&:strip).reject(&:empty?)]
      end
    end
    def owned_inspect
      obj = JSON.parse(docker(['inspect', @container_id || NAME], 'inspect-owned', monitor: false, deadline: @boot_readiness_deadline)[0]).fetch(0)
      raise 'refusing unowned container' unless obj.dig('Config', 'Labels', LABEL) == @owner && obj.dig('Config', 'Labels', 'com.docker.compose.project') == PROJECT
      obj
    end
    def private_directory(path)
      if File.exist?(path) || File.symlink?(path)
        st = File.lstat(path)
        raise "unsafe directory #{path}" unless st.directory? && !st.symlink? && st.uid == Process.uid
      else
        Dir.mkdir(path, 0o700)
      end
      raise "non-real directory #{path}" unless File.realpath(path) == path
    end
    def preflight
      raise 'root execution prohibited; use host nonroot uid' if Process.uid.zero?
      @result[:host_uname] = command(['/usr/bin/uname', '-a'], 'host-uname', monitor: false)[0]
      @result[:host_memory_cpu] = command(['/usr/sbin/sysctl', 'hw.memsize', 'hw.ncpu'], 'host-memory-cpu', monitor: false)[0]
      @result[:host_model_chip_cores] = command(['/usr/sbin/sysctl', 'hw.model', 'machdep.cpu.brand_string', 'hw.perflevel0.physicalcpu', 'hw.perflevel1.physicalcpu'], 'host-model-chip-cores', monitor: false)[0]
      @result[:host_macos] = command(['/usr/bin/sw_vers'], 'host-macos', monitor: false)[0]
      config = File.join(Dir.home, '.docker', 'config.json')
      context = JSON.parse(File.read(config)).fetch('currentContext', 'default')
      raise 'invalid context name' unless context.is_a?(String) && context.match?(/\A[a-zA-Z0-9_.-]+\z/)
      out, = command(['docker', 'context', 'inspect', context], 'context', monitor: false)
      endpoint = JSON.parse(out).fetch(0).fetch('Endpoints').fetch('docker').fetch('Host')
      uri = URI.parse(endpoint)
      raise 'only existing local Unix socket allowed' unless uri.scheme == 'unix' && (uri.host.nil? || uri.host.empty?) && uri.query.nil? && uri.fragment.nil? && uri.path.start_with?('/') && File.socket?(uri.path)
      @cli = ['docker', '--host', endpoint]
      @result[:context] = { name: context, endpoint: endpoint, socket_realpath: File.realpath(uri.path), config_sha256: Digest::SHA256.file(config).hexdigest }
      @result[:docker_info] = JSON.parse(docker(['info', '--format', '{{json .}}'], 'docker-info', monitor: false)[0]).slice('ID', 'ServerVersion', 'OSType', 'Architecture', 'NCPU', 'MemTotal', 'DockerRootDir', 'OperatingSystem')
      raise 'insufficient Docker CPU/memory' unless @result[:docker_info].fetch('NCPU') >= 3 && @result[:docker_info].fetch('MemTotal') >= 5 * 1024**3
      @result[:inventory_before] = inventory
      raise 'reserved resource name exists' if @result[:inventory_before].values.flatten.include?(NAME)
      @result[:project_before] = project_inventory
      raise 'preexisting project resources' unless @result[:project_before].values.all?(&:empty?)
      @result[:image] = JSON.parse(docker(['image', 'inspect', IMAGE], 'image-identity', monitor: false)[0]).fetch(0)
      image = @result[:image]
      raise 'image digest missing' unless image.fetch('RepoDigests').any? { |r| r.end_with?('@' + IMAGE.split('@').last) }
      @result[:architecture] = { host: RbConfig::CONFIG['host_cpu'], daemon: @result[:docker_info]['Architecture'], image: image['Architecture'], platform: image['Os'], native_expected: 'arm64', no_platform_override: true }
      raise 'native ARM64 host/daemon/image required' unless %w[arm64 aarch64].include?(@result[:architecture][:host]) && %w[arm64 aarch64].include?(@result[:architecture][:daemon]) && image['Architecture'] == 'arm64' && image['Os'] == 'linux'
      private_directory(File.join(ROOT, '.data'))
      @data_root = File.join(ROOT, '.data/engine-count-100m-cold')
      private_directory(@data_root)
      raise 'data parent must be private' unless File.stat(@data_root).mode & 0o077 == 0
      @owner_root = File.join(@data_root, @owner)
      Dir.mkdir(@owner_root, 0o700)
      @marker = File.join(@owner_root, '.owner.json')
      File.write(@marker, JSON.generate({ nonce: @owner, root: @owner_root, uid: Process.uid }), mode: 'wx', perm: 0o600)
      @result[:data_owner_root] = @owner_root
      @result[:environment] = @env.dup # sanitized allowlist only
      capacity('preflight', minimum: 70 * 1024**3)
    end
    def guard_stage
      raise 'owner path mismatch' unless @owner_root == File.join(@data_root, @owner) && File.realpath(@owner_root) == @owner_root && File.stat(@owner_root).uid == Process.uid && File.stat(@owner_root).mode & 0o077 == 0
      raise 'unsafe owner marker' if File.symlink?(@marker)
      marker = JSON.parse(File.read(@marker))
      raise 'owner marker mismatch' unless marker == { 'nonce' => @owner, 'root' => @owner_root, 'uid' => Process.uid }
      if @stage_path && (File.exist?(@stage_path) || File.symlink?(@stage_path))
        expected = File.join(@owner_root, @stage.fetch(:engine), 'stage')
        raise 'unsafe stage path' unless @stage_path == expected && File.realpath(File.dirname(@stage_path)) == File.dirname(@stage_path) && File.realpath(@stage_path) == @stage_path && File.stat(@stage_path).uid == Process.uid
      end
    end
    def new_stage(engine)
      @stage = { engine: engine, batches: [], conditions: CONDITIONS.fetch(engine).map { |id| Engine100MCold.condition_model(id) },
                 boots: [], cold_buffer_policy: 'unverified', expected_batches: BATCHES, expected_counts: CONDITIONS.fetch(engine).length * 3, completion: 'incomplete', precision: 'unverified', cleanup: 'unverified' }
      @result[:stages] << @stage
      capacity('before-stage', minimum: 70 * 1024**3)
      engine_root = File.join(@owner_root, engine)
      Dir.mkdir(engine_root, 0o700)
      @stage_path = File.join(engine_root, 'stage')
      Dir.mkdir(@stage_path, 0o700)
      Dir.mkdir(File.join(@stage_path, 'data'), 0o700)
      Dir.mkdir(File.join(@stage_path, 'tmp'), 0o700)
      guard_stage
      raise 'datadir not empty' unless Dir.empty?(File.join(@stage_path, 'data'))
      @env.merge!('EC_UID' => Process.uid.to_s, 'EC_GID' => Process.gid.to_s, 'EC_OWNER' => @owner, 'EC_STAGE' => @stage_path)
      @stage[:compose_environment] = @env.select { |k, _| k.start_with?('EC_') }
      compose = ['compose', '--project-directory', __dir__, '--env-file', File::NULL, '-p', PROJECT, '-f', File.join(__dir__, 'compose.yaml')]
      @stage[:rendered_compose] = docker(compose + ['config'], 'compose-config')[0]
      @may_have_container = true # Even a failing create can have partially created resources.
      docker(compose + %w[create --no-recreate --no-build --pull never], 'compose-create', seconds: 30)
      obj = owned_inspect
      @container_id = obj.fetch('Id')
      mounts = obj.fetch('Mounts')
      raise 'unexpected mounts' unless mounts.all? { |m| m['Type'] == 'tmpfs' || (m['Type'] == 'bind' && m['Source'] == @stage_path && m['Destination'] == '/labdata') }
      raise 'incorrect isolation/resources' unless obj.dig('HostConfig', 'NetworkMode') == 'none' && obj.dig('HostConfig', 'Memory') == 4 * 1024**3 && obj.dig('HostConfig', 'NanoCpus') == 3_000_000_000 && obj.dig('Config', 'User') == "#{Process.uid}:#{Process.gid}" && obj.dig('HostConfig', 'PortBindings').to_h.empty?
      raise 'image ID differs from preflight' unless obj['Image'] == @result[:image]['Id']
      @stage[:container_before_start] = obj
      @stage[:boots] = []
      start_boot('initial-load')
      restart_client('fixture-loader')
      seed = @client.query(Engine100MCold.seed, seconds: 60)
      raise 'seed receipt mismatch' unless seed[:lines] == [BATCH.to_s]
      @client.query(Engine100MCold.ddl(engine))
      @stage[:schema] = @client.query("SELECT COLUMN_NAME,COLUMN_TYPE,IS_NULLABLE,COLUMN_KEY FROM information_schema.columns WHERE TABLE_SCHEMA='lab' AND TABLE_NAME='count_target' ORDER BY ORDINAL_POSITION;")[:lines]
      expected = ["id\tbigint unsigned\tNO\tPRI"] + (1..99).map { |i| format("c%03d\tint\tNO\t", i) }
      raise '100-column schema mismatch' unless @stage[:schema] == expected
      @stage[:table_definition] = @client.query('SHOW CREATE TABLE lab.count_target;')[:lines]
      @stage[:indexes] = @client.query('SHOW INDEX FROM lab.count_target;')[:lines]
      raise 'unexpected index' unless @stage[:indexes].length == 1 && @stage[:indexes][0].split("\t")[2] == 'PRIMARY'
      @stage[:engine_receipt] = @client.query("SELECT ENGINE FROM information_schema.tables WHERE TABLE_SCHEMA='lab' AND TABLE_NAME='count_target';")[:lines]
      raise 'engine mismatch' unless @stage[:engine_receipt] == [engine]
      save
    end
    def measure_stage
      load_start = Engine100MCold.ns
      load_deadline = limit(7200)
      BATCHES.times do |i|
        receipt = { batch: i + 1, expected_rows: BATCH, status: 'incomplete' }
        @stage[:batches] << receipt
        begin
          response = @client.query(Engine100MCold.batch_sql(i), seconds: 7200, deadline: load_deadline)
        rescue Exception
          receipt[:failure] = @client.last_failure
          raise
        end
        receipt.merge!(raw: response[:lines], caller_elapsed_ns: response[:elapsed_ns], acknowledged_after_commit: true)
        raise "batch #{i + 1} receipt mismatch" unless response[:lines] == [BATCH.to_s]
        receipt[:rows] = BATCH
        receipt[:status] = 'valid'
        @stage[:accumulated_rows] = @stage[:batches].sum { |b| b.fetch(:rows, 0) }
        @stage[:load_elapsed_ns] = Engine100MCold.ns - load_start
        if (i + 1) % 50 == 0
          elapsed = @stage[:load_elapsed_ns] / 1e9
          rate = @stage[:accumulated_rows] / elapsed
          note("#{@stage[:engine]} rows=#{@stage[:accumulated_rows]} elapsed_s=#{elapsed} rows_per_s=#{rate} rough_eta_s=#{(ROWS - @stage[:accumulated_rows]) / rate}")
        end
        save
      end
      raise 'load stage deadline exceeded' if Engine100MCold.ns >= load_deadline
      raise 'total rows mismatch' unless @stage[:accumulated_rows] == ROWS
      measure_condition(@stage[:conditions].first)
      if @stage[:engine] == 'InnoDB'
        restart_client("fixture-index-k_count")
        raise 'active request before index build' if @client.active?
        capacity('before-index-build', minimum: 24 * 1024**3) # 12 GiB floor + 12 GiB working headroom
        build = { status: 'incomplete', sql: 'ALTER TABLE lab.count_target ADD INDEX k_count(c001), ALGORITHM=INPLACE, LOCK=NONE;' }
        @stage[:index_build] = build
        save
        begin
          response = @client.query(build[:sql], seconds: 7200)
          build.merge!(raw: response[:lines], elapsed_ns: response[:elapsed_ns])
          raise 'unexpected index-build scalar' unless response[:lines].empty?
          build[:status] = 'passed'
        rescue Exception
          build[:failure] = @client.last_failure
          raise
        ensure
          save
        end
        measure_condition(@stage[:conditions][1])
        narrow = @stage[:conditions][1]
        observed = narrow[:indexes].map { |line| line.split("\t") }.select { |f| f[2] == 'k_count' }
        raise 'cannot assess narrow index shape' unless observed.length == 1 && observed.first[4] == 'c001'
        @stage[:index_refinement_assessment] = {
          decision: 'add_wider_composite_reference', observed_index_rows: observed,
          selected_key: narrow.dig(:manual_reference, :selected_index),
          reason: 'Actual catalogue is a single INT secondary. Valid narrow baseline but insufficient as sole wider-index reference; add three-column case on unchanged varied data.',
          records: 'c001 permuted 100k values, c002 80-percent-zero skew by id blocks, c003 high-cardinality modular sequence; fixed-width INT payload remains 404 bytes',
          row_data_changed_after_baseline: false }
        save
        restart_client('fixture-index-composite')
        capacity('before-composite-index-build', minimum: 24 * 1024**3)
        composite = { status: 'incomplete', sql: 'ALTER TABLE lab.count_target DROP INDEX k_count, ADD INDEX k_count_v2(c001,c002,c003), ALGORITHM=INPLACE, LOCK=NONE;' }
        @stage[:composite_index_build] = composite
        save
        begin
          response = @client.query(composite[:sql], seconds: 7200)
          composite.merge!(raw: response[:lines], elapsed_ns: response[:elapsed_ns])
          raise 'unexpected composite-index scalar' unless response[:lines].empty?
          composite[:status] = 'passed'
        rescue Exception
          composite[:failure] = @client.last_failure
          raise
        ensure
          save
        end
        measure_condition(@stage[:conditions].last)
      end
      raise 'incomplete stage model' unless Engine100MCold.stage_exact?(@stage)
      @stage[:precision] = 'passed_1000_batch_receipts_and_all_condition_scalars'
      @stage[:completion] = 'passed'
    ensure
      @stage[:load_elapsed_ns] = Engine100MCold.ns - load_start if load_start && @stage[:batches].length < BATCHES
    end
    def capture_sample_reference(condition, sample)
      sample[:index_catalogue] = reference_query('SHOW INDEX FROM lab.count_target;')
      sample[:indexes] = sample[:index_catalogue][:raw]
      expected = [['PRIMARY', '1', 'id', '0']]
      expected << ['k_count', '1', 'c001', '1'] if condition[:id] == 'innodb_secondary_present'
      if condition[:id] == 'innodb_composite_secondary'
        expected += [['k_count_v2', '1', 'c001', '1'], ['k_count_v2', '2', 'c002', '1'], ['k_count_v2', '3', 'c003', '1']]
      end
      actual = sample[:indexes].map { |line| f = line.split("\t"); [f[2], f[3], f[4], f[1]] }.sort
      raise 'condition index catalog mismatch' unless actual == expected.sort
      sample[:manual_reference] = capture_manual_reference(sample)
      sample[:plan] = sample[:manual_reference][:explain][:raw]
      raise 'missing plan' if sample[:plan].empty?
    end
    def measure_condition(condition)
      condition[:counts].each do |sample|
        sample[:label] = sample[:index] == 1 ? (condition[:id] != 'innodb_primary_only' && @stage[:engine] == 'InnoDB' ? 'first_post_index_build' : 'first_post_load') : 'repeat'
        sample[:status] = 'incomplete'
        save
        begin
          reset_before_count(sample)
          restart_client("count-#{condition[:id]}-#{sample[:index]}", count: true, condition: sample)
          sample[:buffer_at_start] = buffer_snapshot
          capture_sample_reference(condition, sample)
          sample[:buffer_before_count] = buffer_snapshot
          assess_cold_sample(sample)
          # Per-condition fields are explicitly representative of its first sample, never reused as another sample's proof.
          if sample[:index] == 1
            %i[indexes manual_reference plan count_session].each { |key| condition[key] = sample[key] }
            condition[:representative_sample_index] = 1
          end
          sample[:status_before] = status_counters
          response = @client.query(COUNT_SQL, seconds: 1200, scalar: true)
          sample.merge!(caller_elapsed_ns: response[:scalar_elapsed_ns], full_request_elapsed_ns: response[:elapsed_ns], raw: response[:lines])
          raise 'invalid measured count scalar' unless response[:lines] == [ROWS.to_s]
          raise 'nonpositive scalar/request timing' unless sample[:caller_elapsed_ns].is_a?(Integer) && sample[:caller_elapsed_ns].positive? && sample[:full_request_elapsed_ns].is_a?(Integer) && sample[:full_request_elapsed_ns] >= sample[:caller_elapsed_ns]
          sample[:value] = ROWS
          sample[:status_after] = status_counters
          before = sample[:status_before].to_h { |line| k, v = line.split("\t"); [k, Integer(v)] }
          after = sample[:status_after].to_h { |line| k, v = line.split("\t"); [k, Integer(v)] }
          sample[:engine_counter_deltas] = before.to_h { |k, v| [k, after.fetch(k) - v] }
          sample[:counter_scope] = 'Engine logical/physical counters include background work; not SSD/device physical reads'
          sample[:table_bytes_metadata] = reference_query("SELECT ENGINE,DATA_LENGTH,INDEX_LENGTH,DATA_FREE,ROW_FORMAT,CREATE_OPTIONS FROM information_schema.tables WHERE TABLE_SCHEMA='lab' AND TABLE_NAME='count_target';")
          @client.close(seconds: 10, aborting: false)
          sample[:client_terminal] = @client.pair[1]
          @client = nil
          sample[:status] = 'valid'
          note("#{condition[:id]} count=#{sample[:index]} value=#{ROWS} caller_elapsed_ns=#{sample[:caller_elapsed_ns]} full_request_elapsed_ns=#{sample[:full_request_elapsed_ns]}")
        rescue Exception => e
          sample[:status] = 'incomplete'
          sample[:failure] = { error: "#{e.class}: #{e.message}", client: @client&.last_failure }
          raise
        ensure
          condition[:completed_valid_counts] = condition[:counts].count { |s| s[:status] == 'valid' }
          save
        end
      end
      condition[:table_bytes_metadata] = condition[:counts].first[:table_bytes_metadata]
      raise 'condition count mismatch' unless Engine100MCold.condition_exact?(condition)
      condition[:precision] = 'passed_3_exact_scalars'
      condition[:completion] = 'passed'
      save
    end
    def status_counters
      @client.query("SHOW GLOBAL STATUS WHERE Variable_name IN ('Handler_read_first','Handler_read_next','Handler_read_rnd_next','Innodb_buffer_pool_reads','Innodb_buffer_pool_read_requests','Innodb_data_reads','Innodb_data_read','Innodb_data_written','Key_read_requests','Key_reads','Key_write_requests','Key_writes');")[:lines]
    end
    def cleanup_stage
      return unless @stage
      @cleaning = true
      @cleanup_deadline = [Engine100MCold.ns + 120_000_000_000, @deadline + 120_000_000_000].min
      @last_cleanup_deadline = @cleanup_deadline
      errors = []
      attempt = lambda { |&b| begin; b.call; rescue Exception => e; errors << "#{e.class}: #{e.message}"; end }
      attempt.call { @client&.close(seconds: 5, aborting: @stage[:completion] != 'passed') }
      @client = nil
      attempt.call { stop_boot('stage-final') } if @boot && @server_ready
      container_absent = !@may_have_container
      if @may_have_container
        attempt.call do
          names = inventory.fetch('container')
          if names.include?(NAME)
            obj = owned_inspect
            @container_id = obj.fetch('Id')
            if obj.dig('State', 'Running')
              begin
                _, code = docker(['stop', '--time', '25', @container_id], 'stop-owned', seconds: 30, check: false, monitor: false)
              rescue StandardError => e
                errors << "stop failure: #{e.class}: #{e.message}"
                code = nil
              end
              if code != 0 && owned_inspect.dig('State', 'Running')
                owned_inspect
                docker(['kill', @container_id], 'kill-owned', seconds: 10, monitor: false)
              end
            end
            obj = owned_inspect
            raise 'container remains running' if obj.dig('State', 'Running')
            @stage[:container_final_state] = obj.fetch('State')
            @boot[:container_final_state] = obj.fetch('State') if @boot
            @stage[:container_exit_status] = obj.dig('State', 'ExitCode')
            attempt.call { docker(['logs', @container_id], 'final-server-logs', seconds: 10, monitor: false) }
            owned_inspect
            docker(['rm', @container_id], 'remove-owned', seconds: 10, monitor: false)
          end
          raise 'owned name still present' if inventory.fetch('container').include?(NAME)
          raise 'project runtime resources remain' unless project_inventory.values.all?(&:empty?)
          container_absent = true
          @may_have_container = false
          @container_id = nil
        end
      end
      attempt.call do
        if @server
          stop = limit(5)
          while reap(@server, nonblock: true).nil?
            raise 'server attach failed to exit' if Engine100MCold.ns >= stop
            sleep 0.05
          end
          @stage[:server_attach_exit_status] = @server[1][:exit_status]
        end
      end
      attempt.call { terminate(@server) if @server && @server[1][:exit_status].nil? }
      @boot[:attach] = @server[1] if @boot && @server
      @server = nil
      @server_ready = false
      attempt.call do
        raise 'refuse data removal while runtime presence unknown' unless container_absent
        guard_stage
        if @stage_path && File.exist?(@stage_path)
          raise 'retain owned stage payload for owner: all counts not complete' unless Engine100MCold.stage_exact?(@stage)
          files = Dir.glob(File.join(@stage_path, 'data/lab/count_target.*')).sort
          @stage[:physical_table_files_after_stop] = files.to_h do |f|
            st = File.lstat(f)
            raise 'unsafe table file' unless st.file? && !st.symlink? && st.uid == Process.uid
            [File.basename(f), { bytes: st.size, allocated_bytes: st.blocks * 512 }]
          end
          # No arbitrary cleanup helper or permission repair. Only this nonce's guarded stage.
          remaining = (@cleanup_deadline - Engine100MCold.ns) / 1e9
          raise 'cleanup deadline exhausted before data removal' unless remaining.positive?
          Timeout.timeout(remaining) { FileUtils.remove_entry_secure(@stage_path) }
          raise 'stage remains' if File.exist?(@stage_path) || File.symlink?(@stage_path)
          Dir.rmdir(File.dirname(@stage_path))
        end
        capacity('after-stage-cleanup')
      end
      if @stage.fetch(:boots, []).any?
        errors << 'one or more boots not cleanly stopped/reaped' unless @stage[:boots].all? { |boot| Engine100MCold.boot_terminal?(boot) }
        @stage[:cold_buffer_policy] = Engine100MCold.stage_cold?(@stage) ? 'passed_all_count_boots_terminal_clean' : 'unproven'
      end
      @stage[:cleanup_errors] = errors
      @stage[:cleanup] = errors.empty? ? 'passed_container_reaped_and_host_stage_absent' : 'failed_or_unknown'
      @stage[:completed_valid_counts] = @stage[:conditions].sum { |c| c[:counts].count { |s| s[:status] == 'valid' } }
      save
      raise "stage cleanup failed: #{errors.join('; ')}" unless errors.empty?
    ensure
      @cleaning = false
      @cleanup_deadline = nil
    end
    def run
      %w[INT TERM].each { |sig| Signal.trap(sig) { raise Interrupt, sig } }
      begin
        preflight
        ENGINES.each do |engine|
          begin
            new_stage(engine)
            measure_stage
          rescue Exception => e
            @stage[:error] = "#{e.class}: #{e.message}" if @stage
            raise
          ensure
            cleanup_stage
          end
          if @stage[:container_exit_status] != 0 || @stage[:server_attach_exit_status] != 0
            raise 'nonzero server/container exit'
          end
          @stage = nil; @stage_path = nil; @last_cleanup_deadline = nil; @boot = nil
        end
        raise 'incomplete run model' unless Engine100MCold.run_exact?(@result[:stages])
      rescue Exception => e
        @result[:error] = "#{e.class}: #{e.message}"
        @result[:backtrace] = e.backtrace
        note("FAIL #{@result[:error]}")
      ensure
        @cleanup_deadline = @last_cleanup_deadline || [Engine100MCold.ns + 120_000_000_000, @deadline + 120_000_000_000].min
        begin
          raise 'runtime or stage cleanup incomplete' if @may_have_container || (@stage_path && (File.exist?(@stage_path) || File.symlink?(@stage_path)))
          if @owner_root
            guard_stage
            raise 'unexpected owner children' unless Dir.children(@owner_root) == ['.owner.json']
            File.unlink(@marker); Dir.rmdir(@owner_root)
            raise 'owner root remains' if File.exist?(@owner_root)
          end
          @result[:inventory_after] = inventory if @cli
          raise 'reserved resource remains' if @result[:inventory_after]&.values&.flatten&.include?(NAME)
          @result[:owned_runtime_and_hostdata_absent] = true
          raise 'one or more stage cleanup decisions did not pass' unless @result[:stages].all? { |stage| stage[:cleanup].start_with?('passed') }
          @result[:cleanup] = 'passed_owned_runtime_and_hostdata_absent'
        rescue Exception => e
          @result[:cleanup] = 'failed_or_unknown'
          @result[:cleanup_error] = "#{e.class}: #{e.message}"
        end
        @processes.each do |p|
          next if p[:reaped]
          begin
            terminate([p[:pid], p])
          rescue Exception => e
            @result[:cleanup] = 'failed_or_unknown'
            p[:reap_error] = "#{e.class}: #{e.message}"
          end
        end
        begin
          @result[:source_after] = hashes
        rescue Exception => e
          @result[:source_after] = { error: e.message }
        end
        @result[:source_unchanged] = @result[:source_before] == @result[:source_after]
        @result[:finished_at] = Time.now.utc.iso8601
        @result[:elapsed_ns] = Engine100MCold.ns - @start
        @result[:completed_stages] = @result[:stages].select { |s| s[:completion] == 'passed' }.map { |s| s[:engine] }
        @result[:completed_conditions] = @result[:stages].flat_map { |s| s[:conditions] }.select { |c| c[:completion] == 'passed' }.map { |c| c[:id] }
        @result[:completed_valid_counts_total] = @result[:stages].sum { |s| s[:conditions].sum { |c| c[:counts].count { |sample| sample[:status] == 'valid' } } }
        exact = Engine100MCold.run_exact?(@result[:stages])
        @result[:precision] = 'passed_exact_100000000_all_four_conditions' if exact
        @result[:completion] = 'passed_two_stages_four_conditions_twelve_counts' if exact && @result[:completed_stages] == ENGINES && @result[:completed_conditions] == CONDITIONS.values.flatten
        cold = exact && @result[:stages].all? { |stage| Engine100MCold.stage_cold?(stage) }
        @result[:cold_buffer_policy] = cold ? 'passed_all_12_fresh_process_boots_terminal_clean' : 'unproven'
        Engine100MCold.summarize(@result[:stages]) if cold
        @result[:caller_median_ratios] = cold ? Engine100MCold.ratios(@result[:stages]) : {}
        clean_clients = @processes.select { |p| p[:tag].end_with?('-mysql-client') }.all? { |p| p[:reaped] && p[:exit_status] == 0 && !p[:forced_termination] }
        @result[:all_mysql_clients_exit_zero] = clean_clients
        @result[:all_boots_terminal_clean] = @result[:stages].all? { |stage| stage.fetch(:boots, []).all? { |boot| Engine100MCold.boot_terminal?(boot) } }
        if cold && clean_clients && !@result[:error] && @result[:completion].start_with?('passed') && @result[:cleanup].start_with?('passed') && @result[:source_unchanged] && @processes.all? { |p| p[:reaped] } && @result[:stages].all? { |s| s[:client_exit_status] == 0 }
          @result[:status] = 'completed_owner_audit_and_two_reviews_required'
          @result[:exit_status] = 0
        end
        note("CONTROLLER_FINAL_EXIT_STATUS=#{@result[:exit_status]}")
        @log.close
        @result[:artifacts_sha256] = Dir.children(@run).sort.reject { |f| f == 'result.json' || f.end_with?('.tmp') }.to_h { |f| [f, Digest::SHA256.file(File.join(@run, f)).hexdigest] }
        save
        puts JSON.generate({ result: File.join(@run, 'result.json'), exit_status: @result[:exit_status] })
      end
      @result[:exit_status]
    end
    # Client gets no endpoint API: argv is fixed to the owned server's Unix socket.
    def client_spawn(engine, input, output, error)
      pair = spawn_owned(@cli + ['exec', '-i', @container_id, 'mysql', '--no-defaults', '--protocol=SOCKET', '--socket=/labdata/mysql.sock', '-uroot', "-p#{PASSWORD}", '--connect-timeout=3', '--batch', '--raw', '--skip-column-names', '--unbuffered'], "#{engine}-mysql-client", input: input, output: output, error: error)
      pair[1].merge!(phase: @client_phase, boot_ordinal: @boot&.dig(:ordinal))
      pair
    end
    def client_path(engine, suffix)
      @client_serial = (@client_serial || 0) + 1 if suffix == 'requests'
      File.join(@run, "#{engine}-client-#{@client_serial}.#{suffix}.log")
    end
    def client_owner = @owner
    def client_finished(code)
      @stage[:client_exit_status] = code
      (@stage[:client_exits] ||= []) << { phase: @client_phase, boot_ordinal: @boot&.dig(:ordinal), exit_status: code }
    end
  end

  class Client
    attr_reader :last_failure, :pair
    def active? = !!@active
    def initialize(controller, engine)
      @controller = controller
      @requests = File.open(controller.client_path(engine, 'requests'), 'wb'); @requests.sync = true
      @raw = File.open(controller.client_path(engine, 'stdout'), 'wb'); @raw.sync = true
      err = File.open(controller.client_path(engine, 'stderr'), 'wb')
      child_in, @input = IO.pipe
      @output, child_out = IO.pipe
      @pair = controller.client_spawn(engine, child_in, child_out, err)
      @pair[1][:stdout] = @raw.path
      @buffer = +''
      @serial = 0
    ensure
      child_in&.close; child_out&.close; err&.close
    end
    def query(sql, seconds: 30, deadline: nil, scalar: false)
      raise 'concurrent client request prohibited' if @active
      raise 'scalar timing only for fixed count or TABLE_ROWS' if scalar && ![COUNT_SQL, TABLE_ROWS_SQL].include?(sql)
      @active = true
      @last_failure = nil
      @serial += 1
      marker = "EC100MCOLD_#{@controller.client_owner}_#{@serial}"
      payload = "#{sql}\nSELECT '#{marker}';\n"
      @requests.puts(JSON.generate({ request: @serial, sql: sql, marker: marker }))
      stop = @controller.limit(seconds)
      stop = [stop, deadline].min if deadline
      scalar ? @controller.count_preflight(stop) : @controller.tick(stop)
      start = Engine100MCold.ns # before any byte of count request, never includes process startup
      offset = 0
      while offset < payload.bytesize
        @controller.tick(stop, monitor: false) # synchronous df was completed before starting the count timer
        if IO.select(nil, [@input], nil, 0.1)
          written = @input.write_nonblock(payload.byteslice(offset..), exception: false)
          offset += written if written.is_a?(Integer)
        end
      end
      lines = []
      scalar_elapsed = nil
      loop do
        @controller.tick(stop)
        next unless IO.select([@output], nil, nil, 0.1)
        chunk = @output.read_nonblock(65_536, exception: false)
        received = Engine100MCold.ns # timestamp immediately after read, before logging/parsing
        raise "mysql EOF before boundary request #{@serial}" if chunk.nil?
        next if chunk == :wait_readable
        @buffer << chunk
        boundary_received = false
        while (newline = @buffer.index("\n"))
          line = @buffer.slice!(0, newline + 1).delete_suffix("\n").delete_suffix("\r")
          if line == marker
            boundary_received = true
            break
          end
          scalar_elapsed ||= received - start if scalar && lines.empty?
          lines << line
        end
        @raw.write(chunk)
        raise 'deadline exceeded before response boundary' if received >= stop
        if boundary_received
          raise 'unexpected output after boundary' unless @buffer.empty?
          return { lines: lines, elapsed_ns: received - start, scalar_elapsed_ns: scalar_elapsed }
        end
      end
    rescue Exception => e
      @last_failure = { request: @serial, failure: "#{e.class}: #{e.message}", elapsed_ns: start ? Engine100MCold.ns - start : nil,
                        scalar_elapsed_ns: scalar_elapsed, raw_lines: lines || [], partial_buffer: @buffer&.dup }
      @requests.puts(JSON.generate(@last_failure))
      raise
    ensure
      @active = false
    end
    def close(seconds:, aborting:)
      return if @closed
      @closed = true
      @input.close unless @input.closed?
      stop = @controller.limit(seconds)
      begin
        loop do
          # Drain until EOF/reap, preserving trailing output even on SQL failure.
          if IO.select([@output], nil, nil, 0.05)
            chunk = @output.read_nonblock(65_536, exception: false)
            @raw.write(chunk) if chunk.is_a?(String)
          end
          break unless @controller.reap(@pair, nonblock: true).nil?
          raise 'mysql client EOF/reap timeout' if Engine100MCold.ns >= stop
        end
      ensure
        @controller.terminate(@pair) if @pair[1][:exit_status].nil?
        # Child has exited; consume pipe bytes through EOF (no writer remains).
        remaining = @output.read
        @raw.write(remaining) unless remaining.empty?
        @output.close; @raw.close; @requests.close
        @controller.client_finished(@pair[1][:exit_status])
      end
      raise "mysql client exit #{@pair[1][:exit_status]}" if !aborting && @pair[1][:exit_status] != 0
    end
  end
end

require_relative 'reference_flow'

if $PROGRAM_NAME == __FILE__
  abort 'usage: ruby lab.rb all (Ruby 4.0.9)' unless ARGV == ['all']
  abort 'Ruby 4.0.9 required' unless RUBY_VERSION == '4.0.9'
  # Keep lock descriptor throughout lifetime, and never unlink a held lock.
  lock_path = File.join(__dir__, '.lab.lock')
  abort 'unsafe lock symlink' if File.symlink?(lock_path)
  lock = File.open(lock_path, File::RDWR | File::CREAT, 0o600)
  abort 'another controller owns this lifecycle' unless lock.flock(File::LOCK_EX | File::LOCK_NB)
  exit Engine100MCold::Controller.new.run
end
