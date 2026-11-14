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
  ROOT = __dir__
  STEPS = %w[reset up self_checks setup load verify count_shapes lock_factors experiments matrix requested summarize].freeze
  # Supported lines only (checked 2026-11): 8.4 LTS, 9.7 LTS, 26.10 Innovation. First and latest of each LTS,
  # so a behaviour change inside a line shows up. Update this list when support changes.
  MATRIX = %w[8.4.0 8.4.13 9.7.0 9.7.4 26.10.0].freeze
  MYSQL_IMAGES = JSON.parse(File.read(File.join(ROOT, "mysql-images.json"))).fetch("images").freeze
  KEEP_IMAGE = "8.4.13"
  REQUESTED_TIMEOUT = 1200
  NETWORK = "bulk-read-lab_default"
  PIPELINES = {
    "all" => STEPS,
    "prepare" => %w[reset up self_checks setup load verify],
    "measure" => %w[self_checks count_shapes lock_factors experiments matrix requested summarize]
  }.freeze
  REPLICA_SETUP = "STOP REPLICA; RESET REPLICA ALL; CHANGE REPLICATION SOURCE TO SOURCE_HOST='primary',SOURCE_USER='root'," \
                  "SOURCE_PASSWORD='lab',SOURCE_AUTO_POSITION=1,GET_SOURCE_PUBLIC_KEY=1; START REPLICA;"

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
    checks = %w[test_workload_accounting.py test_sampling_contract.py test_dependencies.py test_kill_timer.py]
    checks.each do |script|
      output, status = capture("docker", "exec", "lab-bench", "python3", "/work/bench/#{script}")
      abort "self-check #{script} failed: #{output}" unless status.success?
      File.write(File.join(ROOT, "results", "self_check_#{script.delete_suffix('.py')}.json"), JSON.pretty_generate(JSON.parse(output)))
    end
    output, status = capture(RbConfig.ruby, File.join(ROOT, "bench", "test_runner_cache.rb"))
    abort "runner cache self-check failed: #{output}" unless status.success?
    File.write(File.join(ROOT, "results", "self_check_runner_cache.json"), JSON.pretty_generate(JSON.parse(output)))
    puts "self-checks: scheduler, sampling deadline/cleanup, dependencies, timer and result cache passed"
  end

  def setup
    Dir[File.join(ROOT, "sql", "*.sql")].sort.each { |file| sql("lab-primary", stdin: File.read(file)) }
    sql("lab-replica", "-e", REPLICA_SETUP)
  end

  def load = bench("load.py", log: "load")
  def verify = bench("verify_data.py")
  def count_shapes = bench("count_shapes.py", log: "count_shapes")
  def lock_factors = bench("lock_factors.py", log: "lock_factors")
  def experiments = bench("experiments.py", "all", log: "experiments")
  def summarize = bench("summarize.py")

  def matrix(versions = MATRIX)
    versions.each { |version| probe_version(version) }
  end

  # Runs each bench/requested/<id>.py that is new or edited since its last run, inside the bench container only.
  # A <id>.versions file (one MySQL version per line) runs it once per version: argv is <host> <version>.
  # A <id>.encrypted file also gives that server the keyring component.
  # Output goes to results/requested/<id>.log and the outcome to results/requested/<id>.status.json.
  def requested(ids = [])
    FileUtils.mkdir_p(File.join(ROOT, "results", "requested"))
    File.write(File.join(ROOT, "results", "requested", "last_run.json"), JSON.pretty_generate(status: "started", ran: [], lab_data_intact: false))
    scripts = Dir[File.join(ROOT, "bench", "requested", "*.py")].sort
    unless ids.empty?
      unknown = ids - scripts.map { |path| File.basename(path, ".py") }
      abort "unknown requested experiment: #{unknown.join(', ')}" unless unknown.empty?
      scripts.select! { |path| ids.include?(File.basename(path, ".py")) }
    end
    identities, inspected = capture("docker", "inspect", "--format", "{{.Image}}", "lab-primary", "lab-bench")
    abort "could not inspect lab images" unless inspected.success?
    @cache_images = identities.lines.map(&:strip)
    abort "invalid lab image identities" unless @cache_images.size == 2 && @cache_images.all? { |id| /\Asha256:[0-9a-f]{64}\z/.match?(id) }
    dependencies = dependency_hashes(scripts)
    results = scripts.map do |path|
      id = File.basename(path, ".py")
      status_path = File.join(ROOT, "results", "requested", "#{id}.status.json")
      fingerprint = input_fingerprint(path, dependencies.fetch(id))
      previous = begin
        JSON.parse(File.read(status_path)) if File.file?(status_path)
      rescue JSON::ParserError
        nil
      end
      if id != "00-budget-start" && reusable_status?(previous, fingerprint)
        next previous.merge("reused" => true).transform_keys(&:to_sym)
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
      artifact_paths = (produced + required + ["results/requested/#{id}.log"]).uniq
      artifacts = artifact_paths.filter_map { |file| artifact_manifest(file) }
      final_fingerprint = input_fingerprint(path, dependency_hashes([path]).fetch(id))
      ok = runs.all? { |run| run[:ok] } && missing.empty? && fingerprint == final_fingerprint
      outcome = { id: id, ok: ok, seconds: (Time.now-started).round, reused: false,
                  log: "results/requested/#{id}.log", runs: runs, artifacts: artifacts,
                  missing_outputs: missing, fingerprint: final_fingerprint,
                  inputs_changed_during_run: fingerprint != final_fingerprint }
      File.write(status_path, JSON.pretty_generate(outcome))
      outcome
    end
    verify_ok = begin
      verify
      true
    rescue SystemExit
      false
    end
    summary = { status: verify_ok && results.all? { |result| result[:ok] } ? "completed" : "failed", ran: results, lab_data_intact: verify_ok }
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
    yield name
  ensure
    if name
      _, removed = capture("docker", "rm", "-f", "-v", name)
      FileUtils.rm_rf(data_dir) if removed.success? && data_dir
    end
    @matrix_image = nil
    capture("docker", "rmi", image) if image && version != KEEP_IMAGE
  end

  def versions_for(path)
    list = path.sub(/\.py\z/, ".versions")
    File.exist?(list) ? File.readlines(list, chomp: true).map(&:strip).reject(&:empty?) : [nil]
  end

  def run_requested(id, version, encrypted)
    return requested_process(id, []) if version.nil?

    with_version(version, encrypted: encrypted) { |name| requested_process(id, [name, version]) }
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
    inputs = [__FILE__, "compose.yaml", "mysql-images.json", "bench/Dockerfile", "bench/dependencies.py"] +
             Dir[File.join(ROOT, "sql", "*.sql")] + Dir[File.join(ROOT, "bench", "keyring", "*")] +
             %w[.versions .encrypted .outputs].map { |suffix| path.sub(/\.py\z/, suffix) }
    hashes = inputs.to_h do |file|
      absolute = File.expand_path(file, ROOT)
      [absolute.delete_prefix(ROOT + "/"), File.file?(absolute) ? Digest::SHA256.file(absolute).hexdigest : nil]
    end
    Digest::SHA256.hexdigest(JSON.generate(dependencies: dependencies.sort.to_h, inputs: hashes.sort.to_h, images: @cache_images))
  end

  def measurement_snapshot
    Dir[File.join(ROOT, "results", "*.json")].to_h do |file|
      stat = File.stat(file)
      [file.delete_prefix(ROOT + "/"), [stat.size, stat.mtime.to_i, stat.mtime.nsec]]
    end
  end

  def artifact_manifest(relative)
    file = File.expand_path(relative, ROOT)
    return nil unless File.file?(file) && File.realpath(file).start_with?(File.join(ROOT, "results") + "/")
    { path: relative, sha256: Digest::SHA256.file(file).hexdigest, bytes: File.size(file) }
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
