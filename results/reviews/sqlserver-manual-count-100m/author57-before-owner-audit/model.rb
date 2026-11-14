# frozen_string_literal: true
require 'json'
require 'pathname'
module ManualModel
  GIB = 1024**3
  ROWS = 100_000_000
  CELLS = [['B-clustered','B','clustered',2], ['B-narrow','B','narrow',2], ['B-composite','B','composite',2],
           ['C-composite','C','composite',5], ['D-composite','D','composite',2], ['A-composite','A','composite',2]].freeze
  CONDITIONS = CELLS.map(&:first).freeze
  PLAIN_SQL = 'SELECT COUNT_BIG(*) AS exact_rows FROM [dbo].[Target];'
  D_SQL = 'SELECT COUNT_BIG(*) AS observed_rows FROM [dbo].[Target] WITH (NOLOCK);'
  def self.route_valid?(state, route, isolation, sql)
    return false unless isolation == (route == 'C' ? 5 : 2) && sql == (route == 'D' ? D_SQL : PLAIN_SQL)
    return false unless state['rcsi'] == (route == 'B' ? 1 : 0) && state['snapshot'] == (route == 'C' ? 1 : 0)
    return state['ag_primary'].nil? && state['local_role'].nil? if %w[B C D].include?(route)
    route == 'A' && state['ag_primary'] == 0 && state['local_role'] == 'SECONDARY' &&
      %w[ALL READ_ONLY].include?(state['read_allow']) && state['redo_queue'] == 0 && state['synchronized'] == true &&
      state['healthy'] == true && state['active_writes'] == 0 && state['secondary_rosetta'] == true
  end
  def self.cold_valid?(proof)
    proof.is_a?(Hash) && proof['verified'] == true && proof['cached_target_pages'] == 0 &&
      proof['dirty_target_pages'] == 0 && %w[CHECKPOINT_then_DBCC secondary_clean_proof_then_DBCC].include?(proof['method'])
  end
  LABEL = 'count.manual100m.owner'
  def self.accepted_sample?(sample)
    sample['completed'] == true && sample['completion_unknown'] == false && sample['error'].nil? &&
      sample['value'] == ROWS && sample['cold_buffer_reset_verified'] == true && cold_valid?(sample['cold_buffer_proof']) && sample['first_result_ns'].is_a?(Integer) && sample['first_result_ns'].positive? &&
      sample['full_completion_ns'].is_a?(Integer) && sample['full_completion_ns'] >= sample['first_result_ns']
  end
  def self.complete?(probe)
    samples = probe.fetch('samples', [])
    conditions = probe.fetch('conditions', []).map { |c| c['condition'] }
    receipts = probe.fetch('commits', [])
    conditions == CONDITIONS && samples.size == 18 &&
      CELLS.all? { |name, route, index, isolation|
        c = probe.fetch('conditions', []).find { |v| v['condition'] == name }
        r = c && c['actual_state']
        state = r && { 'rcsi' => r['is_read_committed_snapshot_on'], 'snapshot' => r['snapshot_isolation_state'],
          'ag_primary' => r['is_ag_primary'], 'local_role' => r['role_desc'], 'read_allow' => r['secondary_role_allow_connections_desc'],
          'redo_queue' => r['redo_queue_size'], 'synchronized' => r['synchronization_state_desc'] == 'SYNCHRONIZED',
          'healthy' => r['synchronization_health_desc'] == 'HEALTHY', 'active_writes' => r['active_user_writes'],
          'secondary_rosetta' => probe.dig('secondary_gate', 'secondary_rosetta_verified') }
        state && c['route'] == route && c['index'] == index && route_valid?(state, route, isolation, c['selected_sql']) && samples.select { |s| s['condition'] == name }.all? { |s|
        s['route'] == route && s['index'] == index && s.dig('id','Isolation') == isolation &&
        s['sql'] == (route == 'D' ? D_SQL : PLAIN_SQL) && s['precision'] ==
          (route == 'D' ? 'observed_rows_quiet_match_only' : 'exact_rows_static_fixture') } } &&
      probe.fetch('conditions', []).select { |c| c['index'] == 'composite' }.size == 4 &&
      probe.fetch('conditions', []).select { |c| c['index'] == 'composite' }.map { |c| c['structure_sha256'] }.uniq.size == 1 &&
      probe.fetch('conditions', []).all? { |c| /\A[0-9a-f]{64}\z/.match?(c.fetch('structure_sha256', '')) } &&
      probe['ag_setup'] == 'actual_secondary_readable_manual_full_log_restore_same_committed_fixture' &&
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
  def self.topology_stage!(base, owner, stage)
    stage_owned!(base, owner, stage)
    %w[primary secondary shared primary/data primary/log primary/secrets secondary/data secondary/log secondary/secrets].each do |part|
      path = File.join(stage, part)
      raise 'missing/foreign/symlink topology directory' unless File.directory?(path) && !File.symlink?(path) &&
        File.realpath(path) == path && File.stat(path).uid == Process.uid
    end
    true
  end
  def self.server_mounts!(server, stage, node, uid)
    raise 'invalid server node' unless %w[primary secondary].include?(node)
    expected = { '/var/opt/mssql' => File.join(stage, node), '/backup' => File.join(stage, 'shared') }
    mounts = server.fetch('Mounts')
    raise 'host binds missing/mismatched or extra volume' unless mounts.size == 2 && mounts.all? { |m|
      m['Type'] == 'bind' && m['RW'] == true && expected[m['Destination']] == m['Source'] }
    raise 'published server ports refused' unless server.dig('HostConfig', 'PortBindings').to_h.empty?
    raise 'incorrect arbitrary user' unless server.dig('Config', 'User') == "#{uid}:0" && uid != 0
    raise 'incorrect stable hostname' unless server.dig('Config', 'Hostname') == "count#{node}"
    raise 'HADR environment missing' unless server.dig('Config', 'Env').include?('MSSQL_ENABLE_HADR=1')
    raise 'server resource limits changed' unless server.dig('HostConfig','Memory') == 4 * GIB && server.dig('HostConfig','NanoCpus') == 3_000_000_000
    true
  end
  def self.secondary_gate!(gate, owner, id)
    raise 'secondary gate incomplete/foreign/unproven' unless gate['owner'] == owner && gate['secondary_container_id'] == id &&
      gate['secondary_rosetta_verified'] == true && gate['mounts_verified'] == true && gate['free_bytes'].is_a?(Integer) && gate['free_bytes'] >= 70 * GIB
    true
  end
  def self.terminal_server!(state, started)
    actual_started = started || (state['StartedAt'].is_a?(String) && !state['StartedAt'].start_with?('0001-') && !state['StartedAt'].empty?)
    raise 'server still running' if state['Running']
    raise 'server forced kill/OOM' if state['ExitCode'] == 137 || state['OOMKilled']
    raise 'nonzero server terminal exit' if actual_started && state['ExitCode'] != 0
    true
  end
  def self.owned!(kind, object, owner, id = nil)
    labels = kind == 'container' || kind == 'image' ? object.dig('Config', 'Labels') : object['Labels']
    raise 'refusing unowned resource' unless labels && labels[LABEL] == owner
    raise 'pinned resource ID changed' if id && object['Id'] != id
    true
  end
end
