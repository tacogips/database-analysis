# frozen_string_literal: true
require 'json'
require 'pathname'
module ManualModel
  GIB = 1024**3
  ROWS = 100_000_000
  CONDITIONS = %w[clustered narrow composite].freeze
  LABEL = 'count.manual100m.owner'
  def self.accepted_sample?(sample)
    sample['completed'] == true && sample['completion_unknown'] == false && sample['error'].nil? &&
      sample['value'] == ROWS && sample['cold_buffer_reset_verified'] == true && sample['first_result_ns'].is_a?(Integer) && sample['first_result_ns'].positive? &&
      sample['full_completion_ns'].is_a?(Integer) && sample['full_completion_ns'] >= sample['first_result_ns']
  end
  def self.complete?(probe)
    samples = probe.fetch('samples', [])
    conditions = probe.fetch('conditions', []).map { |c| c['condition'] }
    receipts = probe.fetch('commits', [])
    conditions == CONDITIONS && samples.size == 9 &&
      CONDITIONS.all? { |c| samples.select { |s| s['condition'] == c }.map { |s| s['sample'] } == [1, 2, 3] } &&
      samples.all? { |s| accepted_sample?(s) } && receipts.size == 1000 &&
      receipts.each_with_index.all? do |r, i|
        raw = r.dig('raw', 0, 0)
        r['batch'] == i && r['committed_total'] == (i + 1) * 100_000 && raw &&
          raw['committed_rows'] == 100_000 && raw['first_id'] == i * 100_000 + 1 &&
          raw['last_id'] == (i + 1) * 100_000 && raw['transaction_count'] == 0
      end
  end
  def self.local_endpoint!(endpoint)
    require 'uri'
    u = URI.parse(endpoint)
    raise 'only local Unix Docker endpoint permitted' unless u.scheme == 'unix' &&
      (u.host.nil? || u.host.empty?) && u.path.start_with?('/') && !u.query && !u.fragment
    u.path
  end
  def self.stage_owned!(base, owner, stage)
    raise 'invalid owner nonce' unless /\A[0-9a-f]{32}\z/.match?(owner)
    base = File.realpath(base)
    directory = File.join(base, owner)
    expected = File.join(directory, 'stage')
    raise 'stage not exact owned path' unless stage == expected
    [directory, stage, File.join(directory, 'owner.json')].each do |p|
      raise 'symlink ownership refusal' if File.symlink?(p)
    end
    raise 'owner directory mismatch' unless File.realpath(directory) == directory
    raise 'stage realpath mismatch' unless File.realpath(stage) == expected
    marker = JSON.parse(File.read(File.join(directory, 'owner.json')))
    raise 'marker owner mismatch' unless marker == { 'owner' => owner, 'stage' => expected, 'uid' => Process.uid }
    raise 'owner uid mismatch' unless File.stat(directory).uid == Process.uid && File.stat(stage).uid == Process.uid
    expected
  end
  def self.owned!(kind, object, owner, id = nil)
    labels = kind == 'container' || kind == 'image' ? object.dig('Config', 'Labels') : object['Labels']
    raise 'refusing unowned resource' unless labels && labels[LABEL] == owner
    raise 'pinned resource ID changed' if id && object['Id'] != id
    true
  end
end
