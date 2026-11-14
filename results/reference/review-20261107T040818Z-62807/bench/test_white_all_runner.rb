#!/usr/bin/env ruby
# Test the actual workflow script in an isolated fake project. No Docker/DB access.
require "tmpdir"
require "fileutils"
require "json"
require "open3"
require "rbconfig"

runner = File.expand_path("../.riela/workflows/table-count-guideline-review/scripts/run-requested.rb", __dir__)
checked = []
%w[success failure source_change].each do |mode|
  Dir.mktmpdir("fake-white-all-") do |root|
    relative = ".riela/workflows/table-count-guideline-review/scripts/run-requested.rb"
    FileUtils.mkdir_p([File.dirname(File.join(root, relative)), File.join(root, "bench"), File.join(root, "fake-bin"), File.join(root, "results/reviews")])
    FileUtils.cp(runner, File.join(root, relative))
    File.write(File.join(root, "bench/input.py"), "# initial source\n")
    File.write(File.join(root, "results/reviews/final-all-proof.json"), '{"old":true}')
    fake = <<~RUBY
      #!#{RbConfig.ruby}
      require "json"
      require "fileutils"
      abort "wrong command" unless ARGV == %w[run all]
      abort "aggregate cap" unless ENV.fetch("LAB_REQUESTED_BUDGET_MINUTES") == "0"
      FileUtils.mkdir_p("results/requested")
      puts "fake white-all output before completion"
      File.write("bench/input.py", "# changed during measurement") if ENV.fetch("FAKE_MODE") == "source_change"
      File.write("results/requested/last_run.json", JSON.generate(status: "completed", ran: [], lab_data_intact: true))
      exit(ENV.fetch("FAKE_MODE") == "failure" ? 7 : 0)
    RUBY
    fake_path = File.join(root, "fake-bin/mise")
    File.write(fake_path, fake)
    FileUtils.chmod(0o755, fake_path)
    output, status = Open3.capture2e({ "PATH" => "#{File.join(root, 'fake-bin')}:#{ENV.fetch('PATH')}", "FAKE_MODE" => mode },
                                    RbConfig.ruby, File.join(root, relative))
    raise output unless status.success?
    payload = JSON.parse(output).fetch("payload")
    candidate = JSON.parse(File.read(File.join(root, "results/reviews/final-all-candidate.json")))
    expected = mode == "success"
    raise "false runner success: #{mode}" unless payload.fetch("runnerExitOk") == expected
    raise "exit was lost" unless candidate.fetch("exitCode") == (mode == "failure" ? 7 : 0)
    raise "source drift ignored" unless candidate.fetch("measurementSourceUnchanged") == (mode != "source_change")
    raise "proof auto-created" if File.exist?(File.join(root, "results/reviews/final-all-proof.json"))
    raise "old proof lost" unless Dir[File.join(root, "results/reviews/previous-final-all-proof-*.json")].size == 1
    raise "log lost" unless File.read(File.join(root, "results/reviews/final-all-run.log")).include?("fake white-all output")
    raise "snapshot absent" unless File.file?(File.join(root, payload.fetch("measurementSnapshot"), "manifest.json"))
    raise "premature acceptance" if candidate.fetch("status") == "accepted"
    checked << mode
  end
end
puts JSON.generate(check: "actual workflow runner with isolated fake mise; no DB", cases: checked,
                   nonzero_exit_propagated: true, source_change_rejected: true, stale_proof_archived: true,
                   process_success_requires_owner_audit: true, logs_and_snapshot_retained: true)
