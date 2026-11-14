#!/usr/bin/env ruby
# Actual POSIX bootstrap -> controller -> mise task -> explicit exec, in an
# isolated fake project. Fake mise/lab never access Docker or a DB.
require "tmpdir"
require "fileutils"
require "json"
require "open3"
require "rbconfig"
require "digest"

source = File.expand_path("..", __dir__)
script_dir = ".riela/workflows/table-count-guideline-review/scripts"
checked = []
%w[success failure source_change early_failure].each do |mode|
  Dir.mktmpdir("fake-white-all-") do |root|
    FileUtils.mkdir_p([File.join(root, script_dir), File.join(root, "bench"), File.join(root, "fake-bin"), File.join(root, "results/reviews"), File.join(root, "results/requested")])
    %w[run-requested.rb run-white-all.sh].each { |name| FileUtils.cp(File.join(source, script_dir, name), File.join(root, script_dir, name), preserve: true) }
    FileUtils.cp(File.join(source, "mise.toml"), File.join(root, "mise.toml"))
    File.write(File.join(root, "bench/input.py"), "# initial source\n")
    File.write(File.join(root, "results/reviews/final-all-proof.json"), '{"old":true}')
    File.write(File.join(root, "results/reviews/final-all-candidate.json"), '{"old_candidate":true}')
    File.write(File.join(root, "results/reviews/final-all-run.log"), "previous complete log\n")
    stale = JSON.generate(status: "completed", ran: [{id: "old_experiment", ok: true}], lab_data_intact: true)
    File.write(File.join(root, "results/requested/last_run.json"), stale)
    File.write(File.join(root, "lab.rb"), <<~'FAKE')
      require "json"
      require "fileutils"
      abort "wrong lab args" unless ARGV == ['all']
      abort "aggregate cap" unless ENV.fetch("LAB_REQUESTED_BUDGET_MINUTES") == "0"
      puts "fake white-all output before completion; child Ruby #{RUBY_VERSION}"
      exit(7) if ENV.fetch("FAKE_MODE") == "early_failure"
      FileUtils.mkdir_p("results/raw/synthetic/service-0")
      File.binwrite("results/raw/synthetic/service-0/00000.jsonl.gz", "fake chunk for collection only")
      File.write("bench/input.py", "# changed during measurement") if ENV.fetch("FAKE_MODE") == "source_change"
      File.write("results/requested/last_run.json", JSON.generate(status: "completed", ran: [{id: "new_experiment", ok: true}], lab_data_intact: true))
      exit(ENV.fetch("FAKE_MODE") == "failure" ? 7 : 0)
    FAKE
    fake = "#!#{RbConfig.ruby}\n" + <<~'RUBY_FAKE'
      require "json"
      require "rbconfig"
      File.open("launch-trace.jsonl", "a") { |out| out.puts JSON.generate(args: ARGV, cwd: Dir.pwd, ruby: RUBY_VERSION) }
      if ARGV[0,3] == %w[exec -- ruby]
        exec(RbConfig.ruby, *ARGV.drop(3))
      elsif ARGV == %w[run all]
        task = File.read("mise.toml")[/\[tasks\.all\].*?^run = "([^"]+)"/m, 1]
        abort "missing actual all task" unless task == "mise exec -- ruby lab.rb all"
        exec("/bin/sh", "-c", task)
      else
        abort "unexpected fake mise args"
      end
    RUBY_FAKE
    fake_path = File.join(root, "fake-bin/mise")
    File.write(fake_path, fake)
    FileUtils.chmod(0o755, fake_path)
    output, status = Open3.capture2e({ "PATH" => "#{File.join(root, 'fake-bin')}:#{ENV.fetch('PATH')}", "FAKE_MODE" => mode },
                                    "/bin/sh", File.join(root, script_dir, "run-white-all.sh"), chdir: root)
    raise output unless status.success?
    payload = JSON.parse(output).fetch("payload")
    candidate = JSON.parse(File.read(File.join(root, "results/reviews/final-all-candidate.json")))
    expected = mode == "success"
    raise "false runner success: #{mode}" unless payload.fetch("runnerExitOk") == expected
    raise "exit was lost" unless candidate.fetch("exitCode") == (%w[failure early_failure].include?(mode) ? 7 : 0)
    raise "source drift ignored" unless candidate.fetch("measurementSourceUnchanged") == (mode != "source_change")
    raise "proof auto-created" if File.exist?(File.join(root, "results/reviews/final-all-proof.json"))
    raise "old proof lost" unless Dir[File.join(root, "results/reviews/previous-final-all-proof-*.json")].size == 1
    prior_candidate = Dir[File.join(root, "results/reviews/previous-*-final-all-candidate.json")]
    raise "old candidate lost" unless prior_candidate.size == 1 && JSON.parse(File.read(prior_candidate[0])) == {"old_candidate"=>true}
    prior_log = Dir[File.join(root, "results/reviews/previous-*-final-all-run.log")]
    raise "old log lost" unless prior_log.size == 1 && File.read(prior_log[0]) == "previous complete log\n"
    log = File.read(File.join(root, "results/reviews/final-all-run.log"))
    raise "log/runtime lost" unless log.include?("fake white-all output") && log.include?("controller Ruby #{RUBY_VERSION}") && log.include?("child Ruby #{RUBY_VERSION}")
    raise "log hash wrong" unless Digest::SHA256.hexdigest(log) == candidate.fetch("logSha256")
    snapshot = File.join(root, payload.fetch("measurementSnapshot"))
    manifest = JSON.parse(File.read(File.join(snapshot, "manifest.json")))
    manifest.fetch("files").each { |path, sha| raise "bad snapshot #{path}" unless Digest::SHA256.file(File.join(snapshot, path)).hexdigest == sha }
    raise "wrapper not fingerprinted" unless candidate.fetch("measurementSourceBefore").key?("#{script_dir}/run-white-all.sh")
    raise "wrapper not retained" unless manifest.fetch("files").key?("#{script_dir}/run-white-all.sh")
    raise "nested raw not retained" unless mode == "early_failure" || manifest.fetch("files").key?("results/raw/synthetic/service-0/00000.jsonl.gz")
    if mode == "early_failure"
      raise "stale summary credited" unless payload.fetch("requestedStatus") == "not_reached" && payload.fetch("ran").empty? && !payload.fetch("lab_data_intact")
      raise "stale file mutated before CLI lock" unless File.read(File.join(root,"results/requested/last_run.json")) == stale
      raise "freshness wrong" if candidate.fetch("requestedSummaryFresh") || manifest.fetch("requestedSummaryFresh")
    else
      raise "fresh summary rejected" unless candidate.fetch("requestedSummaryFresh") && payload.fetch("ran").map { |r| r.fetch("id") } == ['new_experiment']
    end
    trace = File.readlines(File.join(root, "launch-trace.jsonl")).map { |line| JSON.parse(line) }
    raise "bootstrap/task exec chain bypassed" unless trace.map { |t| t.fetch("args")[0,3] } == [["exec", "--", "ruby"], ["run", "all"], ["exec", "--", "ruby"]]
    raise "cwd changed" unless trace.all? { |t| File.realpath(t.fetch("cwd")) == File.realpath(root) }
    raise "premature acceptance" if candidate.fetch("status") == "accepted"
    checked << mode
  end
end
puts JSON.generate(check: "actual bootstrap/controller/task chain with isolated fake mise/lab; no DB", cases: checked,
                   nonzero_exit_propagated: true, source_change_rejected: true, stale_proof_archived: true,
                   previous_attempt_retained: true, stale_completed_summary_uncredited: true, old_lab_summary_untouched: true,
                   nested_raw_in_snapshot: true, wrapper_fingerprinted: true, explicit_exec_chain_and_cwd: true,
                   process_success_requires_owner_audit: true, logs_and_snapshot_hashes_verified: true)
