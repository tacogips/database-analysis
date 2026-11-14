# frozen_string_literal: true
# Exercise actual Lab methods against an isolated filesystem; no Docker/DB.
require "tmpdir"
require "fileutils"
require "json"
source = File.expand_path("..", __dir__)
Dir.mktmpdir("cache-test-") do |dir|
  FileUtils.cp(File.join(source, "lab.rb"), File.join(dir, "lab.rb"))
  FileUtils.cp(File.join(source, "mysql-images.json"), File.join(dir, "mysql-images.json"))
  FileUtils.cp(File.join(source, "mise.toml"), File.join(dir, "mise.toml"))
  FileUtils.mkdir_p(File.join(dir, "results/raw/synthetic/service-0"))
  require File.join(dir, "lab")
  lab = Lab.new
  file = File.join(dir, "results/measurement.json")
  raw = File.join(dir, "results/raw/synthetic/service-0/00000.jsonl.gz")
  File.write(file, JSON.generate(value: 42, raw: {format: "jsonl+gzip-v1", chunks: [{path: "raw/synthetic/service-0/00000.jsonl.gz"}]}))
  File.binwrite(raw, "synthetic telemetry, not DB data")
  paths = [file, raw].map { |p| p.delete_prefix(dir + "/") }
  raise "nested references not collected" unless lab.send(:raw_artifact_references, paths[0]) == [paths[1]]
  artifacts = paths.map { |p| lab.send(:artifact_manifest, p).transform_keys(&:to_s) }
  raise "nested raw missing from collection" unless paths.all? { |p| lab.send(:measurement_snapshot).key?(p) }
  status = { "ok" => true, "fingerprint" => "current", "runs" => [{ "exit_code" => 0 }], "artifacts" => artifacts }
  check = ->(record, fp = "current") { lab.send(:reusable_status?, record, fp) }
  raise "valid result not reused" unless check.call(status)
  raise "failed result reused" if check.call(status.merge("ok" => false))
  raise "old inputs reused" if check.call(status, "changed")
  raise "failed process reused" if check.call(status.merge("runs" => [{ "exit_code" => 124 }]))
  raise "wide semantic failure exit1 reused" if check.call(status.merge("runs" => [{ "exit_code" => 1 }]))
  File.binwrite(raw, "changed raw telemetry")
  raise "changed raw chunk reused" if check.call(status)
  File.unlink(raw)
  raise "missing raw chunk reused" if check.call(status)
  File.binwrite(raw, "synthetic telemetry, not DB data")
  File.write(file, '{"value":43}')
  raise "changed artifact reused" if check.call(status)
  File.unlink(file)
  raise "missing artifact reused" if check.call(status)
  fp = lab.send(:input_fingerprint, File.join(dir, "bench/requested/fake.py"), {})
  File.open(File.join(dir, "mise.toml"), "a") { |out| out.puts "# synthetic config change" }
  raise "mise.toml excluded from cache inputs" if fp == lab.send(:input_fingerprint, File.join(dir, "bench/requested/fake.py"), {})
end
puts JSON.generate(success_reused: true, failure_rejected: true, old_inputs_rejected: true,
                   failed_process_rejected: true, wide_failure_exit1_rejected: true, changed_or_missing_artifact_rejected: true,
                   nested_raw_collected: true, changed_or_missing_raw_chunk_rejected: true, mise_config_invalidates_cache: true, db_access: false)
