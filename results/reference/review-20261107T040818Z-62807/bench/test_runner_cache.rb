# frozen_string_literal: true
require "tmpdir"
require_relative "../lab"
lab = Lab.new
Dir.mktmpdir("cache-test-", File.join(Lab::ROOT, "results")) do |dir|
  file = File.join(dir, "measurement.json")
  File.write(file, '{"value":42}')
  relative = file.delete_prefix(Lab::ROOT + "/")
  artifact = lab.send(:artifact_manifest, relative).transform_keys(&:to_s)
  status = { "ok" => true, "fingerprint" => "current", "runs" => [{ "exit_code" => 0 }], "artifacts" => [artifact] }
  check = ->(record, fp = "current") { lab.send(:reusable_status?, record, fp) }
  raise "valid result not reused" unless check.call(status)
  raise "failed result reused" if check.call(status.merge("ok" => false))
  raise "old inputs reused" if check.call(status, "changed")
  raise "failed process reused" if check.call(status.merge("runs" => [{ "exit_code" => 124 }]))
  File.write(file, '{"value":43}')
  raise "changed artifact reused" if check.call(status)
  File.unlink(file)
  raise "missing artifact reused" if check.call(status)
end
puts JSON.generate(success_reused: true, failure_rejected: true, old_inputs_rejected: true,
                   failed_process_rejected: true, changed_or_missing_artifact_rejected: true)
