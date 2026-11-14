# frozen_string_literal: true
# Standard library only. This controller owns every created runtime resource until reaped.
require 'json'
require 'digest'
require 'fileutils'
require 'securerandom'
require 'time'
require 'socket'
require 'uri'

abort 'usage: ruby lab.rb all (Ruby 4.0.9)' unless ARGV == ['all']
abort 'Ruby 4.0.9 required' unless RUBY_VERSION == '4.0.9'
ROOT = File.expand_path('../..', __dir__)
# Hold the descriptor for the entire foreground lifecycle; never unlink a held lock.
lifecycle_lock = File.open(File.join(__dir__, '.lab.lock'), File::RDWR | File::CREAT, 0o600)
abort 'another Issue 1 controller owns this lifecycle' unless lifecycle_lock.flock(File::LOCK_EX | File::LOCK_NB)
SOURCE = %w[lab.rb mise.toml compose.yaml Dockerfile .dockerignore CountIssue1.csproj NuGet.Config packages.lock.json Program.cs packages.lock.json runbook-example-inputs.json README.md verify_author.py].freeze
NAMES = { 'container' => %w[lab-sqlserver-issue1-client lab-sqlserver-issue1],
          'volume' => ['lab-sqlserver-issue1-data'], 'network' => ['lab-sqlserver-issue1-network'] }.freeze
SERVER_IMAGE = 'mcr.microsoft.com/mssql/server:2025-latest@sha256:245f6b1c8903edae52cb20fa31419cefcde1a49938bb8c3447099011fc871ca3'
owner = SecureRandom.hex(16)
run_dir = File.join(ROOT, 'results/issue-1/runs', "#{Time.now.utc.strftime('%Y%m%dT%H%M%S')}-#{owner[0, 12]}")
FileUtils.mkdir_p(run_dir)
raw = File.join(run_dir, 'controller.log')
File.write(raw, '')
clock = -> { Process.clock_gettime(Process::CLOCK_MONOTONIC) }
started = clock.call
deadline = started + 540
hashes = lambda do
  SOURCE.to_h do |f|
    path = File.join(__dir__, f)
    ["sqlserver/issue1/#{f}", File.exist?(path) ? Digest::SHA256.file(path).hexdigest : 'absent']
  end
end
client_image = "lab-sqlserver-issue1-client:#{owner}"
result = { 'status' => 'incomplete', 'started_at' => Time.now.utc.iso8601, 'run_id' => owner,
           'raw_log' => raw, 'source_before' => hashes.call, 'commands' => [],
           'completion' => 'incomplete', 'ownership' => 'unverified', 'transaction_release' => 'unverified',
           'recovery' => 'unverified', 'precision' => 'unverified', 'service_budget' => 'unavailable_functional_only',
           'cleanup' => 'unverified', 'exit_status' => 1,
           'limits' => 'ARM64/amd64 SQL emulation unsupported; no native performance or production SLO certification' }
# Remove inherited endpoint/config/TLS/compose overrides. Read current context from the fixed local config only.
env = { 'PATH' => ENV.fetch('PATH'), 'HOME' => Dir.home, 'LANG' => 'C.UTF-8', 'ISSUE1_OWNER' => owner,
        'DOCKER_CONFIG' => File.join(Dir.home, '.docker') }
execute = lambda do |args, seconds: 60, check: true, cleanup: false|
  stop = [clock.call + seconds, cleanup ? started + 600 : deadline].min
  raise 'total deadline exhausted' if stop <= clock.call
  offset = File.size(raw)
  File.open(raw, 'a') { |f| f.puts("\n#{Time.now.utc.iso8601} argv=#{JSON.generate(args)}") }
  status = nil
  timed_out = false
  File.open(raw, 'a') do |log|
    pid = Process.spawn(env, *args, chdir: __dir__, in: File::NULL, out: log, err: log, unsetenv_others: true)
    begin
      loop do
        waited = Process.waitpid2(pid, Process::WNOHANG)
        if waited
          status = waited[1]
          break
        end
        if clock.call >= stop
          timed_out = true
          Process.kill('TERM', pid) rescue Errno::ESRCH
          grace = [clock.call + 2, started + 600].min
          loop do
            waited = Process.waitpid2(pid, Process::WNOHANG)
            if waited
              status = waited[1]
              break
            end
            break if clock.call >= grace
            sleep 0.05
          end
          unless status
            Process.kill('KILL', pid) rescue Errno::ESRCH
            status = Process.waitpid2(pid)[1]
          end
          break
        end
        sleep 0.1
      end
    ensure
      unless status
        Process.kill('KILL', pid) rescue Errno::ESRCH
        status = Process.waitpid2(pid)[1] rescue nil
      end
    end
  end
  code = status&.exitstatus
  code ||= 128 + status.termsig if status&.signaled?
  record = { 'argv' => args, 'exit_status' => code, 'timed_out' => timed_out, 'log' => raw, 'offset' => offset }
  result['commands'] << record
  output = File.read(raw)[offset..]
  File.open(raw, 'a') { |f| f.puts("exit_status=#{code} timed_out=#{timed_out}") }
  # JSON CLI parsing excludes our header.
  body = output.lines.drop(2).join
  raise "command failed (#{code}, timeout=#{timed_out}): #{args.join(' ')}" if check && (code != 0 || timed_out)
  [body, code]
end
cli = nil
mutations_allowed = false
inspection = lambda do |kind, name, cleanup: false|
  out, code = execute.call(cli + [kind, 'inspect', name], seconds: 5, check: false, cleanup: cleanup)
  code == 0 ? JSON.parse(out).fetch(0) : nil
end
owned = lambda do |kind, obj|
  next false unless obj
  labels = kind == 'container' ? obj.dig('Config', 'Labels') : obj['Labels']
  labels && labels['count.issue1.owner'] == owner
end
%w[INT TERM].each { |sig| Signal.trap(sig) { raise Interrupt, sig } }
begin
  local_config = File.join(Dir.home, '.docker', 'config.json')
  context = File.exist?(local_config) ? JSON.parse(File.read(local_config)).fetch('currentContext', 'default') : 'default'
  out, = execute.call(['docker', 'context', 'inspect', context], seconds: 10)
  endpoint = JSON.parse(out).fetch(0).fetch('Endpoints').fetch('docker').fetch('Host')
  uri = URI.parse(endpoint)
  raise 'only an existing local Unix Docker context is allowed' unless uri.scheme == 'unix' && (uri.host.nil? || uri.host.empty?) && File.socket?(uri.path)
  cli = ['docker', '--host', endpoint]
  result['context'] = { 'name' => context, 'endpoint' => endpoint }
  execute.call(cli + ['info', '--format', '{{.ID}} {{.ServerVersion}} {{.OSType}} {{.Architecture}}'], seconds: 10)
  # Inventory must succeed. Inspect failure is never evidence that a resource is absent.
  NAMES.each do |kind, names|
    flags = kind == 'container' ? ['ls', '-a'] : ['ls']
    format = kind == 'container' ? '{{.Names}}' : '{{.Name}}'
    out, = execute.call(cli + [kind] + flags + ['--format', format], seconds: 10)
    existing = out.lines.map(&:strip)
    raise "reserved #{kind} name already exists" unless (existing & names).empty?
  end
  mutations_allowed = true
  result['ownership'] = 'exclusive_names_preflight_passed'
  execute.call(cli + ['image', 'inspect', SERVER_IMAGE], seconds: 10)
  sdk, = execute.call(cli + ['image', 'inspect', 'mcr.microsoft.com/dotnet/sdk:10.0@sha256:212fe4cb74627d5adf9817a4088906ae3edff1bd853bb27565d4b46731efe61e'], seconds: 10)
  result['sdk_before_build'] = JSON.parse(sdk)
  execute.call(cli + ['build', '--pull=false', '--label', "count.issue1.owner=#{owner}", '-t', client_image, '.'], seconds: 240)
  execute.call(cli + ['compose', '-p', 'lab-sqlserver-issue1', '-f', 'compose.yaml', 'create', '--no-recreate', '--no-build', '--pull', 'never'], seconds: 30)
  NAMES.each { |kind, names| names.each { |name| raise "ownership mismatch #{name}" unless owned.call(kind, inspection.call(kind, name)) } }
  result['ownership'] = 'verified_all_runtime_labels'
  execute.call(cli + ['start', 'lab-sqlserver-issue1'], seconds: 10)
  execute.call(cli + ['start', '--attach', 'lab-sqlserver-issue1-client'], seconds: 220)
  client = inspection.call('container', 'lab-sqlserver-issue1-client')
  result['client_exit_status'] = client.dig('State', 'ExitCode')
  raise 'client still running / incomplete log' if client.dig('State', 'Running')
  execute.call(cli + ['cp', 'lab-sqlserver-issue1-client:/evidence/.', run_dir], seconds: 10)
  probe = JSON.parse(File.read(File.join(run_dir, 'probe.json')))
  result['probe'] = probe
  %w[completion ownership transaction_release recovery precision].each { |k| result[k] = probe.fetch(k) }
  raise 'functional checks did not pass' unless result['client_exit_status'] == 0 && probe['completion'] == 'passed'
  result['completion'] = 'passed'
rescue Exception => e # ensure cleanup even on interrupt; never report an incomplete run as passing
  result['error'] = "#{e.class}: #{e.message}"
ensure
  if cli && mutations_allowed
    cleanup_errors = []
    # Each step is independent best effort; an unowned resource must not prevent cleanup of our others.
    attempt = lambda do |&block|
      begin
        block.call
      rescue Exception => e
        cleanup_errors << "#{e.class}: #{e.message}"
      end
    end
    attempt.call { execute.call(cli + ['logs', 'lab-sqlserver-issue1'], seconds: 4, check: false, cleanup: true) }
    attempt.call { execute.call(cli + ['cp', 'lab-sqlserver-issue1-client:/evidence/.', run_dir], seconds: 4, check: false, cleanup: true) }
    NAMES.each do |kind, names|
      names.each do |name|
        attempt.call do
          obj = inspection.call(kind, name, cleanup: true)
          next unless obj
          raise "refusing cleanup of unowned #{kind}:#{name}" unless owned.call(kind, obj)
          args = kind == 'container' ? ['rm', '--force', name] : ['rm', name]
          execute.call(cli + [kind] + args, seconds: 5, cleanup: true)
        end
      end
    end
    attempt.call do
      image = inspection.call('image', client_image, cleanup: true)
      if image
        raise 'refuse unowned build image removal' unless image.dig('Config','Labels','count.issue1.owner') == owner
        execute.call(cli + ['image', 'rm', client_image], seconds: 5, cleanup: true)
      end
    end
    NAMES.each do |kind, names|
      attempt.call do
        flags = kind == 'container' ? ['ls', '-a'] : ['ls']
        format = kind == 'container' ? '{{.Names}}' : '{{.Name}}'
        out, = execute.call(cli + [kind] + flags + ['--format', format], seconds: 4, cleanup: true)
        raise "cleanup incomplete #{kind}" unless (out.lines.map(&:strip) & names).empty?
      end
    end
    result['cleanup'] = cleanup_errors.empty? ? 'passed_owned_runtime_resources_absent' : 'failed_or_unknown'
    result['cleanup_errors'] = cleanup_errors
  else
    result['cleanup'] = 'not_created_preflight_refusal'
  end
  result['source_after'] = hashes.call
  result['source_unchanged'] = result['source_before'] == result['source_after']
  result['elapsed_monotonic_seconds'] = clock.call - started
  result['finished_at'] = Time.now.utc.iso8601
  if !result['error'] && result['completion'] == 'passed' && result['cleanup'] == 'passed_owned_runtime_resources_absent' && result['source_unchanged'] && result['commands'].none? { |c| c['timed_out'] }
    result['status'] = 'functional_validation_passed_owner_audit_required'
    result['exit_status'] = 0
  end
  File.open(raw, 'a') { |f| f.puts("CONTROLLER_FINAL_EXIT_STATUS=#{result['exit_status']}") }
  result['raw_log_sha256'] = Digest::SHA256.file(raw).hexdigest
  result['artifacts'] = Dir.children(run_dir).sort.to_h { |f| [f, Digest::SHA256.file(File.join(run_dir, f)).hexdigest] }
  File.write(File.join(run_dir, 'result.json'), JSON.pretty_generate(result) + "\n")
  puts JSON.generate({ result: File.join(run_dir, 'result.json'), exit_status: result['exit_status'], cleanup: result['cleanup'] })
end
exit result['exit_status']
