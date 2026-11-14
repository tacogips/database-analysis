#!/usr/bin/env ruby
# frozen_string_literal: true
# Ruby 4.0.9, standard library only. Sole entrypoint: all (blank lab required).
require 'json'
require 'digest'
require 'fileutils'
require 'open3'
require 'securerandom'
require 'time'
require 'rbconfig'

class CountLab
  ROOT = File.expand_path('..', __dir__)
  CONTAINER = 'lab-sqlserver-count'
  VOLUME = 'lab-sqlserver-count-data'
  NETWORK = 'lab-sqlserver-count-network'
  PROJECT = 'sqlserver-count-lab'
  IMAGE = 'mcr.microsoft.com/mssql/server:2025-latest@sha256:245f6b1c8903edae52cb20fa31419cefcde1a49938bb8c3447099011fc871ca3'
  PASSWORD = 'CountLab_Only_2026!'
  SEED = 20_261_108
  # Limits are lab experiment budgets, not service safety thresholds.
  LIMITS = { total_seconds: 3600, startup_seconds: 240, load_seconds: 480,
             optional_thin_seconds: 900, query_seconds: 45, cleanup_seconds: 180,
             database_data_mb: 7000, database_log_mb: 1024,
             tempdb_max_mb: 2048, volume_stop_bytes: 14_000_000_000 }.freeze
  FIXTURES = {
    'empty_table' => 0, 'heap_table' => 50_000, 'clustered_table' => 200_000,
    'partitioned_table' => 200_000, 'columnstore_table' => 200_000,
    'skew_gaps' => 100_000, 'wide80' => 750_000, 'wide100' => 750_000,
    'service_table' => 100_000, 'thin_100m' => 100_000_000
  }.freeze
  SOURCES = ['AGENTS.md', 'mise.toml', 'sqlserver/compose.yaml', 'sqlserver/lab.rb',
             'sqlserver/setup.sql', 'sqlserver/fixtures.sql', 'sqlserver/mise.toml',
             'results/sqlserver/plan.json'].freeze

  def initialize
    @run_id = Time.now.utc.strftime('%Y%m%dT%H%M%SZ') + '-' + SecureRandom.hex(4)
    @dir = File.join(ROOT, 'results/sqlserver/runs', @run_id)
    FileUtils.mkdir_p(@dir)
    @mutex = Mutex.new
    @counter = 0
    @session_prefix = 'cl_' + SecureRandom.hex(10) + '_'
    @start = mono
    @deadline = @start + LIMITS[:total_seconds]
    @created = false
    @container_id = nil
    @unsafe = false
    @result = { schema_version: 1, run_id: @run_id, seed: SEED,
                status: 'running', started_at: Time.now.utc.iso8601, session_prefix: @session_prefix,
                ruby_version: RUBY_VERSION, source_before: sources, limits: LIMITS,
                fixtures: {}, observations: [], commands: [],
                service: { status: 'notrun', slo: 'unavailable' },
                completion: 'incomplete', precision: 'unavailable',
                exact_method_budget: 'unavailable', service_budget: 'unavailable',
                cleanup: { status: 'not_started' }, exit_status: nil }
    @log = File.open(File.join(@dir, 'runner.log'), 'a')
    note("run_id=#{@run_id}; owned foreground lifecycle; no external DB endpoint")
  end

  def mono = Process.clock_gettime(Process::CLOCK_MONOTONIC)

  def note(message)
    @mutex.synchronize do
      @log.puts("#{Time.now.utc.iso8601} #{message}")
      @log.flush
      puts message
    end
  end

  def sources
    SOURCES.to_h { |p| [p, Digest::SHA256.file(File.join(ROOT, p)).hexdigest] }
  end

  def save
    path = File.join(@dir, 'result.json')
    File.write(path + '.tmp', JSON.pretty_generate(@result) + "\n")
    File.rename(path + '.tmp', path)
  end

  # No shell, detached processes, environment-driven Docker target or DB arguments.
  # Each child is reaped even on deadlines; SQL server sessions are checked separately.
  def command(label, argv, input: '', timeout: 60, global_budget: true)
    remaining = if @cleanup_deadline
                  @cleanup_deadline - mono
                else
                  global_budget ? @deadline - mono : timeout
                end
    raise 'total experiment budget exhausted' if remaining <= 0
    timeout = [timeout, remaining].min
    seq = @mutex.synchronize { @counter += 1 }
    stem = format('%04d-%s', seq, label.gsub(/[^a-zA-Z0-9_-]/, '_'))
    out_path = File.join(@dir, stem + '.stdout.log')
    err_path = File.join(@dir, stem + '.stderr.log')
    started = mono
    timed_out = false
    status = nil
    interrupted = nil
    # Docker local Unix socket only. Strip all inherited connection/context settings.
    env = ENV.to_h.keys.grep(/\A(?:DOCKER_|COMPOSE_|SQLCMD|MSSQL)/).to_h { |k| [k, nil] }
    raise 'local Docker endpoint not resolved' unless @docker_endpoint
    env['DOCKER_HOST'] = @docker_endpoint
    File.open(out_path, 'w') do |out|
      File.open(err_path, 'w') do |err|
        Open3.popen3(env, *argv, chdir: ROOT) do |stdin, stdout, stderr, waiter|
          readers = [Thread.new { IO.copy_stream(stdout, out) }, Thread.new { IO.copy_stream(stderr, err) }]
          begin
            begin
              stdin.write(input)
            rescue Errno::EPIPE
              # Process status/raw error below remains authoritative.
            ensure
              stdin.close
            end
            timed_out = !waiter.join(timeout)
          rescue StandardError, Interrupt => e
            interrupted = e
          ensure
            if waiter.alive?
              Process.kill('TERM', waiter.pid) rescue Errno::ESRCH
              unless waiter.join(3)
                Process.kill('KILL', waiter.pid) rescue Errno::ESRCH
              end
            end
            status = waiter.value
            # Reaping the Docker client does not establish SQL termination.
            readers.each(&:value)
          end
        end
      end
    end
    record = { label: label, argv: argv, input_sha256: Digest::SHA256.hexdigest(input),
               monotonic_start: started, seconds: mono - started, timeout_seconds: timeout,
               timed_out: timed_out, interrupted: interrupted&.class&.name,
               exit_status: status.exitstatus, signal: status.termsig,
               stdout_log: out_path.delete_prefix(ROOT + '/'), stderr_log: err_path.delete_prefix(ROOT + '/'),
               logs_complete: true }
    @mutex.synchronize { @result[:commands] << record }
    raise interrupted if interrupted
    record.merge(stdout: File.read(out_path), stderr: File.read(err_path))
  end

  def good!(record)
    raise "#{record[:label]} failed; inspect #{record[:stdout_log]} and #{record[:stderr_log]}" unless record[:exit_status] == 0 && !record[:timed_out]
    record
  end

  def docker(label, *args, timeout: 60, global_budget: true)
    command(label, ['docker', *args], timeout: timeout, global_budget: global_budget)
  end

  def compose(label, *args, timeout: 120)
    docker(label, 'compose', '--project-name', PROJECT, '--file', File.join(ROOT, 'sqlserver/compose.yaml'),
           *args, timeout: timeout)
  end

  def json_rows(record)
    lines = record[:stdout].lines.map(&:strip).grep(/\ALABJSON:/)
    raise "missing/duplicate JSON envelope: #{record[:label]}" unless lines.length == 1
    JSON.parse(lines.first.delete_prefix('LABJSON:'))
  end

  def envelope(select, array: false)
    "SELECT 'LABJSON:' + (#{select} FOR JSON PATH, INCLUDE_NULL_VALUES#{array ? '' : ', WITHOUT_ARRAY_WRAPPER'});"
  end

  def sql_raw(label, body, database: 'CountLab', timeout: 45, token: nil, global_budget: true)
    token ||= @session_prefix + SecureRandom.hex(10)
    raise 'invalid owned token' unless token.match?(/\A[a-z0-9_]{1,80}\z/)
    raise 'invalid database' unless %w[master CountLab].include?(database)
    header = "SET NOCOUNT ON; SET XACT_ABORT ON; SET LANGUAGE us_english; SET LOCK_TIMEOUT 1500;\n" \
             "SET CONTEXT_INFO 0x#{token.unpack1('H*')};\n" \
             "EXEC sys.sp_set_session_context @key=N'count_lab_job', @value=N'#{token}';\n"
    argv = ['docker', 'exec', '-i', '-e', "SQLCMDPASSWORD=#{PASSWORD}", CONTAINER,
            '/opt/mssql-tools18/bin/sqlcmd', '-S', 'localhost', '-U', 'sa', '-C',
            '-d', database, '-H', token, '-b', '-V', '11', '-r', '1', '-l', '10',
            '-t', [timeout.to_i, 1].max.to_s, '-y', '0', '-w', '65535', '-x']
    record = command(label, argv, input: header + body + "\nGO\n", timeout: timeout + 5, global_budget: global_budget)
    record[:token] = token
    @mutex.synchronize do
      @result[:commands].find { |r| r[:stdout_log] == record[:stdout_log] }[:session_token] = token
    end
    record
  end

  # Revalidate ownership at the instant of cancellation. Never KILL a saved SPID blindly.
  # Used after success as well as errors: zero matching sessions is required to continue.
  def terminate_and_verify(token)
    hex = token.unpack1('H*')
    predicate = "host_name=N'#{token}' AND SUBSTRING(context_info,1,#{token.bytesize})=0x#{hex} AND login_name=N'sa' AND is_user_process=1 AND session_id<>@@SPID"
    body = <<~SQL
      DECLARE @id int, @kill nvarchar(80);
      DECLARE owned CURSOR LOCAL FAST_FORWARD FOR SELECT session_id FROM sys.dm_exec_sessions WHERE #{predicate};
      OPEN owned; FETCH NEXT FROM owned INTO @id;
      WHILE @@FETCH_STATUS=0 BEGIN
        IF EXISTS(SELECT 1 FROM sys.dm_exec_sessions WHERE session_id=@id AND #{predicate})
        BEGIN SET @kill=N'KILL '+CONVERT(nvarchar(12),@id); EXEC(@kill); END;
        FETCH NEXT FROM owned INTO @id;
      END;
      CLOSE owned; DEALLOCATE owned;
    SQL
    good!(sql_raw('cancel-owned', body, database: 'master', timeout: 15, global_budget: false))
    deadline = mono + 45
    loop do
      query = envelope("SELECT COUNT_BIG(*) AS remaining FROM sys.dm_exec_sessions WHERE #{predicate}")
      rec = good!(sql_raw('termination-check', query, database: 'master', timeout: 10, global_budget: false))
      rows = json_rows(rec)
      raise 'invalid termination envelope' unless rows['remaining'].is_a?(Integer)
      return { status: 'verified', remaining_sessions: 0, log: rec[:stdout_log] } if rows['remaining'].zero?
      raise 'owned SQL session/rollback did not terminate' if mono >= deadline
      sleep 0.5
    end
  rescue StandardError
    @unsafe = true
    raise
  end

  def sql(label, body, **options)
    raise 'previous server termination unverified' if @unsafe
    record = sql_raw(label, body, **options)
    record[:termination] = terminate_and_verify(record[:token])
    @mutex.synchronize do
      entry = @result[:commands].find { |r| r[:stdout_log] == record[:stdout_log] }
      entry[:server_termination] = record[:termination]
    end
    record
  end

  def rows!(label, select, **options)
    json_rows(good!(sql(label, envelope(select), **options)))
  end

  def preflight
    raise "Ruby 4.0.9 required; got #{RUBY_VERSION}" unless RUBY_VERSION == '4.0.9'
    # Resolve only the user's configured local Unix context, ignoring inherited
    # endpoint/context overrides. TCP, SSH and absent sockets fail before creation.
    context_env = ENV.to_h.keys.grep(/\A(?:DOCKER_|COMPOSE_|SQLCMD|MSSQL)/).to_h { |k| [k, nil] }
    endpoint, context_error, context_status = Open3.capture3(context_env, 'docker', 'context', 'inspect', '--format', '{{.Endpoints.docker.Host}}')
    raise "Docker context inspection failed: #{context_error}" unless context_status.success?
    @docker_endpoint = endpoint.strip
    raise 'only an existing local Unix Docker socket is allowed' unless @docker_endpoint.match?(/\Aunix:\/\/\/[^\r\n]+\z/) && File.socket?(@docker_endpoint.delete_prefix('unix://'))
    @result[:docker_connection] = { endpoint: @docker_endpoint, context_inspection_exit: context_status.exitstatus, transport: 'local_unix_only', inherited_overrides: 'removed' }
    info = good!(docker('docker-info', 'info', '--format', '{{json .}}'))
    host = JSON.parse(info[:stdout])
    raise 'Docker needs at least 8 GB RAM' unless host.fetch('MemTotal') >= 7_900_000_000
    @result[:host] = { ruby_arch: RbConfig::CONFIG['host_cpu'], docker_arch: host['Architecture'],
                       docker_os: host['OperatingSystem'], docker_version: host['ServerVersion'],
                       docker_memory_bytes: host['MemTotal'],
                       emulated: (host['Architecture'].to_s + ' ' + RbConfig::CONFIG['host_cpu']).match?(/arm|aarch/i),
                       native_performance: 'unmeasured', microsoft_emulation_support: 'unsupported' }
    # Listing succeeds independently of absence; permission/daemon errors cannot mean blank.
    names = good!(docker('existing-containers', 'container', 'ls', '-a', '--format', '{{.Names}}'))[:stdout].lines.map(&:strip)
    volumes = good!(docker('existing-volumes', 'volume', 'ls', '--format', '{{.Name}}'))[:stdout].lines.map(&:strip)
    networks = good!(docker('existing-networks', 'network', 'ls', '--format', '{{.Name}}'))[:stdout].lines.map(&:strip)
    raise 'blank lab required: owner must remove the disposable feasibility lab first' if names.include?(CONTAINER) || volumes.include?(VOLUME) || networks.include?(NETWORK)
    good!(compose('compose-config', 'config', '--quiet'))
    # Set before up: ensure can clean a partially created compose project after failure.
    @created = true
    good!(compose('compose-create', 'create', '--pull', 'missing', timeout: 240))
    good!(docker('container-start', 'start', CONTAINER, timeout: 60))
    obj = JSON.parse(good!(docker('container-inspect', 'inspect', CONTAINER))[:stdout]).fetch(0)
    @container_id = obj.fetch('Id')
    raise 'wrong image configuration' unless obj.fetch('Config').fetch('Image') == IMAGE
    image = JSON.parse(good!(docker('image-inspect', 'image', 'inspect', obj.fetch('Image')))[:stdout]).fetch(0)
    raise 'image digest missing' unless image.fetch('RepoDigests').any? { |d| d.end_with?(IMAGE.split('@').last) }
    raise 'container not amd64' unless image.fetch('Architecture') == 'amd64'
    raise 'unexpected published port' unless (obj.dig('HostConfig', 'PortBindings') || {}).empty?
    raise 'resource limits differ' unless obj.dig('HostConfig', 'Memory') == 4 * 1024**3 && obj.dig('HostConfig', 'NanoCpus') == 2_000_000_000
    @result[:image] = { requested: IMAGE, id: image['Id'], repo_digests: image['RepoDigests'],
                        container_id: @container_id, platform: image['Os'].to_s + '/' + image['Architecture'].to_s }
    ready_deadline = mono + LIMITS[:startup_seconds]
    loop do
      rec = sql_raw('startup-probe', envelope('SELECT 1 AS ready'), database: 'master', timeout: 10)
      break if rec[:exit_status] == 0 && !rec[:timed_out] && json_rows(rec)['ready'] == 1
      raise 'startup deadline exceeded' if mono >= ready_deadline
      sleep 2
    end
    @result[:server] = rows!('server-version', "SELECT @@VERSION AS version, CONVERT(nvarchar(128),SERVERPROPERTY('ProductVersion')) AS product_version, CONVERT(nvarchar(128),SERVERPROPERTY('Edition')) AS edition, CONVERT(int,SERVERPROPERTY('EngineEdition')) AS engine_edition", database: 'master')
  end

  def setup
    good!(sql('setup', File.read(File.join(__dir__, 'setup.sql')), database: 'master', timeout: 90))
    # Password exists only in memory/stdin and the disposable master database, never a file.
    master_password = SecureRandom.base64(48)
    tde = "CREATE MASTER KEY ENCRYPTION BY PASSWORD='#{master_password}';\n" \
          "CREATE CERTIFICATE CountLabTDE WITH SUBJECT='Disposable count lab';\n" \
          "USE CountLab; CREATE DATABASE ENCRYPTION KEY WITH ALGORITHM=AES_256 ENCRYPTION BY SERVER CERTIFICATE CountLabTDE;\n" \
          "ALTER DATABASE CountLab SET ENCRYPTION ON;"
    good!(sql('tde-create', tde, database: 'master', timeout: 60))
    deadline = mono + 180
    loop do
      @result[:tde] = rows!('tde-state', "SELECT encryption_state,percent_complete,key_algorithm,key_length FROM sys.dm_database_encryption_keys WHERE database_id=DB_ID()")
      break if @result[:tde]['encryption_state'] == 3
      raise 'TDE encryption_state=3 deadline exceeded' if mono >= deadline
      sleep 1
    end
    verify_versioning
    good!(sql('fixture-ddl', File.read(File.join(__dir__, 'fixtures.sql')), timeout: 60))
    [80, 100].each do |width|
      # Exactly 80/100 columns: id + (width-2) int columns + ciphertext.
      cols = (1..width - 2).map { |i| "c#{i} int NOT NULL" }.join(',')
      good!(sql("wide#{width}-ddl", "CREATE TABLE dbo.wide#{width}(id bigint NOT NULL PRIMARY KEY,#{cols},ciphertext varbinary(256) NOT NULL); CREATE INDEX ix_wide#{width}_c1 ON dbo.wide#{width}(c1);", timeout: 30))
    end
  end

  def verify_versioning
    state = rows!('versioning-preflight', "SELECT is_read_committed_snapshot_on,snapshot_isolation_state FROM sys.databases WHERE database_id=DB_ID()")
    raise 'CountLab row versioning not ready' unless state['is_read_committed_snapshot_on'] == true && state['snapshot_isolation_state'] == 1
    @result[:versioning] = state
  end

  def load_sql(table, total)
    expressions = case table
                  when 'empty_table', 'thin_100m' then '@base+n+1'
                  when 'heap_table' then "@base+n+1,'heap'"
                  when 'clustered_table' then "@base+n+1,(@base+n)%97,REPLICATE('x',64)"
                  when 'partitioned_table', 'columnstore_table' then '@base+n+1,(@base+n)%97'
                  when 'skew_gaps' then "CASE WHEN @base+n<90000 THEN @base+n+1 ELSE 10000000000+@base+n END,CASE WHEN (@base+n)%10=0 THEN REPLICATE('z',1800) ELSE 'x' END"
                  when 'service_table' then "@base+n+1,0,REPLICATE('s',64)"
                  when /\Awide(80|100)\z/
                    width = Regexp.last_match(1).to_i
                    ['@base+n+1', *(1..width - 2).map { |i| "CONVERT(int,(@base+n+#{i})%10007)" }, '@cipher'].join(',')
                  else raise 'unknown fixture'
                  end
    # A single generated encrypted value is copied as opaque ciphertext. This is not
    # Always Encrypted, real application encryption, or per-row encryption throughput.
    key = SecureRandom.hex(24)
    <<~SQL
      DECLARE @base bigint=0, @take int, @inserted bigint=0;
      DECLARE @cipher varbinary(256)=EncryptByPassPhrase('#{key}',REPLICATE('opaque',16));
      WHILE @base<#{total} BEGIN
        SET @take=CONVERT(int,CASE WHEN #{total}-@base<50000 THEN #{total}-@base ELSE 50000 END);
        INSERT dbo.#{table} SELECT #{expressions} FROM dbo.Numbers WHERE n<@take;
        IF @@ROWCOUNT<>@take THROW 51000,'generator cardinality mismatch',1;
        SET @inserted+=@take; SET @base+=@take;
        CHECKPOINT;
      END;
      #{envelope('SELECT @inserted AS inserted')}
    SQL
  end

  def footprint
    rec = good!(docker('volume-size', 'exec', CONTAINER, 'du', '-sb', '/var/opt/mssql', timeout: 20))
    bytes = Integer(rec[:stdout].split.fetch(0), 10)
    @result[:volume_bytes] = bytes
    (@result[:footprints] ||= []) << { bytes: bytes, monotonic: mono, command_log: rec[:stdout_log] }
    raise '14 GB lab stop bound reached' if bytes >= LIMITS[:volume_stop_bytes]
    bytes
  end

  def load
    FIXTURES.each do |table, target|
      optional = table == 'thin_100m'
      f = { planned_rows: target, actual_rows: nil, status: 'notrun',
            scale: 'fixed target; no silent reduction', batch_rows: 50_000,
            optional: optional, deadline_seconds: optional ? LIMITS[:optional_thin_seconds] : LIMITS[:load_seconds] }
      @result[:fixtures][table] = f
      if optional && @deadline - mono < LIMITS[:optional_thin_seconds] + 500
        f[:reason] = 'remaining total budget reserved for measurement/cleanup'
        good!(sql('drop-notrun-thin', 'DROP TABLE dbo.thin_100m;'))
        next
      end
      footprint
      rec = sql("load-#{table}", load_sql(table, target), timeout: f[:deadline_seconds])
      f[:load_command] = rec[:stdout_log]
      if rec[:exit_status] != 0 || rec[:timed_out]
        f[:status] = 'failed'
        f[:reason] = 'load failed or deadline; partial rows have no accepted oracle'
        if optional
          good!(sql('drop-partial-thin', 'DROP TABLE dbo.thin_100m;', timeout: 60))
          f[:status] = 'notrun'
          next
        end
        good!(rec)
      end
      inserted = json_rows(rec).fetch('inserted')
      raise 'load envelope cardinality mismatch' unless inserted == target
      f[:actual_rows] = inserted
      f[:status] = 'loaded'
      # Preparation only; do not carry this operation to production collection.
      good!(sql("prepare-stats-#{table}", "CREATE STATISTICS lab_id ON dbo.#{table}(id) WITH SAMPLE 20 PERCENT;", timeout: 60))
      f[:initial_oracle] = exact(table, 'rcsi', 'initial-oracle')
      raise "fixture known truth mismatch: #{table}" unless f[:initial_oracle]['rows'] == inserted
      f[:status] = 'verified'
      save
    end
    footprint
  end

  def measured(label, body, snapshot: false)
    verify_versioning
    isolation = snapshot ? 'SET TRANSACTION ISOLATION LEVEL SNAPSHOT; BEGIN TRANSACTION;' : 'SET TRANSACTION ISOLATION LEVEL READ COMMITTED;'
    query = "#{isolation}\nSET STATISTICS IO ON; SET STATISTICS TIME ON;\n#{body}\nSET STATISTICS TIME OFF; SET STATISTICS IO OFF;\n#{snapshot ? 'COMMIT;' : ''}"
    rec = sql(label, query, timeout: LIMITS[:query_seconds])
    combined = rec[:stdout] + "\n" + rec[:stderr]
    execution = combined.scan(/SQL Server Execution Times:\s*CPU time = (\d+) ms,\s*elapsed time = (\d+) ms/m).map { |cpu, ms| { cpu_ms: cpu.to_i, elapsed_ms: ms.to_i } }
    io = combined.lines.grep(/logical reads \d+/).map(&:strip)
    numeric_io = io.map do |line|
      { table: line[/\ATable '([^']+)'/, 1],
        counters: line.scan(/(\b(?:lob )?(?:scan count|logical reads|physical reads|page server reads|read-ahead reads)) (\d+)/).to_h.transform_values(&:to_i),
        raw: line }
    end
    observation = { label: label, command_log: rec[:stdout_log],
                    client_seconds_including_docker_cli: rec[:seconds],
                    server_execution_times: execution, server_io_messages: io, server_io: numeric_io,
                    server_metrics_scope: 'STATISTICS messages around instrumented SELECT, including JSON formatting; parse/compile preserved in raw logs; SET messages may also appear',
                    server_metrics_status: execution.empty? ? 'unavailable' : 'captured',
                    cache: 'uncontrolled; prior load/oracle/count carryover; no flush',
                    status: rec[:exit_status] == 0 && !rec[:timed_out] ? 'completed' : 'failed',
                    exit_status: rec[:exit_status], timed_out: rec[:timed_out],
                    termination: rec[:termination], value: nil }
    @mutex.synchronize { @result[:observations] << observation }
    good!(rec)
    observation[:value] = json_rows(rec)
    raise 'server CPU/duration capture missing' if execution.empty?
    observation[:value]
  end

  def exact(table, isolation, purpose)
    measured("#{purpose}-#{table}-#{isolation}", envelope("SELECT COUNT_BIG(*) AS rows FROM dbo.#{table}"), snapshot: isolation == 'snapshot')
  end

  def metadata(table, purpose)
    dmv = measured("#{purpose}-#{table}-partition-dmv", envelope("SELECT SUM(row_count) AS rows, COUNT_BIG(*) AS partitions FROM sys.dm_db_partition_stats WHERE object_id=OBJECT_ID(N'dbo.#{table}') AND index_id IN(0,1)"))
    cat = measured("#{purpose}-#{table}-partitions", envelope("SELECT SUM(rows) AS rows, COUNT_BIG(*) AS partitions FROM sys.partitions WHERE object_id=OBJECT_ID(N'dbo.#{table}') AND index_id IN(0,1)"))
    stats = measured("#{purpose}-#{table}-optimizer-stats", envelope("SELECT s.name,s.stats_id,s.has_filter,s.filter_definition,p.last_updated,p.rows,p.rows_sampled,p.unfiltered_rows,p.modification_counter FROM sys.stats s OUTER APPLY sys.dm_db_stats_properties(s.object_id,s.stats_id)p WHERE s.object_id=OBJECT_ID(N'dbo.#{table}') ORDER BY s.stats_id", array: true))
    [dmv, cat].each { |v| raise 'missing partition metadata' unless v['rows'].is_a?(Integer) && v['partitions'].is_a?(Integer) && v['partitions'].positive? }
    raise 'optimizer stats missing' unless stats.is_a?(Array) && stats.any? { |s| s['name'] == 'lab_id' }
    { dmv: dmv, partitions: cat, optimizer: stats }
  end

  def error_row(table, phase, repeat, method, value, truth)
    raise 'noninteger/unknown measurement' unless value.is_a?(Integer) && truth.is_a?(Integer)
    signed = truth.zero? ? nil : 100.0 * (value - truth) / truth
    { table: table, phase: phase, repeat: repeat, method: method, truth: truth, value: value,
      signed_error_pct: signed, absolute_error_pct: signed&.abs,
      empty_match: truth.zero? ? value.zero? : nil,
      conditions: { tde: @result[:tde], row_versioning: @result[:versioning],
                    stats_auto_update: false, stats_sample_percent_requested: 20,
                    host_emulated: @result.dig(:host, :emulated), cache: 'uncontrolled',
                    shape_reference: "inventory.objects/sampled_physical_shape for #{table}; fixture target in fixtures.#{table}" } }
  end

  def measure
    @result[:errors] = []
    rng = Random.new(SEED)
    @result[:order] = []
    @result[:fixtures].select { |_, f| f[:status] == 'verified' }.keys.shuffle(random: rng).each do |table|
      f = @result[:fixtures].fetch(table)
      # Small variation is deterministic committed INSERT in a quiet fixture,
      # after initial statistics. DMV vs stale optimizer rows remain separate.
      3.times do |repeat|
        phase = repeat.zero? ? 'prepared' : "quiet_delta_#{repeat}"
        if repeat.positive? && %w[heap_table clustered_table].include?(table)
          count = repeat * 7
          new_id = 1_000_000_000 + repeat * 100
          values = table == 'heap_table' ? "#{new_id}+n,'variation'" : "#{new_id}+n,1,'variation'"
          good!(sql("variation-#{table}-#{repeat}", "INSERT dbo.#{table} SELECT #{values} FROM dbo.Numbers WHERE n<#{count}; IF @@ROWCOUNT<>#{count} THROW 51000,'delta mismatch',1;"))
          f[:actual_rows] += count
        end
        methods = %w[rcsi snapshot metadata].shuffle(random: rng)
        methods.each do |method|
          @result[:order] << { table: table, repeat: repeat, method: method, phase: phase }
          if method == 'metadata'
            meta = metadata(table, "#{phase}-r#{repeat}")
            %i[dmv partitions].each { |m| @result[:errors] << error_row(table, phase, repeat, m.to_s, meta.fetch(m).fetch('rows'), f[:actual_rows]) }
            meta[:optimizer].each do |s|
              if s['last_updated'] && s['rows'].is_a?(Integer) && !s['has_filter']
                @result[:errors] << error_row(table, phase, repeat, "optimizer:#{s['name']}", s['rows'], f[:actual_rows]).merge(stats: s)
              end
            end
          else
            value = exact(table, method, "#{phase}-r#{repeat}").fetch('rows')
            @result[:errors] << error_row(table, phase, repeat, method, value, f[:actual_rows])
            raise "exact count mismatch #{table}" unless value == f[:actual_rows]
          end
        end
      end
      f[:final_oracle] = exact(table, 'rcsi', 'final-oracle')
      raise 'final oracle mismatch' unless f[:final_oracle]['rows'] == f[:actual_rows]
      save
    end
    @result[:extrema] = @result[:errors].group_by { |e| e[:method] }.transform_values do |rows|
      nonempty = rows.reject { |r| r[:signed_error_pct].nil? }
      { observations: rows.length, signed_min: nonempty.min_by { |r| r[:signed_error_pct] },
        signed_max: nonempty.max_by { |r| r[:signed_error_pct] },
        absolute_min: nonempty.min_by { |r| r[:absolute_error_pct] },
        absolute_max: nonempty.max_by { |r| r[:absolute_error_pct] },
        scope: 'observed fixtures only; no universal 10% bound; ties retained in errors' }
    end
  end

  def inventory
    @result[:inventory] = rows!('inventory', <<~SQL.strip)
      SELECT JSON_QUERY((SELECT t.name,c.columns_count,i.index_id,i.name AS index_name,i.type_desc,
        p.partition_number,p.row_count,p.used_page_count,p.reserved_page_count
        FROM sys.tables t CROSS APPLY(SELECT COUNT(*) AS columns_count FROM sys.columns c WHERE c.object_id=t.object_id)c
        JOIN sys.indexes i ON i.object_id=t.object_id
        JOIN sys.dm_db_partition_stats p ON p.object_id=i.object_id AND p.index_id=i.index_id
        ORDER BY t.name,i.index_id,p.partition_number FOR JSON PATH)) AS objects,
        JSON_QUERY((SELECT physical_memory_in_use_kb,locked_page_allocations_kb,process_physical_memory_low FROM sys.dm_os_process_memory FOR JSON PATH)) AS process_memory,
        JSON_QUERY((SELECT name,value_in_use FROM sys.configurations WHERE name IN('max server memory (MB)','max degree of parallelism') FOR JSON PATH)) AS configuration,
        JSON_QUERY((SELECT OBJECT_NAME(object_id) AS table_name,state_desc,total_rows,deleted_rows,size_in_bytes FROM sys.dm_db_column_store_row_group_physical_stats FOR JSON PATH)) AS columnstore_rowgroups,
        JSON_QUERY((SELECT OBJECT_NAME(object_id) AS table_name,index_id,avg_record_size_in_bytes,page_count FROM sys.dm_db_index_physical_stats(DB_ID(),NULL,NULL,NULL,'SAMPLED') WHERE index_level=0 FOR JSON PATH)) AS sampled_physical_shape,
        JSON_QUERY((SELECT file_id,name,size,max_size,type_desc FROM sys.database_files FOR JSON PATH)) AS database_files
    SQL
    @result[:inventory_notes] = 'Isolated lab diagnostic; physical-stats scan adds I/O; sampled row size excludes full LOB accounting; cache order not controlled. Columnstore inserts exercised; explicit delete/delta transition matrix not exercised.'
  end

  def permissions
    good!(sql('permission-user-create', 'CREATE USER count_reader WITHOUT LOGIN;'))
    @result[:permission_probes] = []
    %w[invisible select_only dmv_granted].each do |stage|
      grant = case stage
              when 'select_only' then 'GRANT SELECT ON dbo.clustered_table TO count_reader;'
              when 'dmv_granted' then 'GRANT VIEW DATABASE PERFORMANCE STATE TO count_reader; GRANT VIEW SECURITY DEFINITION TO count_reader;'
              else ''
              end
      query = <<~SQL
        #{grant}
        EXECUTE AS USER=N'count_reader';
        DECLARE @dmv bigint=NULL, @error int=NULL;
        BEGIN TRY
          EXEC sys.sp_executesql N'SELECT @rows=SUM(row_count) FROM sys.dm_db_partition_stats
            WHERE object_id=OBJECT_ID(N''dbo.clustered_table'') AND index_id IN(0,1);',N'@rows bigint OUTPUT',@rows=@dmv OUTPUT;
        END TRY BEGIN CATCH SET @error=ERROR_NUMBER(); END CATCH;
        #{envelope("SELECT N'#{stage}' AS stage,USER_NAME() AS execution_user,OBJECT_ID(N'dbo.clustered_table') AS visible_object_id,@dmv AS dmv_rows,@error AS dmv_error_number,(SELECT SUM(rows) FROM sys.partitions WHERE object_id=OBJECT_ID(N'dbo.clustered_table') AND index_id IN(0,1)) AS catalog_rows,JSON_QUERY((SELECT s.name,p.rows,p.last_updated FROM sys.stats s OUTER APPLY sys.dm_db_stats_properties(s.object_id,s.stats_id)p WHERE s.object_id=OBJECT_ID(N'dbo.clustered_table') FOR JSON PATH,INCLUDE_NULL_VALUES)) AS optimizer_stats")}
        REVERT;
      SQL
      probe = json_rows(good!(sql("permissions-#{stage}", query)))
      raise 'permission execution context mismatch' unless probe['execution_user'] == 'count_reader'
      if stage == 'invisible'
        raise 'metadata visibility precondition failed' unless probe['visible_object_id'].nil? && probe['catalog_rows'].nil?
      elsif stage == 'select_only'
        raise 'expected DMV permission denial absent' unless probe['dmv_rows'].nil? && probe['dmv_error_number'].is_a?(Integer) && probe['catalog_rows'] == 200_000
      else
        raise 'expected DMV grants insufficient' unless probe['dmv_error_number'].nil? && probe['dmv_rows'] == 200_000
      end
      @result[:permission_probes] << probe
    end
    good!(sql('permission-user-drop', 'DROP USER count_reader;'))
  end

  # Honest narrow paired diagnostic: one foreground-owned worker, fixed scheduled
  # arrivals; if it falls behind, all missed arrivals remain in raw evidence.
  # CLI startup dominates some requests. Never certify p99/SLO from this window.
  def service
    result = { status: 'running', slo: 'unavailable', reason: 'small CLI diagnostic, not production SLO evidence',
               workload: 'single client, natural committed UPDATE of existing PK row; row delta zero',
               planned_requests_per_arm: 12, arrival_interval_seconds: 0.5,
               arms: [], recovery: 'diagnostic only; production headroom unmeasured' }
    @result[:service] = result
    %w[baseline exact metadata].each do |arm|
      before = exact('service_table', 'rcsi', "service-#{arm}-before")['rows']
      arrivals = []
      start = mono
      worker = Thread.new do
        12.times do |i|
          due = start + i * 0.5
          sleep([due - mono, 0].max)
          arrival = { request: i, scheduled_monotonic: due, started_monotonic: mono,
                      outcome: 'unfinished', committed_row_delta: nil, latency_seconds: nil }
          arrivals << arrival
          begin
            # No INSERT/DELETE: oracle for all concurrent count snapshots stays fixed.
            rec = sql("service-#{arm}-update-#{i}", "UPDATE dbo.service_table SET v=v+1 WHERE id=#{i + 1}; IF @@ROWCOUNT<>1 THROW 51000,'workload target missing',1; #{envelope('SELECT 1 AS committed')}", timeout: 5)
            good!(rec)
            raise 'missing workload commit envelope' unless json_rows(rec)['committed'] == 1
            arrival[:outcome] = 'committed'
            arrival[:committed_row_delta] = 0
            arrival[:command_log] = rec[:stdout_log]
          rescue StandardError => e
            arrival[:outcome] = 'failed_or_commit_unknown'
            arrival[:error] = e.message
          ensure
            arrival[:finished_monotonic] = mono
            arrival[:latency_seconds] = mono - due
          end
        end
      end
      probe = nil
      probe_start = nil
      probe_end = nil
      begin
        sleep 1
        probe_start = mono
        probe = case arm
                when 'exact' then exact('service_table', 'rcsi', 'service-probe')
                when 'metadata' then measured('service-probe-dmv', envelope("SELECT SUM(row_count) AS rows FROM sys.dm_db_partition_stats WHERE object_id=OBJECT_ID(N'dbo.service_table') AND index_id IN(0,1)"))
                else { 'rows' => nil, 'status' => 'baseline_no_probe' }
                end
        probe_end = mono
      ensure
        worker.join # No workflow return while any owned worker/CLI is running.
        result[:arms] << { arm: arm, monotonic_start: start, monotonic_end: mono,
                           arrivals: arrivals, probe: probe, planned: 12,
                           probe_started_monotonic: probe_start, probe_finished_monotonic: probe_end,
                           unfinished: arrivals.count { |r| r[:outcome] == 'unfinished' },
                           failed: arrivals.count { |r| r[:outcome] != 'committed' },
                           missing_arrivals: 12 - arrivals.length }
      end
      after = exact('service_table', 'rcsi', "service-#{arm}-after")['rows']
      result[:arms].last.merge!(initial_oracle: before, final_oracle: after, committed_row_delta: 0)
      raise 'live-write row oracle mismatch' unless before == 100_000 && after == before && (arm == 'baseline' || probe['rows'] == before)
      raise 'service method error; raw failures retained' unless arrivals.length == 12 && arrivals.all? { |r| r[:outcome] == 'committed' }
      sleep 1 # Lab recovery gap only; no production recovery/SLO inference.
      save
    end
    result[:status] = 'diagnostic_complete'
    result[:order] = 'baseline, exact, metadata; cache and temporal drift not separated'
  end

  def cleanup
    @cleanup_deadline = mono + LIMITS[:cleanup_seconds]
    unless @created
      @result[:cleanup] = { status: 'not_needed', reason: 'no resources created; existing resources untouched' }
      return
    end
    # Inspect only fixed names, remove only this compose project's labelled resources.
    containers = good!(docker('cleanup-list-containers', 'container', 'ls', '-a', '--format', '{{.Names}}', global_budget: false))[:stdout].lines.map(&:strip)
    if containers.include?(CONTAINER)
      obj = JSON.parse(good!(docker('cleanup-inspect', 'inspect', CONTAINER, global_budget: false))[:stdout]).fetch(0)
      labels = obj.fetch('Config').fetch('Labels')
      raise 'cleanup ownership mismatch' unless labels['com.docker.compose.project'] == PROJECT && labels['org.count-lab.scope'] == 'sqlserver-count-only'
      raise 'cleanup container ID changed' if @container_id && obj['Id'] != @container_id
      # Container stop/removal is an independent final server-termination boundary,
      # including if SQL cancellation was unavailable. Keep that failure in status.
      good!(docker('cleanup-stop', 'stop', '--time', '30', CONTAINER, timeout: 60, global_budget: false))
      good!(docker('cleanup-remove', 'rm', CONTAINER, global_budget: false))
    end
    %w[volume network].each do |kind|
      name = kind == 'volume' ? VOLUME : NETWORK
      names = good!(docker("cleanup-#{kind}-list", kind, 'ls', '--format', '{{.Name}}', global_budget: false))[:stdout].lines.map(&:strip)
      next unless names.include?(name)
      obj = JSON.parse(good!(docker("cleanup-#{kind}-inspect", kind, 'inspect', name, global_budget: false))[:stdout]).fetch(0)
      raise "cleanup #{kind} ownership mismatch" unless obj.fetch('Labels')['com.docker.compose.project'] == PROJECT
      good!(docker("cleanup-#{kind}-remove", kind, 'rm', name, global_budget: false))
    end
    remaining = {
      container: good!(docker('final-container-list', 'container', 'ls', '-a', '--format', '{{.Names}}', global_budget: false))[:stdout].lines.map(&:strip).include?(CONTAINER),
      volume: good!(docker('final-volume-list', 'volume', 'ls', '--format', '{{.Name}}', global_budget: false))[:stdout].lines.map(&:strip).include?(VOLUME),
      network: good!(docker('final-network-list', 'network', 'ls', '--format', '{{.Name}}', global_budget: false))[:stdout].lines.map(&:strip).include?(NETWORK)
    }
    raise 'cleanup resources remain' if remaining.values.any?
    @result[:cleanup] = { status: 'verified', remaining: remaining, server_boundary: 'owned container stopped and removed; volume removed', sql_termination_failure_seen: @unsafe }
  end

  def run
    code = 1
    begin
      save
      @lock = File.open(File.join(ROOT, 'results/sqlserver/.lab.lock'), 'w')
      raise 'another local canonical runner owns this lab' unless @lock.flock(File::LOCK_EX | File::LOCK_NB)
      preflight
      setup
      load
      inventory
      permissions
      measure
      service
      @result[:final_owned_sessions] = rows!('final-owned-session-audit', "SELECT COUNT_BIG(*) AS remaining FROM sys.dm_exec_sessions WHERE LEFT(host_name,#{@session_prefix.length})=N'#{@session_prefix}' AND session_id<>@@SPID", database: 'master')
      raise 'owned server sessions remain before cleanup' unless @result[:final_owned_sessions]['remaining'] == 0
      @result[:final_tde] = rows!('final-tde', 'SELECT encryption_state FROM sys.dm_database_encryption_keys WHERE database_id=DB_ID()')
      raise 'final encryption not complete' unless @result[:final_tde]['encryption_state'] == 3
      footprint
      @result[:completion] = 'essential_experiments_completed'
      @result[:precision] = 'exact_oracles_match; metadata_errors_observed_only; no_bound_certified'
      @result[:exact_method_budget] = 'completed_within_lab_query_deadlines'
      code = 0
    rescue StandardError, Interrupt => e
      @result[:failure] = { class: e.class.name, message: e.message, backtrace: e.backtrace&.first(12) }
      note("failed: #{e.class}: #{e.message}")
    ensure
      begin
        cleanup
      rescue StandardError, Interrupt => e
        code = 1
        @result[:cleanup] = { status: 'failed', error: e.message }
        note("cleanup failed: #{e.message}")
      end
      @result[:source_after] = sources
      @result[:source_unchanged] = @result[:source_before] == @result[:source_after]
      code = 1 unless @result[:source_unchanged]
      code = 1 if @unsafe
      @result[:artifact_integrity] = @result[:commands].flat_map do |c|
        %i[stdout_log stderr_log].map do |key|
          relative = c.fetch(key)
          path = File.join(ROOT, relative)
          { path: relative, bytes: File.size(path), sha256: Digest::SHA256.file(path).hexdigest,
            complete: c[:logs_complete], command_exit_status: c[:exit_status], command_signal: c[:signal] }
        end
      end
      @result[:status] = code.zero? ? 'completed_pending_owner_review' : 'failed'
      @result[:finished_at] = Time.now.utc.iso8601
      @result[:monotonic_seconds] = mono - @start
      @result[:exit_status] = code
      @result[:complete_log_path] = File.join(@dir, 'runner.log').delete_prefix(ROOT + '/')
      note("exit_status=#{code}; cleanup=#{@result[:cleanup][:status]}; evidence=#{@dir}")
      @result[:runner_log_sha256] = Digest::SHA256.file(File.join(@dir, 'runner.log')).hexdigest
      save
      @log.close
      @lock&.close
    end
    code
  end
end

if $PROGRAM_NAME == __FILE__
  abort 'Usage: mise exec -- ruby sqlserver/lab.rb all (blank dedicated lab only)' unless ARGV == ['all']
  Signal.trap('TERM') { raise Interrupt, 'SIGTERM received; run owned cleanup' }
  exit CountLab.new.run
end
