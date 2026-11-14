#!/usr/bin/env ruby
# frozen_string_literal: true

# Reproduce the whole lab from nothing: mise run all
# Touches only this project's containers and ./.data. Never point it at a real database.
require "fileutils"
require "digest"
require "json"
require "open3"
require "rbconfig"

class Lab
  class MatrixCleanupError < StandardError
    attr_reader :process_result, :cleanup

    def initialize(process_result, cleanup, original_error)
      @process_result, @cleanup = process_result, cleanup
      super("matrix cleanup failed; original=#{original_error.inspect}; see #{cleanup[:record]}")
    end
  end
  ROOT = __dir__
  STEPS = %w[prepare_images reset up resource_preflight self_checks setup load verify count_shapes lock_factors experiments matrix requested summarize].freeze
  # Supported lines only (checked 2026-11): 8.4 LTS, 9.7 LTS, 26.10 Innovation. First and latest of each LTS,
  # so a behaviour change inside a line shows up. Update this list when support changes.
  MATRIX = %w[8.4.0 8.4.13 9.7.0 9.7.4 26.10.0].freeze
  MYSQL_IMAGES = JSON.parse(File.read(File.join(ROOT, "mysql-images.json"))).fetch("images").freeze
  KEEP_IMAGE = "8.4.13"
  REQUESTED_TIMEOUT = 1200
  NETWORK = "bulk-read-lab_default"
  PIPELINES = {
    "all" => STEPS,
    "prepare" => %w[prepare_images reset up resource_preflight self_checks setup load verify],
    "measure" => %w[prepare_images resource_preflight self_checks count_shapes lock_factors experiments matrix requested summarize]
  }.freeze
  REPLICA_SETUP = "STOP REPLICA; RESET REPLICA ALL; CHANGE REPLICATION SOURCE TO SOURCE_HOST='primary',SOURCE_USER='root'," \
                  "SOURCE_PASSWORD='lab',SOURCE_AUTO_POSITION=1,GET_SOURCE_PUBLIC_KEY=1; START REPLICA;"

  # Image pulls/builds must precede performance. Never prune shared caches/data.
  def prepare_images
    @prepared_images = {}
    MYSQL_IMAGES.each_value do |image|
      _output, found = capture("docker", "image", "inspect", image)
      run("docker", "pull", image) unless found.success?
      identity, checked = capture("docker", "image", "inspect", "--format", "{{.Id}}", image)
      abort "pinned image unavailable: #{image}" unless checked.success? && /\Asha256:[0-9a-f]{64}\s*\z/.match?(identity)
      @prepared_images[image] = identity.strip
    end
  end

  def resource_preflight
    # Chosen fail-fast reserves, not measured minimum hardware requirements.
    record = { vm_reserve_kib: 2 * 1024 * 1024, host_reserve_kib: 40 * 1024 * 1024,
               recorded_at: Time.now.utc.to_s, prepared_images: @prepared_images, automatic_pruning: false, ok: false }
    output, status = capture("docker", "run", "--rm", "--name", "lab-space-preflight",
      "--mount", "type=bind,source=#{ROOT},target=/lab-host,readonly", "--entrypoint", "sh",
      MYSQL_IMAGES.fetch(KEEP_IMAGE), "-c", "df -Pk / /lab-host")
    record[:df_output], record[:exit_code] = output, status.exitstatus
    rows = output.lines.drop(1).map(&:split)
    record[:vm_available_kib] = Integer(rows.find { |r| r.last == "/" }&.[](3) || "0")
    record[:host_available_kib] = Integer(rows.find { |r| r.last == "/lab-host" }&.[](3) || "0")
    record[:ok] = status.success? && record[:vm_available_kib] >= record[:vm_reserve_kib] &&
                  record[:host_available_kib] >= record[:host_reserve_kib]
    FileUtils.mkdir_p(File.join(ROOT, "results"))
    File.write(File.join(ROOT, "results", "resource_preflight.json"), JSON.pretty_generate(record))
    abort "insufficient/unreadable lab space; inspect results/resource_preflight.json; no automatic pruning" unless record[:ok]
  end

  def reset
    run("docker", "compose", "down", "--volumes", "--remove-orphans")
    FileUtils.rm_rf(".data")
    # Published evidence and review records are inputs to the documentation, not generated table data.
    Dir.glob(File.join(ROOT, "results", "*"), File::FNM_DOTMATCH).each do |path|
      next if %w[. .. reference reviews].include?(File.basename(path))
      FileUtils.rm_rf(path)
    end
    FileUtils.mkdir_p([".data/primary", ".data/replica", ".data/out", "results"])
  end

  def up = run("docker", "compose", "up", "-d", "--build", "--wait", "primary", "replica", "bench")

  def self_checks
    checks = %w[test_nonlocking_scope.py test_workload_accounting.py test_sampling_contract.py test_dependencies.py test_kill_timer.py test_accuracy_frontier.py test_wide_outcomes.py test_protocol_integrity.py test_reqmix_protocol.py test_raw_evidence.py]
    checks.each do |script|
      output, status = capture("docker", "exec", "lab-bench", "python3", "/work/bench/#{script}")
      abort "self-check #{script} failed: #{output}" unless status.success?
      File.write(File.join(ROOT, "results", "self_check_#{script.delete_suffix('.py')}.json"), JSON.pretty_generate(JSON.parse(output)))
    end
    %w[test_runner_cache.rb test_white_all_runner.rb test_matrix_cleanup.rb test_resource_preflight.rb test_nonlocking_runner.rb].each do |script|
      output, status = capture(RbConfig.ruby, File.join(ROOT, "bench", script))
      abort "self-check #{script} failed: #{output}" unless status.success?
      File.write(File.join(ROOT, "results", "self_check_#{script.delete_suffix('.rb')}.json"), JSON.pretty_generate(JSON.parse(output)))
    end
    puts "self-checks: accounting, sampling, dependencies, timer, protocols, cache, white-all and matrix cleanup passed"
  end

  def setup
    Dir[File.join(ROOT, "sql", "*.sql")].sort.each { |file| sql("lab-primary", stdin: File.read(file)) }
    sql("lab-replica", "-e", REPLICA_SETUP)
  end

  def load = bench("load.py", log: "load")
  def verify = bench("verify_data.py")
  def count_shapes = bench("count_shapes.py", log: "count_shapes")
  def lock_factors = bench("lock_factors.py", log: "lock_factors")
  # The full Python registry remains available through explicit experiment names.
  def experiments
    bench("experiments.py", "run", *default_scope.fetch("baseImpactNames"), log: "experiments")
  end
  def summarize = bench("summarize.py")

  def matrix(versions = MATRIX)
    versions.each { |version| probe_version(version) }
  end

  # Runs the default allowlist, or explicitly named available IDs, inside the bench container only.
  # Successful unchanged results may be reused; skipped scripts are never credited as passes.
  # A <id>.versions file (one MySQL version per line) runs it once per version: argv is <host> <version>.
  # A <id>.encrypted file also gives that server the keyring component.
  # Output goes to results/requested/<id>.log and the outcome to results/requested/<id>.status.json.
  def requested(ids = [])
    scripts = requested_paths(ids) # fail closed before writing a summary or inspecting Docker
    scope = default_scope
    skipped = scope.fetch("skippedRequestedScripts").reject { |item| ids.include?(item.fetch("id")) }
    scope_record = { scope_sha256: Digest::SHA256.file(File.join(ROOT, "bench/nonlocking-scope.json")).hexdigest,
                     selection: ids.empty? ? "default_allowlist" : "explicit_ids",
                     planned_scripts: scripts.map { |p| File.basename(p, ".py") }, skipped_by_user_scope: skipped }
    FileUtils.mkdir_p(File.join(ROOT, "results", "requested"))
    File.write(File.join(ROOT, "results", "requested", "last_run.json"), JSON.pretty_generate(**scope_record, status: "started", ran: [], lab_data_intact: false))
    identities, inspected = capture("docker", "inspect", "--format", "{{.Image}}", "lab-primary", "lab-bench")
    abort "could not inspect lab images" unless inspected.success?
    @cache_images = identities.lines.map(&:strip)
    abort "invalid lab image identities" unless @cache_images.size == 2 && @cache_images.all? { |id| /\Asha256:[0-9a-f]{64}\z/.match?(id) }
    dependencies = dependency_hashes(scripts)
    results = []
    scripts.each do |path|
      id = File.basename(path, ".py")
      status_path = File.join(ROOT, "results", "requested", "#{id}.status.json")
      fingerprint = input_fingerprint(path, dependencies.fetch(id))
      previous = begin
        JSON.parse(File.read(status_path)) if File.file?(status_path)
      rescue JSON::ParserError
        nil
      end
      if id != "00-budget-start" && reusable_status?(previous, fingerprint)
        results << previous.merge("reused" => true).transform_keys(&:to_sym)
        next
      end

      started = Time.now
      before = measurement_snapshot
      encrypted = File.exist?(path.sub(/\.py\z/, ".encrypted"))
      runs = versions_for(path).map { |version| run_requested(id, version, encrypted) }
      after = measurement_snapshot
      produced = after.keys.select { |file| before[file] != after[file] }
      required_file = path.sub(/\.py\z/, ".outputs")
      required = File.file?(required_file) ? File.readlines(required_file, chomp: true).map(&:strip).reject(&:empty?) : []
      missing = required.reject { |file| produced.include?(file) }
      raw_references = (produced + required).uniq.flat_map { |file| raw_artifact_references(file) }.uniq
      missing.concat(raw_references.reject { |file| artifact_manifest(file) })
      cleanup_records = runs.filter_map { |item| item.dig(:cleanup, :record) }
      artifact_paths = (produced + required + raw_references + cleanup_records + ["results/requested/#{id}.log"]).uniq
      artifacts = artifact_paths.filter_map { |file| artifact_manifest(file) }
      final_fingerprint = input_fingerprint(path, dependency_hashes([path]).fetch(id))
      ok = runs.all? { |run| run[:ok] } && missing.empty? && fingerprint == final_fingerprint
      outcome = { id: id, ok: ok, seconds: (Time.now-started).round, reused: false,
                  log: "results/requested/#{id}.log", runs: runs, artifacts: artifacts,
                  missing_outputs: missing, fingerprint: final_fingerprint,
                  inputs_changed_during_run: fingerprint != final_fingerprint }
      File.write(status_path, JSON.pretty_generate(outcome))
      results << outcome
      break unless ok # a failed process may still own DB work; never start a later comparison
    end
    verify_ok = begin
      raise SystemExit unless results.all? { |result| result[:ok] }
      verify
      true
    rescue SystemExit
      false
    end
    summary = { **scope_record,
                not_run: scripts.drop(results.length).map { |p| { id: File.basename(p, ".py"), outcome: "not_started", reason: "prior_failed_run; cleanup_or_scope_not_accepted" } },
                status: verify_ok && results.all? { |result| result[:ok] } ? "completed" : "failed", ran: results, lab_data_intact: verify_ok }
    File.write(File.join(ROOT, "results", "requested", "last_run.json"), JSON.pretty_generate(summary))
    puts JSON.generate(summary)
    abort "requested experiments failed; inspect results/requested/last_run.json" unless verify_ok && results.all? { |result| result[:ok] }
  end

  def experiment(names)
    abort "usage: lab.rb experiment <name>..." if names.empty?
    bench("experiments.py", "run", *names, log: "experiments")
    summarize
  end

  private

  def default_scope
    scope = JSON.parse(File.read(File.join(ROOT, "bench/nonlocking-scope.json")))
    %w[baseImpactNames requestedScripts].each do |key|
      items = scope.fetch(key)
      ids = key == "requestedScripts" ? items.map { |item| item.fetch("id") } : items
      abort "invalid default scope: #{key}" unless !ids.empty? && ids.all? { |id| id.is_a?(String) && !id.empty? } && ids.uniq == ids
    end
    scope
  end

  def requested_paths(ids)
    paths = Dir[File.join(ROOT, "bench", "requested", "*.py")].sort
    selected = ids.empty? ? default_scope.fetch("requestedScripts").map { |item| item.fetch("id") } : ids
    unknown = selected - paths.map { |path| File.basename(path, ".py") }
    abort "unknown requested experiment: #{unknown.join(', ')}" unless unknown.empty?
    # Preserve the existing lexical execution order and explicit-ID semantics.
    paths.select { |path| selected.include?(File.basename(path, ".py")) }
  end

  def probe_version(version)
    with_version(version) do |name|
      bench("matrix_probe.py", name, version, log: "matrix")
      File.write(File.join(ROOT, "results", "matrix_#{version}_tools.txt"), tool_report(name))
    end
  end

  # Lab only: the key file lives inside the throwaway container.
  def keyring_mounts
    dir = File.join(ROOT, "bench", "keyring")
    # Run as mysql: initializing as root would leave a key file the server cannot read.
    ["--user", "mysql", "-v", "#{dir}/mysqld.my:/usr/sbin/mysqld.my:ro",
     "-v", "#{dir}/component_keyring_file.cnf:/usr/lib64/mysql/plugin/component_keyring_file.cnf:ro"]
  end

  def with_version(version, encrypted: false)
    @last_matrix_cleanup = nil
    abort "unsupported lab version: #{version}" unless MATRIX.include?(version)
    name = "lab-matrix-#{version.tr('.', '-')}"
    image = MYSQL_IMAGES.fetch(version)
    # Anonymous volumes consume the small Docker VM disk, independently of the host's free space.
    # Matrix data is disposable and belongs only to this compose lab.
    data_dir = File.join(ROOT, ".data", name)
    existing, listed = capture("docker", "container", "ls", "-a", "--filter", "name=^/#{name}$", "--format", "{{.Names}}")
    abort "could not inspect lab containers" unless listed.success?
    run("docker", "rm", "-f", "-v", name) unless existing.strip.empty?
    FileUtils.rm_rf(data_dir)
    FileUtils.mkdir_p(data_dir)
    run("docker", "run", "-d", "--name", name, "--network", NETWORK, "--memory", "1500m", "--cpus", "2",
        "--cpuset-cpus", "0,1",
        "--mount", "type=bind,source=#{data_dir},target=/var/lib/mysql",
        *(encrypted ? keyring_mounts : []), "-e", "MYSQL_ROOT_PASSWORD=lab", image, "--innodb-buffer-pool-size=256M",
        "--log-bin=binlog", "--server-id=9", "--gtid-mode=ON", "--enforce-gtid-consistency=ON")
    wait_ready(name)
    image_id, inspected = capture("docker", "inspect", "--format", "{{.Image}}", name)
    abort "could not inspect #{name}" unless inspected.success?
    @matrix_image = { reference: image, id: image_id.strip }
    result = yield name
  ensure
    original_error = $!
    if name
      @last_matrix_cleanup = teardown_version(name, data_dir)
      result[:cleanup] = @last_matrix_cleanup if result.is_a?(Hash)
    end
    @matrix_image = nil
    # Images are a shared immutable cache, not a container/data resource owned by
    # this invocation. They may be reused by the next requested experiment.
    if name && !@last_matrix_cleanup[:ok]
      raise MatrixCleanupError.new(result, @last_matrix_cleanup, original_error)
    end
  end

  def teardown_version(name, data_dir)
    outcome = { container: name, data_dir: data_dir, ok: false, container_removed: false,
                data_removed: false, image_disposition: "shared_immutable_cache_retained" }
    begin
      output, removed = capture("docker", "rm", "-f", "-v", name)
      outcome[:remove_exit_code] = removed.exitstatus
      outcome[:remove_output] = output
      listed, inspected = capture("docker", "container", "ls", "-a", "--filter", "name=^/#{name}$", "--format", "{{.Names}}")
      outcome[:verify_exit_code] = inspected.exitstatus
      outcome[:container_removed] = removed.success? && inspected.success? && listed.strip.empty?
      if outcome[:container_removed] && data_dir
        FileUtils.rm_rf(data_dir)
        outcome[:data_removed] = !File.exist?(data_dir)
      end
      outcome[:ok] = outcome[:container_removed] && outcome[:data_removed]
    rescue StandardError => error
      outcome[:error] = "#{error.class}: #{error.message}"
    ensure
      dir = File.join(ROOT, "results", "matrix_cleanup")
      FileUtils.mkdir_p(dir)
      relative = "results/matrix_cleanup/#{name}-#{Process.pid}-#{Time.now.to_i}-#{Process.clock_gettime(Process::CLOCK_MONOTONIC, :nanosecond)}.json"
      outcome[:record] = relative
      File.write(File.join(ROOT, relative), JSON.pretty_generate(outcome))
    end
    outcome
  end

  def versions_for(path)
    list = path.sub(/\.py\z/, ".versions")
    File.exist?(list) ? File.readlines(list, chomp: true).map(&:strip).reject(&:empty?) : [nil]
  end

  def run_requested(id, version, encrypted)
    return requested_process(id, []) if version.nil?

    with_version(version, encrypted: encrypted) { |name| requested_process(id, [name, version]) }
  rescue StandardError => error
    process_result = error.is_a?(MatrixCleanupError) && error.process_result.is_a?(Hash) ? error.process_result : {}
    { exit_code: nil, version: version, **process_result, ok: false,
      runner_error: "#{error.class}: #{error.message}", cleanup: @last_matrix_cleanup }
  end

  # The timeout is enforced inside the container, so a runaway script cannot outlive its limit.
  def requested_process(id, args)
    budget = ENV.fetch("LAB_REQUESTED_BUDGET_MINUTES", "0")
    abort "invalid LAB_REQUESTED_BUDGET_MINUTES" unless /\A\d+\z/.match?(budget)
    cmd = ["docker", "exec", "-e", "PYTHONPATH=/work/bench", "lab-bench", "timeout", REQUESTED_TIMEOUT.to_s,
           "python3", "-u", "/work/bench/requested/#{id}.py", *args]
    cmd.insert(2, "-e", "LAB_REQUESTED_BUDGET_MINUTES=#{budget}")
    output, status = capture(*cmd)
    File.write(File.join(ROOT, "results", "requested", "#{id}.log"), "$ #{args.join(' ')}\n#{output}\nexit_code=#{status.exitstatus.inspect} signal=#{status.termsig.inspect}\n", mode: "a")
    { ok: status.success?, exit_code: status.exitstatus, signal: status.termsig, version: args[1],
      mysql_image: @matrix_image || { reference: MYSQL_IMAGES.fetch(KEEP_IMAGE), id: @cache_images&.first },
      bench_image_id: @cache_images&.last }
  end

  def dependency_hashes(paths)
    ids = paths.map { |path| File.basename(path, ".py") }
    output, status = capture("docker", "exec", "lab-bench", "python3", "/work/bench/dependencies.py", *ids)
    abort "could not inspect Python dependencies: #{output}" unless status.success?
    JSON.parse(output)
  end

  def input_fingerprint(path, dependencies)
    inputs = [__FILE__, "compose.yaml", "mysql-images.json", "mise.toml", "bench/Dockerfile", "bench/dependencies.py", "bench/nonlocking-scope.json"] +
             Dir[File.join(ROOT, "sql", "*.sql")] + Dir[File.join(ROOT, "bench", "keyring", "*")] +
             %w[.versions .encrypted .outputs].map { |suffix| path.sub(/\.py\z/, suffix) }
    hashes = inputs.to_h do |file|
      absolute = File.expand_path(file, ROOT)
      [absolute.delete_prefix(ROOT + "/"), File.file?(absolute) ? Digest::SHA256.file(absolute).hexdigest : nil]
    end
    Digest::SHA256.hexdigest(JSON.generate(dependencies: dependencies.sort.to_h, inputs: hashes.sort.to_h, images: @cache_images))
  end

  def measurement_snapshot
    (Dir[File.join(ROOT, "results", "*.json")] + Dir[File.join(ROOT, "results", "raw", "**", "*")].select { |file| File.file?(file) }).to_h do |file|
      stat = File.stat(file)
      [file.delete_prefix(ROOT + "/"), [stat.size, stat.mtime.to_i, stat.mtime.nsec]]
    end
  end

  def artifact_manifest(relative)
    file = File.expand_path(relative, ROOT)
    return nil unless File.file?(file) && File.realpath(file).start_with?(File.join(ROOT, "results") + "/")
    { path: relative, sha256: Digest::SHA256.file(file).hexdigest, bytes: File.size(file) }
  end

  def raw_artifact_references(relative)
    return [] unless relative.end_with?(".json") && artifact_manifest(relative)
    collect = lambda do |value|
      case value
      when Hash
        if value["format"] == "jsonl+gzip-v1"
          value.fetch("chunks").map do |chunk|
            path = chunk.fetch("path")
            raise "invalid raw chunk path" unless %r{\Araw/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/\d+\.jsonl\.gz\z}.match?(path) && !path.split("/").any? { |part| %w[. ..].include?(part) }
            "results/#{path}"
          end
        else
          value.values.flat_map { |child| collect.call(child) }
        end
      when Array then value.flat_map { |child| collect.call(child) }
      else []
      end
    end
    collect.call(JSON.parse(File.read(File.join(ROOT, relative))))
  end

  def reusable_status?(previous, fingerprint)
    return false unless previous.is_a?(Hash) && previous["ok"] == true && previous["fingerprint"] == fingerprint
    runs = previous["runs"]
    return false unless runs.is_a?(Array) && !runs.empty? && runs.all? { |run| run["exit_code"] == 0 }
    artifacts = previous["artifacts"]
    return false unless artifacts.is_a?(Array) && !artifacts.empty?
    artifacts.all? do |artifact|
      current = artifact_manifest(artifact.fetch("path"))
      current && current[:sha256] == artifact["sha256"] && current[:bytes] == artifact["bytes"]
    end
  rescue KeyError, Errno::ENOENT, TypeError, NoMethodError
    false
  end

  def wait_ready(name, seconds: 300)
    deadline = Time.now + seconds
    until capture("docker", "exec", name, "mysql", "-h127.0.0.1", "-uroot", "-plab", "-e", "SELECT 1").last.success?
      abort "#{name} did not start" if Time.now > deadline
      sleep 2
    end
  end

  def tool_report(name)
    capture("docker", "exec", name, "mysql", "--version").first
  end

  def capture(*cmd)
    Open3.capture2e(*cmd, chdir: ROOT)
  end

  def run(*cmd, stdin: nil, log: nil)
    puts "==> #{cmd.join(' ')}" unless stdin
    Open3.popen2e(*cmd, chdir: ROOT) do |input, output, status|
      input.write(stdin) if stdin
      input.close
      log_file = log && File.open(File.join(ROOT, "results", "#{log}.log"), "a")
      output.each_line do |line|
        next if line.include?("Using a password on the command line")

        puts line
        $stdout.flush
        log_file&.write(line)
        log_file&.flush
      end
      log_file&.close
      abort "failed (#{status.value.exitstatus}): #{cmd.join(' ')}" unless status.value.success?
    end
  end

  def sql(container, *args, stdin: nil)
    run("docker", "exec", "-i", container, "mysql", "-uroot", "-plab", *args, stdin: stdin || "")
  end

  def bench(script, *args, log: nil)
    run("docker", "exec", "lab-bench", "python3", "-u", "/work/bench/#{script}", *args, log: log)
  end
end

if $PROGRAM_NAME == __FILE__
command, *rest = ARGV
Dir.chdir(Lab::ROOT)
puts "lab Ruby #{RUBY_VERSION} #{RbConfig.ruby}; command=#{ARGV.inspect}"
$stdout.flush
lab_lock = File.open(File.join(Lab::ROOT, ".lab.lock"), File::RDWR | File::CREAT, 0o600)
abort "another lab runner owns this environment" unless lab_lock.flock(File::LOCK_EX | File::LOCK_NB)
active, inspected = Lab.new.send(:capture, "docker", "top", "lab-bench")
if inspected.success?
  abort "a Python process is still active in lab-bench; wait for its owner" if active.lines.drop(1).any? { |line| /\bpython[0-9.]*\b/.match?(line) }
elsif !active.match?(/No such container/i)
  abort "could not inspect lab-bench: #{active}"
end
lab = Lab.new
case command
when *Lab::PIPELINES.keys then Lab::PIPELINES.fetch(command).each { |step| lab.public_send(step) }
when "requested" then lab.requested(rest)
when *Lab::STEPS then lab.public_send(command)
when "experiment" then lab.experiment(rest)
when "matrix-only" then lab.matrix(rest)
else
  abort "usage: lab.rb #{(Lab::PIPELINES.keys + Lab::STEPS + ['experiment <name>...']).join('|')}"
end

end
