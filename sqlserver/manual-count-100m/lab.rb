# frozen_string_literal: true
# Ruby 4.0.9 stdlib; every process is waited/reaped in this foreground lifecycle.
require 'json'
require 'digest'
require 'fileutils'
require 'securerandom'
require 'time'
require 'uri'
require 'shellwords'
require_relative 'model'

class ManualLab
  ROOT = File.expand_path('../..', __dir__)
  PREFIX = 'lab-sqlserver-manual100m'
  NAMES = { 'container' => [PREFIX, "#{PREFIX}-secondary", "#{PREFIX}-client"], 'network' => ["#{PREFIX}-network"] }.freeze
  SQL_IMAGE = 'mcr.microsoft.com/mssql/server:2022-latest@sha256:cf8f0ab2ebe61feec2eed2629061a07d247f791c2a8ddda74a4ac9f06a291718'
  SDK_IMAGE = 'mcr.microsoft.com/dotnet/sdk:10.0@sha256:212fe4cb74627d5adf9817a4088906ae3edff1bd853bb27565d4b46731efe61e'
  SOURCE = (%w[lab.rb model.rb verify_offline.rb mise.toml compose.yaml Dockerfile .dockerignore ManualCount100m.csproj
               NuGet.Config packages.lock.json Program.cs Fixture.cs State.cs README.md input-bindings.json manual/sql-server.md manual/source.json] +
            %w[inventory guard role showplan indexes pages branch-a branch-b branch-c branch-d ag-primary-state ag-endpoint ag-peer ag-primary-setup ag-secondary-setup ag-backup-proof ag-topology].map { |n| "sql/#{n}.sql" }).freeze
  def now = Process.clock_gettime(Process::CLOCK_MONOTONIC)
  def hashes
    SOURCE.to_h { |f| ["sqlserver/manual-count-100m/#{f}", Digest::SHA256.file(File.join(__dir__, f)).hexdigest] }.merge(
      'results/reviews/sqlserver-manual-count-100m/baseline.json' => Digest::SHA256.file(File.join(ROOT, 'results/reviews/sqlserver-manual-count-100m/baseline.json')).hexdigest)
  end
  def persist
    path = File.join(@run, 'result.json')
    File.open(path + '.tmp', 'w', 0o600) { |f| f.write(JSON.pretty_generate(@result) + "\n"); f.flush; f.fsync }
    File.rename(path + '.tmp', path)
  end
  def execute(args, seconds: 30, check: true, cleanup: false, watch: false)
    deadline = [now + seconds, cleanup ? @cleanup_deadline : @deadline].min
    raise 'lifecycle deadline exhausted' if now >= deadline
    @sequence += 1
    prefix = File.join(@run, format('%05d', @sequence))
    rec = { 'argv' => args, 'stdout' => prefix + '.stdout.log', 'stderr' => prefix + '.stderr.log',
            'started_at' => Time.now.utc.iso8601, 'start_monotonic' => now, 'exit_status' => nil }
    @result['commands'] << rec
    status = nil
    pid = nil
    File.open(rec['stdout'], 'w') do |out|
      File.open(rec['stderr'], 'w') do |err|
        begin
          pid = Process.spawn(@env, *args, chdir: __dir__, in: File::NULL, out: out, err: err,
                              unsetenv_others: true, pgroup: true)
          next_capacity = now
          reported_bytes = 0
          loop do
            pair = Process.waitpid2(pid, Process::WNOHANG)
            if pair
              status = pair[1]
              break
            end
            if now >= deadline
              rec['timed_out'] = true
              raise "command deadline: #{args.first}"
            end
            if watch && now >= next_capacity
              free = host_free
              raise 'host free space below 12 GiB; abort' if free < 12 * ManualModel::GIB
              @result['last_free_bytes'] = free
              import_probe
              stdout = File.read(rec['stdout']) # driver emits only sparse progress, never row data
              if stdout.bytesize > reported_bytes
                $stdout.write(stdout.byteslice(reported_bytes..)); $stdout.flush
                reported_bytes = stdout.bytesize
              end
              %w[narrow composite].each do |condition|
                gate = File.join(@run, 'index-gate-' + condition)
                next unless stdout.include?('INDEX_CAPACITY_GATE ' + condition) && !File.exist?(gate)
                raise 'before-index host free space below 24 GiB' if free < 24 * ManualModel::GIB
                File.open(gate, 'w', 0o600) { |f| f.puts(JSON.generate({ owner: @owner, free_bytes: free, at: Time.now.utc.iso8601 })); f.flush; f.fsync }
              end
              if stdout.include?('AG_SECONDARY_GATE') && !@secondary_started
                start_secondary
              end
              @result['stage_file_sizes'] = Dir.glob(File.join(@stage, '**', '*')).select { |p| File.file?(p) && !File.symlink?(p) }
                .sort.to_h { |p| [p.delete_prefix(@stage + '/'), File.size(p)] }
              persist
              next_capacity = now + 3
            end
            sleep 0.1
          end
        ensure
          if pid && !status
            rec['terminated_by_controller'] = true
            Process.kill('TERM', -pid) rescue Errno::ESRCH
            grace = [now + 2, deadline + 2].min
            while now < grace
              pair = Process.waitpid2(pid, Process::WNOHANG)
              if pair
                status = pair[1]
                break
              end
              sleep 0.05
            end
            unless status
              rec['forced_process_kill'] = true
              Process.kill('KILL', -pid) rescue Errno::ESRCH
              status = Process.waitpid2(pid)[1]
            end
          end
          rec['exit_status'] = status&.exitstatus || (128 + status.termsig if status&.signaled?)
          rec['end_monotonic'] = now
          out.flush; err.flush
          rec['stdout_sha256'] = Digest::SHA256.file(rec['stdout']).hexdigest
          rec['stderr_sha256'] = Digest::SHA256.file(rec['stderr']).hexdigest
          persist
        end
      end
    end
    raise "command failed: #{args.first} exit=#{rec['exit_status']}" if check && rec['exit_status'] != 0
    [File.read(rec['stdout']), rec]
  end
  def docker(*args, **opts) = execute(@cli + args, **opts)
  def inspect(kind, id, cleanup: false)
    out, = docker(kind, 'inspect', id, seconds: 8, cleanup: cleanup)
    JSON.parse(out).fetch(0) # inspect error is never evidence of absence
  end
  def inventory(kind, cleanup: false)
    flags = kind == 'container' ? ['ls', '-a'] : ['ls']
    out, = docker(kind, *flags, '--filter', "label=#{ManualModel::LABEL}=#{@owner}", '--format', '{{json .}}', cleanup: cleanup, seconds: 8)
    out.lines.reject { |l| l.strip.empty? }.map { |l| JSON.parse(l) }
  end
  def host_free
    out, = execute(['df', '-Pk', @capacity_path], seconds: 5)
    fields = out.lines.last.split
    Integer(fields.fetch(3)) * 1024
  end
  def import_probe
    path = File.join(@run, 'probe.json')
    @result['probe'] = JSON.parse(File.read(path)) if File.file?(path)
  end
  def setup_context
    config = File.join(Dir.home, '.docker', 'config.json')
    current = File.file?(config) ? JSON.parse(File.read(config)).fetch('currentContext', 'default') : 'default'
    # Only this fixed HOME config; Docker/Compose/TLS endpoint environment is absent in @env.
    out, = execute(['docker', 'context', 'inspect', current], seconds: 10)
    endpoint = JSON.parse(out).fetch(0).fetch('Endpoints').fetch('docker').fetch('Host')
    path = ManualModel.local_endpoint!(endpoint)
    raise 'current local Unix socket unavailable' unless File.socket?(path)
    @cli = ['docker', '--host', endpoint]
    @result['context'] = { 'name' => current, 'endpoint' => endpoint }
    # Prevent using a different local VM while inspecting the prescribed Rosetta VM.
    raise 'current context must be colima-brotli-verify' unless current == 'colima-brotli-verify'
    docker('info', '--format', '{{json .}}', seconds: 10)
    execute(['uname', '-a'])
    execute(['sysctl', '-n', 'hw.memsize', 'hw.ncpu', 'machdep.cpu.brand_string'])
    @colima_home = File.dirname(File.dirname(path))
    raise 'VM home profile endpoint mismatch' unless File.basename(File.dirname(path)) == 'brotli-verify' && File.basename(@colima_home) == 'colima'
    @env['COLIMA_HOME'] = @colima_home
    config_path = File.join(@colima_home, 'brotli-verify', 'colima.yaml')
    raise 'VM configuration unavailable' unless File.file?(config_path)
    FileUtils.cp(config_path, File.join(@run, 'colima-config.yaml'))
    vm('uname -a; df -Pk /; cat /proc/meminfo; cat /proc/cpuinfo', 'guest-resources')
    registration = vm('cat /proc/sys/fs/binfmt_misc/rosetta', 'rosetta-registration')
    raise 'Rosetta registration disabled/missing' unless registration.lines.map(&:strip).include?('enabled') &&
      registration.include?('interpreter /mnt/lima-rosetta/rosetta')
    @result['rosetta_registration'] = registration
  end
  def vm(script, name, seconds: 10, cleanup: false)
    out, rec = execute(['colima', 'ssh', '--profile', 'brotli-verify', '--', 'sh', '-c', script], seconds: seconds, cleanup: cleanup)
    (@result['vm_evidence'] ||= {})[name] = rec
    out
  end
  def exclusive_preflight
    NAMES.each do |kind, names|
      names.each do |name|
        flags = kind == 'container' ? ['ls', '-a'] : ['ls']
        fmt = kind == 'container' ? '{{.Names}}' : '{{.Name}}'
        out, = docker(kind, *flags, '--filter', "name=^#{name}$", '--format', fmt)
        raise "reserved resource already exists: #{name}" unless out.lines.map(&:strip).reject(&:empty?).empty?
      end
    end
    out, = docker('image', 'ls', '--filter', "reference=#{@client_image}", '--format', '{{.ID}}')
    raise 'owned build image tag already exists' unless out.strip.empty?
    @preflight = true
  end
  def setup_stage
    # Validate every parent without following a symlink; marker lives OUTSIDE SQL server data.
    cursor = ROOT
    %w[.data sqlserver-manual-count-100m].each do |part|
      cursor = File.join(cursor, part)
      raise 'symlink in stage parent' if File.symlink?(cursor)
      Dir.mkdir(cursor, 0o700) unless File.exist?(cursor)
      raise 'stage parent realpath mismatch' unless File.realpath(cursor) == cursor
    end
    @base = cursor
    directory = File.join(@base, @owner)
    raise 'owner directory already exists' if File.exist?(directory) || File.symlink?(directory)
    Dir.mkdir(directory, 0o700)
    @stage = File.join(directory, 'stage')
    Dir.mkdir(@stage, 0o700)
    File.write(File.join(directory, 'owner.json'), JSON.generate({ owner: @owner, stage: @stage, uid: Process.uid }), mode: 'w', perm: 0o600)
    ManualModel.stage_owned!(@base, @owner, @stage)
    @stage_created = true
    # Arbitrary image user: actual host UID with primary group 0; no chmod/chown outside our stage.
    %w[primary secondary shared].each { |n| Dir.mkdir(File.join(@stage, n), 0o700) }
    %w[primary secondary].each do |node|
      %w[data log secrets].each { |n| Dir.mkdir(File.join(@stage, node, n), 0o700) }
    end
    ManualModel.topology_stage!(@base, @owner, @stage)
    @env.merge!('MANUAL_STAGE' => @stage, 'MANUAL_UID' => Process.uid.to_s, 'MANUAL_EVIDENCE' => @run)
    @result['stage'] = { 'path' => @stage, 'uid' => Process.uid, 'gid' => 0, 'marker' => File.join(directory, 'owner.json') }
  end
  def capture_ids
    NAMES.each do |kind, names|
      names.each do |name|
        obj = inspect(kind, name)
        ManualModel.owned!(kind, obj, @owner)
        @ids[[kind, name]] = obj.fetch('Id')
      end
    end
    servers = [PREFIX, "#{PREFIX}-secondary"].map do |name|
      server = inspect('container', @ids.fetch(['container', name]))
      node = name == PREFIX ? 'primary' : 'secondary'
      ManualModel.server_mounts!(server, @stage, node, Process.uid)
      (@result['servers_created_inspect'] ||= {})[node] = server
      server
    end
    client = inspect('container', @ids.fetch(['container', "#{PREFIX}-client"]))
    raise 'published client ports refused' unless client.dig('HostConfig', 'PortBindings').to_h.empty?
    raise 'root or unexpected client user refused' unless client.dig('Config', 'User') == "#{Process.uid}:0" && Process.uid != 0
    raise 'client evidence bind missing' unless client.fetch('Mounts').size == 1 && client.fetch('Mounts')[0]['Type'] == 'bind' &&
      client.fetch('Mounts')[0]['Source'] == @run && client.fetch('Mounts')[0]['Destination'] == '/evidence'
    network = inspect('network', @ids.fetch(['network', "#{PREFIX}-network"]))
    raise 'runtime network must be internal' unless network['Internal'] == true
    (servers + [client]).each do |container|
      raise 'unexpected runtime network' unless container.dig('NetworkSettings', 'Networks').keys == ["#{PREFIX}-network"]
    end
    @result['network_created_inspect'] = network
    @result['client_created_inspect'] = client
    @result['owned_ids'] = @ids.to_h { |(kind, name), id| ["#{kind}:#{name}", id] }
    persist
  end
  def runtime_rosetta(name = PREFIX, readiness: 120)
    server_id = @ids.fetch(['container', name])
    stop = [now + readiness, @deadline].min
    budget = lambda do
      remaining = stop - now
      raise 'SQL server process readiness deadline' unless remaining.positive?
      [10, remaining].min
    end
    loop do
      raw, = docker('container', 'inspect', server_id, seconds: budget.call)
      server = JSON.parse(raw).fetch(0)
      ManualModel.owned!('container', server, @owner, server_id)
      raise 'SQL server stopped before Rosetta proof' unless server.dig('State', 'Running')
      top, = docker('top', server_id, '-eo', 'pid,comm,args', seconds: budget.call)
      pids = ManualModel.sqlserver_pids!(top)
      unless pids.empty?
        executables = pids.to_h do |pid|
          # Numeric PID belongs to an actual sqlservr comm/argv in this pinned container.
          [pid, vm("sudo -n readlink /proc/#{pid}/exe", "#{name}-pid-#{pid}-exe", seconds: budget.call).strip]
        end
        raise 'actual SQL server interpreter is not Rosetta (QEMU forbidden)' unless executables.values.all? { |exe| exe.include?('rosetta') && !exe.downcase.include?('qemu') }
        confirmed_top, = docker('top', server_id, '-eo', 'pid,comm,args', seconds: budget.call)
        # SQL startup can fork another sqlservr between the two snapshots.
        # Retry the entire owned-container/executable proof within the SAME deadline.
        unless ManualModel.sqlserver_pids!(confirmed_top).sort == pids.sort
          remaining = stop - now
          raise 'SQL server process readiness deadline' unless remaining.positive?
          sleep [1, remaining].min
          next
        end
        (@result['actual_sqlserver_rosetta'] ||= {})[name] = { 'container_id' => server_id, 'owner' => @owner,
          'docker_top' => top, 'docker_top_confirmed' => confirmed_top, 'pid_executables' => executables }
        return
      end
      remaining = stop - now
      raise 'SQL server process readiness deadline' unless remaining.positive?
      sleep [1, remaining].min
    end
  end
  def start_secondary
    free = host_free
    raise 'before-secondary host free space below 70 GiB' if free < 70 * ManualModel::GIB
    ManualModel.topology_stage!(@base, @owner, @stage)
    id = @ids.fetch(['container', "#{PREFIX}-secondary"])
    obj = inspect('container', id); ManualModel.owned!('container', obj, @owner, id)
    ManualModel.server_mounts!(obj, @stage, 'secondary', Process.uid)
    raise 'secondary already running before owned gate' if obj.dig('State', 'Running')
    docker('start', id, seconds: 15)
    @secondary_started = true
    runtime_rosetta("#{PREFIX}-secondary", readiness: 90)
    obj = inspect('container', id); ManualModel.owned!('container', obj, @owner, id)
    raise 'secondary did not stay running' unless obj.dig('State', 'Running')
    docker('exec', '--user', "#{Process.uid}:0", id, 'id', seconds: 5)
    docker('exec', '--user', "#{Process.uid}:0", id, 'df', '-Pk', '/var/opt/mssql', '/backup', seconds: 5)
    gate = { 'owner' => @owner, 'secondary_container_id' => id, 'secondary_rosetta_verified' => true,
             'mounts_verified' => true, 'free_bytes' => free, 'started_at' => Time.now.utc.iso8601 }
    ManualModel.secondary_gate!(gate, @owner, id)
    path = File.join(@run, 'ag-secondary-ready')
    File.open(path + '.tmp', 'w', 0o600) { |f| f.puts(JSON.generate(gate)); f.flush; f.fsync }
    File.rename(path + '.tmp', path)
    @result['secondary_gate'] = gate
    persist
  end
  def run_work
    setup_context
    exclusive_preflight
    @result['free_before_bytes'] = host_free
    raise 'host requires at least 70 GiB free after MySQL cleanup' if @result['free_before_bytes'] < 70 * ManualModel::GIB
    setup_stage
    sql, = docker('image', 'inspect', SQL_IMAGE)
    sdk, = docker('image', 'inspect', SDK_IMAGE)
    @result['sql_image'] = JSON.parse(sql); @result['sdk_image'] = JSON.parse(sdk)
    raise 'SQL image must be amd64' unless @result['sql_image'][0]['Architecture'] == 'amd64'
    raise 'SDK image must be native arm64' unless @result['sdk_image'][0]['Architecture'] == 'arm64'
    @mutations = true
    docker('build', '--platform', 'linux/arm64', '--pull=false', '--label', "#{ManualModel::LABEL}=#{@owner}", '-t', @client_image, '.', seconds: 900)
    obj = inspect('image', @client_image); ManualModel.owned!('image', obj, @owner); @build_id = obj.fetch('Id')
    docker('compose', '-p', PREFIX, '-f', 'compose.yaml', 'create', '--no-recreate', '--no-build', '--pull', 'never', seconds: 60)
    capture_ids
    client = @ids.fetch(['container', "#{PREFIX}-client"]); server = @ids.fetch(['container', PREFIX])
    docker('cp', "#{client}:/provenance/.", File.join(@run, 'provenance'), seconds: 20)
    docker('start', server)
    @server_started = true
    runtime_rosetta
    docker('exec', '--user', "#{Process.uid}:0", server, 'id', seconds: 10)
    docker('exec', '--user', "#{Process.uid}:0", server, 'df', '-Pk', '/var/opt/mssql', '/var/opt/mssql/data', '/var/opt/mssql/log', seconds: 10)
    # Attach is foreground; bounded controller owns CLI, client container and server to terminal exit.
    _, attached = docker('start', '--attach', client, seconds: 21_600, watch: true, check: false)
    final = inspect('container', client)
    @result['client_exit_status'] = final.dig('State', 'ExitCode')
    @result['client_final_inspect'] = final
    raise 'client still running / incomplete output' if final.dig('State', 'Running')
    raise 'driver or attach failed' unless @result['client_exit_status'] == 0 && attached['exit_status'] == 0
    import_probe
    raise 'secondary gate/probe mismatch' unless @result['probe']['secondary_gate'] == @result['secondary_gate']
    [PREFIX, "#{PREFIX}-secondary"].each do |name|
      proof = @result.dig('actual_sqlserver_rosetta', name)
      raise 'missing actual per-instance Rosetta evidence' unless proof && proof['container_id'] == @ids.fetch(['container', name]) && !proof['pid_executables'].empty?
    end
    raise 'exact fixture/18 sample acceptance failed'  unless ManualModel.complete?(@result.fetch('probe'))
    # Re-observe after actual successful SQL on both nodes, beyond entrypoint/bootstrap.
    @result['initial_sqlserver_rosetta'] = Marshal.load(Marshal.dump(@result['actual_sqlserver_rosetta']))
    [PREFIX, "#{PREFIX}-secondary"].each { |name| runtime_rosetta(name, readiness: 10) }
    @result['final_sqlserver_rosetta_verified_after_18_counts'] = true
    persist
    %w[completion precision transaction_release service_budget].each { |k| @result[k] = @result['probe'].fetch(k) }
    @result['completion'] = 'passed_18_commands_owner_audit_pending'
  end
  def remove_owned_stage_via_guest
    ManualModel.topology_stage!(@base, @owner, @stage)
    target = File.dirname(@stage)
    raise 'cleanup target is not exact nonce directory' unless target == File.join(File.realpath(@base), @owner)
    # Delete through the guest filesystem so virtiofs receives unlink/forget, rather than
    # retaining host-open descriptors to files removed behind the guest's back.
    vm('sudo -n -- rm -rf -- ' + Shellwords.escape(target), 'remove-owned-stage', seconds: 60, cleanup: true)
    raise 'stage or owner marker still present' if File.exist?(target) || File.symlink?(target)
    @result['stage_removed_through_guest'] = true
    @result['stage_absent'] = true
  end
  def cleanup
    @cleanup_deadline = now + 240
    errors = []
    attempt = lambda do |&block|
      begin
        block.call
      rescue Exception => e
        errors << "#{e.class}: #{e.message}"
      end
    end
    # A partially completed Compose create may have resources even when the CLI failed.
    if @cli && @preflight && @mutations
      NAMES.each do |kind, names|
        names.each do |name|
          attempt.call do
            flags = kind == 'container' ? ['ls', '-a'] : ['ls']
            fmt = kind == 'container' ? '{{.Names}}' : '{{.Name}}'
            out, = docker(kind, *flags, '--filter', "name=^#{name}$", '--format', fmt, seconds: 6, cleanup: true)
            next if out.strip.empty? # successful inventory only, never inspect-failure-as-absence
            obj = inspect(kind, name, cleanup: true)
            ManualModel.owned!(kind, obj, @owner, @ids[[kind, name]])
            @ids[[kind, name]] ||= obj.fetch('Id')
          end
        end
      end
      ["#{PREFIX}-client", "#{PREFIX}-secondary", PREFIX].each do |name|
        id = @ids[['container', name]]
        next unless id
        stopped = false
        if name != "#{PREFIX}-client"
          attempt.call { docker('logs', id, cleanup: true, seconds: 8) }
          attempt.call { vm('df -Pk /; cat /proc/meminfo', 'guest-before-cleanup', seconds: 5, cleanup: true) } if now + 20 < @cleanup_deadline
        end
        attempt.call do
          obj = inspect('container', id, cleanup: true); ManualModel.owned!('container', obj, @owner, id)
          if obj.dig('State', 'Running')
            stop_seconds = name == "#{PREFIX}-client" ? 20 : 60
            docker('stop', '--time', stop_seconds.to_s, id, cleanup: true, seconds: stop_seconds + 5)
            obj = inspect('container', id, cleanup: true)
          end
          @result["#{name}_terminal_state"] = obj.fetch('State')
          docker('logs', id, cleanup: true, seconds: 8) if name != "#{PREFIX}-client"
          raise 'container stop incomplete' if obj.dig('State', 'Running')
          stopped = true
          raise 'forced container kill recorded as failure' if obj.dig('State', 'ExitCode') == 137 || obj.dig('State', 'OOMKilled')
          started = name == PREFIX ? @server_started : (name == "#{PREFIX}-secondary" && @secondary_started)
          ManualModel.terminal_server!(obj.fetch('State'), started) if name != "#{PREFIX}-client"
        end
        attempt.call do
          # If stop failed, no deletion of stage; force removal is evidence of failed cleanup.
          unless stopped
            errors << "forced removal required: #{name}"
            obj = inspect('container', id, cleanup: true); ManualModel.owned!('container', obj, @owner, id)
            docker('rm', '--force', '--volumes', id, cleanup: true, seconds: 10)
            next
          end
          docker('rm', '--volumes', id, cleanup: true, seconds: 10)
        end
      end
      network = @ids[['network', "#{PREFIX}-network"]]
      attempt.call do
        if network
          obj = inspect('network', network, cleanup: true); ManualModel.owned!('network', obj, @owner, network)
          docker('network', 'rm', network, cleanup: true, seconds: 8)
        end
      end
      attempt.call do
        # Build may have completed even if the build CLI failed: resolve tag, verify nonce, then pin ID.
        out, = docker('image', 'ls', '--filter', "reference=#{@client_image}", '--format', '{{.ID}}', cleanup: true, seconds: 5)
        unless out.strip.empty?
          image = inspect('image', @client_image, cleanup: true); ManualModel.owned!('image', image, @owner, @build_id)
          docker('image', 'rm', image.fetch('Id'), cleanup: true, seconds: 10)
        end
      end
      %w[container network volume].each do |kind|
        attempt.call { raise "owned #{kind} resources remain" unless inventory(kind, cleanup: true).empty? }
      end
      # Successful inventories of fixed reserved names too: a replaced/unowned resource must not pass.
      NAMES.each do |kind, names|
        names.each do |name|
          attempt.call do
            flags = kind == 'container' ? ['ls', '-a'] : ['ls']
            out, = docker(kind, *flags, '--filter', "name=^#{name}$", '--format', '{{json .}}', cleanup: true, seconds: 5)
            raise "reserved name remains: #{name}" unless out.strip.empty?
          end
        end
      end
      attempt.call do
        out, = docker('image', 'ls', '--filter', "label=#{ManualModel::LABEL}=#{@owner}", '--format', '{{.ID}}', cleanup: true, seconds: 5)
        raise 'owned image remains' unless out.strip.empty?
      end
    end
    # Delete data only after successful container absence inventories, no symlink/marker ambiguity.
    if @stage_created && errors.empty?
      attempt.call do
        remove_owned_stage_via_guest
      end
    end
    attempt.call do
      # host_free uses ordinary lifecycle deadline; switch temporarily to independent cleanup budget.
      prior = @deadline; @deadline = @cleanup_deadline
      @result['free_after_bytes'] = host_free
      if @result['free_before_bytes'] && @stage_created && @result['stage_absent']
        restored = @result['free_after_bytes'] >= @result['free_before_bytes'] - ManualModel::GIB
        @result['capacity_restored_within_1GiB'] = restored
        raise 'host capacity not restored within 1 GiB (shared-host observation)' unless restored
      end
    ensure
      @deadline = prior
    end
    @result['cleanup_errors'] = errors
    @result['cleanup'] = errors.empty? ? (@stage_created ? 'passed_owned_resources_and_stage_absent' : 'not_created_preflight_refusal') : 'failed_or_unknown_preserved_evidence'
  end
  def run
    abort 'Ruby 4.0.9 required; use mise exec -- ruby lab.rb all' unless RUBY_VERSION == '4.0.9'
    abort 'owner controller must run as a non-root host user' if Process.uid == 0
    @lock = File.open(File.join(__dir__, '.lab.lock'), File::RDWR | File::CREAT, 0o600)
    abort 'another manual100m controller owns this lifecycle' unless @lock.flock(File::LOCK_EX | File::LOCK_NB)
    @owner = SecureRandom.hex(16)
    @client_image = "#{PREFIX}-client:#{@owner}"
    @run = File.join(ROOT, 'results/sqlserver/manual-count-100m/runs', "#{Time.now.utc.strftime('%Y%m%dT%H%M%SZ')}-#{@owner}")
    FileUtils.mkdir_p(@run, mode: 0o700)
    @capacity_path = ROOT
    @sequence = 0; @ids = {}; @start = now; @deadline = @start + 21_600
    @result = { 'status' => 'incomplete', 'exit_status' => 1, 'owner' => @owner, 'started_at' => Time.now.utc.iso8601,
                'source_before' => hashes, 'commands' => [], 'completion' => 'incomplete', 'precision' => 'unverified',
                'transaction_release' => 'unverified', 'service_budget' => 'UNAVAILABLE_quiet_static_lab', 'cleanup' => 'unverified' }
    @env = { 'PATH' => ENV.fetch('PATH'), 'HOME' => Dir.home, 'LANG' => 'C.UTF-8',
             'DOCKER_CONFIG' => File.join(Dir.home, '.docker'), 'MANUAL_OWNER' => @owner }
    %w[INT TERM].each { |sig| Signal.trap(sig) { raise Interrupt, sig } }
    begin
      bindings = JSON.parse(File.read(File.join(__dir__, 'input-bindings.json')))
      baseline = File.join(ROOT, 'results/reviews/sqlserver-manual-count-100m/baseline.json')
      raise 'baseline binding drift' unless Digest::SHA256.file(baseline).hexdigest == bindings.fetch('baselineSha256')
      raise 'manual binding drift' unless Digest::SHA256.file(File.join(__dir__, 'manual/sql-server.md')).hexdigest == bindings.fetch('manualSha256')
      @result['input_bindings'] = bindings
      persist
      run_work
    rescue Exception => e
      @result['error'] = "#{e.class}: #{e.message}"
    ensure
      begin
        cleanup
      rescue Exception => e
        @result['cleanup'] = 'failed_or_unknown'; @result['cleanup_error'] = "#{e.class}: #{e.message}"
      end
      import_probe
      @result['source_after'] = hashes
      @result['source_unchanged'] = @result['source_before'] == @result['source_after']
      @result['elapsed_monotonic_seconds'] = now - @start
      @result['finished_at'] = Time.now.utc.iso8601
      if !@result['error'] && @result['completion'] == 'passed_18_commands_owner_audit_pending' &&
         @result['cleanup'] == 'passed_owned_resources_and_stage_absent' && @result['source_unchanged'] &&
         @result['commands'].none? { |c| c['timed_out'] || c['forced_process_kill'] || c['terminated_by_controller'] }
        @result['status'] = 'diagnostic_complete_owner_audit_required'
        @result['exit_status'] = 0
      end
      @result['artifact_hashes'] = Dir.glob(File.join(@run, '**', '*')).select { |p| File.file?(p) && p != File.join(@run, 'result.json') }
          .sort.to_h { |p| [p.delete_prefix(@run + '/'), Digest::SHA256.file(p).hexdigest] }
      persist
      puts JSON.generate({ result: File.join(@run, 'result.json'), exit_status: @result['exit_status'], cleanup: @result['cleanup'] })
    end
    @result['exit_status']
  end
end

if $PROGRAM_NAME == __FILE__
  abort 'usage: mise exec -- ruby lab.rb all; fixed geometry, no DSN/scale arguments' unless ARGV == ['all']
  exit ManualLab.new.run
end
