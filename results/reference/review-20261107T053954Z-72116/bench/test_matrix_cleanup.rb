# frozen_string_literal: true
# Actual teardown primitive, fake Docker replies, temporary owned data only.
require "tmpdir"
require_relative "../lab"
FakeStatus = Struct.new(:exitstatus) do
  def success? = exitstatus == 0
end
class FakeCleanupLab < Lab
  attr_accessor :mode
  private
  def capture(*cmd)
    return ["remove refused", FakeStatus.new(1)] if mode == :remove_failed && cmd[1] == "rm"
    return ["inspection unavailable", FakeStatus.new(1)] if mode == :verify_failed && cmd[1] == "container"
    return ["fake-still-present", FakeStatus.new(0)] if mode == :still_present && cmd[1] == "container"
    ["", FakeStatus.new(0)]
  end
end
checks = []
%i[success remove_failed verify_failed still_present].each do |mode|
  Dir.mktmpdir("matrix-cleanup-test-") do |parent|
    data = File.join(parent, "owned-data")
    FileUtils.mkdir_p(data)
    File.write(File.join(data, "marker"), "owned")
    lab = FakeCleanupLab.new
    lab.mode = mode
    record = lab.send(:teardown_version, "fake-#{mode}", data)
    expected = mode == :success
    raise "wrong cleanup outcome #{mode}" unless record[:ok] == expected
    raise "unsafe data deletion #{mode}" unless File.exist?(data) == !expected
    persisted = JSON.parse(File.read(File.join(Lab::ROOT, record[:record])))
    raise "cleanup not persisted" unless persisted.fetch("ok") == expected
    File.unlink(File.join(Lab::ROOT, record[:record]))
    checks << mode
  end
end
# The actual requested boundary must reject a teardown exception, not expose
# the successful child process as a successful experiment.
lab = Lab.new
lab.define_singleton_method(:with_version) do |*args, **kwargs, &body|
  cleanup = { ok: false, record: "fake-cleanup-record" }
  @last_matrix_cleanup = cleanup
  raise Lab::MatrixCleanupError.new({ ok: true, exit_code: 0 }, cleanup, nil)
end
out = lab.send(:run_requested, "fake", "8.4.13", false)
raise "cleanup failure accepted or child exit lost" unless out[:ok] == false && out[:exit_code] == 0 && out[:cleanup][:ok] == false
puts JSON.generate(fake_docker_only: true, actual_teardown_checks: checks, cleanup_failure_propagated: true)
