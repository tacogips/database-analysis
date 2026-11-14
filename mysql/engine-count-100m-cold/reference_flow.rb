# frozen_string_literal: true
# Existing manual steps on an owned static fixture. No production setup or metadata guarantees.
module Engine100MCold
  class Controller
    # Each attach process belongs to one boot, including the initial load boot.
    # A stop failure is terminal for the stage; only cleanup may force termination.
    def verify_boot_container(obj, running:)
      guard_stage
      raise 'container identity changed' unless obj['Id'] == @container_id
      raise 'image changed across boot' unless obj['Image'] == @result[:image]['Id']
      raise 'running state mismatch' unless obj.dig('State', 'Running') == running
      raise 'unexpected recovery/restart' unless obj.fetch('RestartCount', 0).zero? && !obj.dig('State', 'OOMKilled')
      raise 'container isolation changed' unless obj.dig('HostConfig', 'NetworkMode') == 'none' && obj.dig('HostConfig', 'Memory') == 4 * 1024**3 && obj.dig('HostConfig', 'NanoCpus') == 3_000_000_000 && obj.dig('Config', 'User') == "#{Process.uid}:#{Process.gid}" && obj.dig('HostConfig', 'PortBindings').to_h.empty?
      raise 'mount changed across boot' unless obj.fetch('Mounts').all? { |m| m['Type'] == 'tmpfs' || (m['Type'] == 'bind' && m['Source'] == @stage_path && m['Destination'] == '/labdata') } && obj.fetch('Mounts').any? { |m| m['Type'] == 'bind' && m['Source'] == @stage_path && m['Destination'] == '/labdata' }
      required = %w[--datadir=/labdata/data --tmpdir=/labdata/tmp --socket=/labdata/mysql.sock --skip-networking --innodb-buffer-pool-load-at-startup=OFF --innodb-buffer-pool-dump-at-shutdown=OFF --innodb-flush-method=O_DIRECT]
      raise 'startup cold options absent' unless (required - obj.dig('Config', 'Cmd').to_a).empty?
    end
    def start_boot(purpose)
      raise 'previous attach not cleanly reaped; refusing restart' if @server && !Engine100MCold.boot_terminal?(@boot)
      before = owned_inspect
      verify_boot_container(before, running: false)
      @boot_serial = (@boot_serial || 0) + 1
      @boot = { ordinal: @boot_serial, purpose: purpose, container_id: @container_id, owner_nonce: @owner,
                datadir: @stage_path, requested_at: Time.now.utc.iso8601(6), completion: 'incomplete', counts_started: 0,
                readiness_failures: [] }
      @stage[:boots] << @boot
      @server_ready = false
      started = Engine100MCold.ns
      @server = spawn_owned(@cli + ['start', '--attach', @container_id], "#{@stage[:engine]}-boot-#{@boot[:ordinal]}-server")
      @boot[:attach] = @server[1]
      save
      ready_deadline = limit(120)
      @boot_readiness_deadline = ready_deadline
      process_name = nil
      loop do
        tick(ready_deadline)
        raise 'server attach exited during readiness' unless reap(@server, nonblock: true).nil?
        process_name, code = docker(['exec', @container_id, 'cat', '/proc/1/comm'], 'readiness-pid1', seconds: 3, check: false, deadline: ready_deadline)
        if code == 0 && process_name.strip == 'mysqld'
          _, ping_code = docker(['exec', @container_id, 'mysqladmin', '--no-defaults', '--protocol=SOCKET', '--socket=/labdata/mysql.sock', '-uroot', "-p#{PASSWORD}", '--connect-timeout=1', 'ping'], 'readiness-ping', seconds: 3, check: false, deadline: ready_deadline)
          if ping_code != 0
            @boot[:readiness_failures] << { ping_exit_status: ping_code }
            sleep 1
            next
          end
          begin
            @client_phase = "readiness-boot-#{@boot[:ordinal]}"
            @client = open_client
            response = @client.query('SELECT 1;', seconds: 5, deadline: ready_deadline)
            raise 'invalid readiness scalar' unless response[:lines] == ['1']
            break
          rescue StandardError => e
            @boot[:readiness_failures] << "#{e.class}: #{e.message}"
            @client&.close(seconds: 2, aborting: true)
            @client = nil
            raise # A failed authenticated CLI after ready ping is fatal before any fixture load.
          end
        end
        sleep 1
      end
      @server_ready = true
      obj = owned_inspect
      verify_boot_container(obj, running: true)
      @boot[:container_started] = obj.fetch('State')
      raise 'missing actual server PID/start time' unless obj.dig('State', 'Pid').to_i.positive? && obj.dig('State', 'StartedAt').to_s.match?(/\A[12][0-9]{3}-/)
      previous = @stage[:boots][-2]
      raise 'server start time reused' if previous && previous.dig(:container_started, 'StartedAt') == obj.dig('State', 'StartedAt')
      @boot[:pid1] = process_name.strip
      @boot[:guest_processes] = docker(['top', @container_id, '-eo', 'pid,ppid,args'], 'boot-guest-processes', deadline: ready_deadline)[0]
      @boot[:identity] = reference_query('SELECT @@server_uuid,@@hostname,CONNECTION_ID(),CURRENT_TIMESTAMP(6);')
      fields = @boot[:identity][:raw].fetch(0).split("\t")
      raise 'missing boot UUID' unless fields[0].match?(/\A[0-9a-f-]{36}\z/)
      @boot[:server_uuid] = fields[0]
      raise 'same datadir UUID changed' if previous && previous[:server_uuid] != @boot[:server_uuid]
      @boot[:uptime] = reference_query("SHOW GLOBAL STATUS LIKE 'Uptime';")
      raise 'missing uptime' unless @boot[:uptime][:raw].one? && @boot[:uptime][:raw][0].match?(/\AUptime\t[0-9]+\z/)
      @boot[:settings] = reference_query("SHOW GLOBAL VARIABLES WHERE Variable_name IN ('version','version_compile_machine','innodb_buffer_pool_size','key_buffer_size','innodb_redo_log_capacity','innodb_buffer_pool_load_at_startup','innodb_buffer_pool_dump_at_shutdown','innodb_flush_method','innodb_fast_shutdown','skip_networking','log_bin','socket','datadir','tmpdir','pid_file');")
      actual = @boot[:settings][:raw].to_h { |line| line.split("\t", 2) }
      raise 'actual boot globals mismatch' unless Engine100MCold.cold_settings?(actual)
      @boot[:cold_globals_verified] = true
      @boot[:completion] = 'healthy_verified'
      @client.close(seconds: 10, aborting: false)
      @client = nil
      tick(ready_deadline)
      @boot[:start_readiness_elapsed_ns] = Engine100MCold.ns - started
      @boot[:ready_at] = Time.now.utc.iso8601(6)
      @boot_readiness_deadline = nil
      save
    rescue Exception => e
      @boot_readiness_deadline = nil
      @boot[:failure] = "#{e.class}: #{e.message}" if @boot
      save
      raise
    end
    def stop_boot(reason)
      raise 'no current owned boot to stop' unless @boot && @server
      @client&.close(seconds: 10, aborting: false)
      @client = nil
      # Every owned CLI must have reached its actual terminal status before stopping.
      clients = @processes.select { |p| p[:tag].end_with?('-mysql-client') }
      raise 'client not zero/reaped before reset' unless clients.all? { |p| p[:reaped] && p[:exit_status] == 0 && !p[:forced_termination] }
      @boot[:stop] = { reason: reason, requested_at: Time.now.utc.iso8601(6), completion: 'incomplete' }
      stop_start = Engine100MCold.ns
      obj = owned_inspect
      verify_boot_container(obj, running: true)
      raise 'server died before orderly stop' unless reap(@server, nonblock: true).nil?
      @server_ready = false
      _, code = docker(['stop', '--time', '25', @container_id], 'cold-stop-owned', seconds: 30, check: false, monitor: false)
      @boot[:stop][:command_exit_status] = code
      obj = owned_inspect
      @boot[:container_final_state] = obj.fetch('State')
      raise 'nonzero/unclean stop; refusing restart' unless code == 0 && !obj.dig('State', 'Running') && obj.dig('State', 'ExitCode') == 0 && !obj.dig('State', 'OOMKilled')
      stop = limit(5)
      while reap(@server, nonblock: true).nil?
        raise 'attach not reaped; refusing restart' if Engine100MCold.ns >= stop
        sleep 0.05
      end
      raise 'nonzero attach; refusing restart' unless @server[1][:exit_status] == 0 && !@server[1][:forced_termination]
      @boot[:stop].merge!(completion: 'clean_stopped_reaped', elapsed_ns: Engine100MCold.ns - stop_start, finished_at: Time.now.utc.iso8601(6))
      save
      raise 'unclean terminal boot; refusing restart' unless Engine100MCold.boot_terminal?(@boot)
    rescue Exception => e
      @boot[:stop_failure] = "#{e.class}: #{e.message}" if @boot
      save
      raise
    end
    def reset_before_count(sample)
      sample[:reset] = { status: 'incomplete', previous_boot_ordinal: @boot&.dig(:ordinal), same_container_id: @container_id, same_datadir: @stage_path, owner_nonce: @owner }
      save
      start = Engine100MCold.ns
      stop_boot('before-each-count')
      sample[:reset][:previous_boot_terminal] = Engine100MCold.boot_terminal?(@boot)
      start_boot('count')
      sample[:boot_ordinal] = @boot[:ordinal]
      sample[:boot_identity] = { ordinal: @boot[:ordinal], server_uuid: @boot[:server_uuid], container_id: @container_id, actual_pid: @boot.dig(:container_started, 'Pid'), actual_started_at: @boot.dig(:container_started, 'StartedAt'), uptime: @boot[:uptime] }
      sample[:reset].merge!(status: 'verified', boot_ordinal: @boot[:ordinal], elapsed_ns: Engine100MCold.ns - start)
      raise 'count boot already used' unless @boot[:counts_started] == 0
      save
    end
    def buffer_snapshot
      return { status: 'not_applicable_MyISAM', reason: 'MyISAM key cache is process-local; restarted process plus Key_* counters are the reference' } unless @stage[:engine] == 'InnoDB'
      # This COUNT groups buffer metadata only. It never scans count_target rows.
      r = reference_query("SELECT INDEX_NAME,PAGE_TYPE,COUNT(*) FROM information_schema.INNODB_BUFFER_PAGE WHERE TABLE_NAME='`lab`.`count_target`' GROUP BY INDEX_NAME,PAGE_TYPE ORDER BY INDEX_NAME,PAGE_TYPE;")
      groups = r[:raw].map do |line|
        name, type, pages = line.split("\t", -1)
        { index_name: name, page_type: type, pages: Integer(pages) }
      end
      raise 'invalid buffer metadata' unless groups.all? { |g| g[:pages] >= 0 }
      r.merge(status: 'observed_metadata_not_zero_page_claim', groups: groups, target_cached_pages: groups.sum { |g| g[:pages] })
    end
    def assess_cold_sample(sample)
      raise 'boot not fresh verified' unless @boot[:purpose] == 'count' && @boot[:completion] == 'healthy_verified' && @boot[:cold_globals_verified] && @boot[:counts_started] == 0
      session = sample.fetch(:count_session)
      raise 'count session boot mismatch' unless session[:boot_ordinal] == @boot[:ordinal] && session[:server_uuid] == @boot[:server_uuid]
      snapshots = [sample[:buffer_at_start], sample[:buffer_before_count]]
      if @stage[:engine] == 'InnoDB'
        size = sample[:manual_reference][:index_page_stats][:size_pages]
        key = sample[:manual_reference][:selected_index]
        snapshots.each do |snapshot|
          pages = snapshot.fetch(:target_cached_pages)
          selected = snapshot[:groups].select { |g| g[:index_name] == key }.sum { |g| g[:pages] }
          # Fixed uncompressed fixture: even nominal stored payload cannot all fit if this bound is lower.
          raise 'target fully resident unexpectedly; cold condition unproven' if pages * session[:page_size] >= ROWS * 404 || (size && size.positive? && selected >= size)
        end
      end
      sample[:target_page_change_during_prechecks] = snapshots.last[:target_cached_pages] - snapshots.first[:target_cached_pages] if @stage[:engine] == 'InnoDB'
      sample[:cache_scope] = 'fresh DB process, prechecks/background may touch metadata pages; OS/VM/host/device caches unknown'
      sample[:cold_buffer_policy] = 'verified_fresh_process_preload_off_metadata_pages_disclosed'
      @boot[:counts_started] += 1
      save
    end
    def open_client = Client.new(self, @stage[:engine])
    def restart_client(phase, count: false, condition: nil)
      @client&.close(seconds: 10, aborting: false)
      @client = nil
      @client_phase = phase
      @client = open_client
      if count
        sql = "SET SESSION autocommit=1;SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED;SET SESSION lock_wait_timeout=3;SET SESSION max_execution_time=1200000;SELECT @@server_uuid,@@hostname,CONNECTION_ID(),@@session.transaction_isolation,@@innodb_page_size,@@session.information_schema_stats_expiry,CURRENT_TIMESTAMP(6),@@session.autocommit,@@session.lock_wait_timeout,@@session.max_execution_time;"
        response = @client.query(sql)
        fields = response[:lines].fetch(0).split("\t")
        raise 'count session flags mismatch' unless fields[3] == 'READ-COMMITTED' && fields[7..9] == %w[1 3 1200000]
        condition[:count_session] = { fresh_dedicated: true, boot_ordinal: @boot[:ordinal], process: @client.pair[1], identity: [@boot[:ordinal], fields[0], Integer(fields[2])], sql: sql, raw: response[:lines], elapsed_ns: response[:elapsed_ns], server_uuid: fields[0], connection_id: Integer(fields[2]), isolation: fields[3], page_size: Integer(fields[4]), stats_expiry: fields[5], observed_at: fields[6] }
      end
      save
    end
    def reference_query(sql)
      r = @client.query(sql, deadline: @boot_readiness_deadline)
      { sql: sql, raw: r[:lines], elapsed_ns: r[:elapsed_ns], observed_at: Time.now.utc.iso8601(6) }
    end
    def table_rows_reference
      response = @client.query(TABLE_ROWS_SQL, scalar: true)
      raise 'TABLE_ROWS scalar shape mismatch' unless response[:lines].one?
      raw = response[:lines].first
      value = raw == 'NULL' ? nil : Integer(raw, 10)
      raise 'negative TABLE_ROWS' if value && value.negative?
      raise 'invalid TABLE_ROWS timing' unless response[:scalar_elapsed_ns].is_a?(Integer) && response[:scalar_elapsed_ns].positive? && response[:elapsed_ns] >= response[:scalar_elapsed_ns]
      { sql: TABLE_ROWS_SQL, raw: response[:lines], value: value,
        status: value.nil? ? 'unknown_not_zero' : (value.zero? ? 'observed_zero_reference_not_empty_proof' : 'reference_statistic'),
        caller_elapsed_ns: response[:scalar_elapsed_ns], full_request_elapsed_ns: response[:elapsed_ns],
        observed_at: Time.now.utc.iso8601(6), signed_error_pct: value.nil? ? nil : 100.0 * (value - ROWS) / ROWS,
        truth_rows: ROWS, meaning: 'One-table existing metadata; not exact count or an error guarantee for other tables. Same scalar-arrival timer semantics as COUNT.' }
    end
    def capture_manual_reference(condition)
      result = { source_snapshot: 'results/reviews/mysql-engine-count-100m-cold/manual-sources/mysql-innodb.md', scope: 'manual steps 1/3/4/5 and count only; no service/replica/cancel workload certification' }
      result[:inventory] = reference_query("SELECT TABLE_SCHEMA,TABLE_NAME,ENGINE,TABLE_ROWS,CURRENT_TIMESTAMP(6) AS observed_at FROM information_schema.TABLES WHERE TABLE_SCHEMA='lab' AND TABLE_TYPE='BASE TABLE' ORDER BY TABLE_NAME;")
      result[:table_rows] = table_rows_reference
      result[:explain] = reference_query('EXPLAIN FORMAT=TRADITIONAL ' + COUNT_SQL)
      raise 'missing count EXPLAIN' if result[:explain][:raw].empty?
      f = result[:explain][:raw].first.split("\t", -1)
      result[:selected_index] = f[6] == 'NULL' ? nil : f[6]
      result[:estimated_rows_raw] = f[9]
      result[:index_catalog] = condition[:indexes]
      result[:page_size_bytes] = condition.fetch(:count_session).fetch(:page_size)
      if @stage[:engine] != 'InnoDB'
        result[:index_page_stats] = { status: 'not_applicable_non_InnoDB', size_pages: nil, leaf_pages: nil }
      elsif result[:selected_index].nil?
        result[:index_page_stats] = { status: 'unavailable_index_not_identified', size_pages: nil, leaf_pages: nil }
      else
        key = result[:selected_index]
        raise 'unexpected fixture index identifier' unless %w[PRIMARY k_count k_count_v2].include?(key)
        sql = "SELECT index_name,stat_name,stat_value,last_update FROM mysql.innodb_index_stats WHERE database_name='lab' AND table_name='count_target' AND index_name='#{key}' AND stat_name IN ('size','n_leaf_pages') ORDER BY stat_name;"
        begin
          rows = reference_query(sql)
          parsed = rows[:raw].to_h do |line|
            v = line.split("\t", -1)
            [v[1], { value: Integer(v[2]), last_update: v[3] }]
          end
          good = %w[size n_leaf_pages].all? { |n| parsed[n] && parsed[n][:value].positive? }
          result[:index_page_stats] = rows.merge(status: good ? 'available_reference_statistics' : 'unavailable_or_uninitialized', parsed: parsed,
            size_pages: good ? parsed['size'][:value] : nil, leaf_pages: good ? parsed['n_leaf_pages'][:value] : nil,
            size_bytes_from_stats: good ? parsed['size'][:value] * result[:page_size_bytes] : nil,
            meaning: 'persisted metadata, not physical IO bytes, safety threshold, exact scan pages, or latency bound')
        rescue Exception => e
          # An actual SQL error terminates mysql batch mode: it cannot silently become a successful count session.
          result[:index_page_stats] = { status: 'unavailable', error: "#{e.class}: #{e.message}" }
          condition[:manual_reference] = result
          save
          raise
        end
      end
      result
    end
  end
end
