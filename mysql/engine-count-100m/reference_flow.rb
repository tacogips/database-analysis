# frozen_string_literal: true
# Existing manual steps on an owned static fixture. No production setup or metadata guarantees.
module Engine100M
  class Controller
    def restart_client(phase, count: false, condition: nil)
      @client&.close(seconds: 10, aborting: false)
      @client = nil
      @client_phase = phase
      @client = Client.new(self, @stage[:engine])
      if count
        sql = "SET SESSION autocommit=1;SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED;SET SESSION lock_wait_timeout=3;SET SESSION max_execution_time=1200000;SELECT @@server_uuid,@@hostname,CONNECTION_ID(),@@session.transaction_isolation,@@innodb_page_size,@@session.information_schema_stats_expiry,CURRENT_TIMESTAMP(6);"
        response = @client.query(sql)
        fields = response[:lines].fetch(0).split("\t")
        raise 'count session is not RC' unless fields[3] == 'READ-COMMITTED'
        condition[:count_session] = { fresh_dedicated: true, sql: sql, raw: response[:lines], elapsed_ns: response[:elapsed_ns], server_uuid: fields[0], connection_id: Integer(fields[2]), isolation: fields[3], page_size: Integer(fields[4]), stats_expiry: fields[5], observed_at: fields[6] }
      end
      save
    end
    def reference_query(sql)
      r = @client.query(sql)
      { sql: sql, raw: r[:lines], elapsed_ns: r[:elapsed_ns], observed_at: Time.now.utc.iso8601(6) }
    end
    def capture_manual_reference(condition)
      result = { source_snapshot: 'results/reviews/mysql-engine-count-100m/manual-sources/mysql-innodb.md', scope: 'manual steps 1/3/4/5 and count only; no service/replica/cancel workload certification' }
      result[:inventory] = reference_query("SELECT TABLE_SCHEMA,TABLE_NAME,ENGINE,TABLE_ROWS,CURRENT_TIMESTAMP(6) AS observed_at FROM information_schema.TABLES WHERE TABLE_SCHEMA='lab' AND TABLE_TYPE='BASE TABLE' ORDER BY TABLE_NAME;")
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
