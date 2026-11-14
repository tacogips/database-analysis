# Actual default Lab dispatch and requested loop in an isolated tree. No DB/Docker.
require 'tmpdir'
require 'fileutils'
require 'json'
require 'stringio'

source = File.expand_path('..', __dir__)
original_out, original_err = $stdout, $stderr
$stdout, $stderr = StringIO.new, StringIO.new
report = {}
Dir.mktmpdir('nonlocking-runner-') do |dir|
  %w[lab.rb mysql-images.json].each { |p| FileUtils.cp(File.join(source,p),dir) }
  FileUtils.mkdir_p(File.join(dir,'bench'))
  FileUtils.cp(File.join(source,'bench/nonlocking-scope.json'),File.join(dir,'bench/nonlocking-scope.json'))
  FileUtils.cp_r(File.join(source,'bench/requested'),File.join(dir,'bench/requested'))
  require File.join(dir,'lab')
  lab = Lab.new
  scope = JSON.parse(File.read(File.join(dir,'bench/nonlocking-scope.json')))
  calls = []
  lab.define_singleton_method(:bench) { |*args, **kw| calls << [args,kw] }
  lab.experiments
  raise 'default experiment dispatch widened' unless calls == [[['experiments.py','run',*scope.fetch('baseImpactNames')],{log:'experiments'}]]
  # All/measure still dispatch the same preserved stages, including both integrity checks.
  raise 'all stages changed' unless Lab::PIPELINES.fetch('all') == scope.fetch('stages')
  raise 'initial integrity lost' unless Lab::PIPELINES.fetch('all').index('verify') < Lab::PIPELINES.fetch('all').index('count_shapes')
  raise 'measure selection bypassed' unless %w[experiments requested].all? { |s| Lab::PIPELINES.fetch('measure').include?(s) }
  raise 'matrix changed' unless Lab::MATRIX == scope.fetch('matrixVersions')
  raise 'pinned matrix images changed' unless Lab::MYSQL_IMAGES.keys.sort == Lab::MATRIX.sort && Lab::MYSQL_IMAGES.values.all? { |v| /@sha256:[0-9a-f]{64}\z/.match?(v) }
  status = Struct.new(:exitstatus) { def success? = exitstatus == 0 }
  captures = []
  lab.define_singleton_method(:capture) { |*args| captures << args; ["sha256:#{'a'*64}\nsha256:#{'b'*64}\n",status.new(0)] }
  lab.define_singleton_method(:dependency_hashes) { |paths| paths.to_h { |p| [File.basename(p,'.py'),{}] } }
  lab.define_singleton_method(:input_fingerprint) { |*args| 'synthetic' }
  snapshot = {}
  lab.define_singleton_method(:measurement_snapshot) { snapshot.dup }
  lab.define_singleton_method(:artifact_manifest) { |path| {path:path,sha256:'synthetic',bytes:1} }
  lab.define_singleton_method(:raw_artifact_references) { |path| [] }
  runs = []
  lab.define_singleton_method(:run_requested) do |id,version,encrypted|
    runs << [id,version,encrypted]
    output = File.join(dir,"bench/requested/#{id}.outputs")
    (File.file?(output) ? File.read(output).split : []).each { |p| snapshot[p] = runs.length }
    {ok:true,exit_code:0,version:version}
  end
  verifies = 0
  lab.define_singleton_method(:verify) { verifies += 1 }
  lab.requested # actual no-argument default, not a reconstruction of its selection
  expected = scope.fetch('requestedScripts').flat_map { |s| s.fetch('versions').map { |v| [s.fetch('id'),v,s.fetch('encrypted')] } }
  raise 'default requested/version/encryption dispatch differs' unless runs == expected
  summary_path = File.join(dir,'results/requested/last_run.json')
  summary = JSON.parse(File.read(summary_path))
  raise 'final integrity lost' unless verifies == 1 && summary['lab_data_intact']
  raise 'scope provenance lost' unless summary['scope_sha256'] == Digest::SHA256.file(File.join(dir,'bench/nonlocking-scope.json')).hexdigest
  raise 'scope skip inventory credited/missing' unless summary['skipped_by_user_scope'] == scope['skippedRequestedScripts'] && summary['ran'].map { |x| x['id'] } == scope['requestedScripts'].map { |x| x['id'] }
  raise 'required outputs lost' unless summary['ran'].all? { |r| r['missing_outputs'].empty? }
  # Every optional historical ID remains reachable through the real selector.
  scope.fetch('skippedRequestedScripts').each do |s|
    raise 'optional ID unreachable' unless lab.send(:requested_paths,[s['id']]).map { |p| File.basename(p,'.py') } == [s['id']]
  end
  optional = scope.fetch('skippedRequestedScripts').first.fetch('id')
  runs.clear
  lab.requested([optional])
  raise 'explicit ID widened' unless runs.map(&:first).uniq == [optional]
  raise 'explicit optional ID still marked skipped' if JSON.parse(File.read(summary_path))['skipped_by_user_scope'].any? { |s| s['id'] == optional }
  before = [File.read(summary_path),captures.length,runs.dup]
  reject = lambda do |ids|
    begin
      lab.requested(ids)
    rescue SystemExit => e
      raise 'successful rejection exit' if e.success?
      raise 'unknown ID reached side effects' unless [File.read(summary_path),captures.length,runs] == before
      next
    end
    raise 'unknown ID accepted'
  end
  reject.call(['unknown-requested-id'])
  reject.call([optional,'unknown-requested-id'])
  scope['requestedScripts'][0]['id'] = 'typo-in-default'
  File.write(File.join(dir,'bench/nonlocking-scope.json'),JSON.generate(scope))
  reject.call([])
  scope['requestedScripts'] = []
  File.write(File.join(dir,'bench/nonlocking-scope.json'),JSON.generate(scope))
  reject.call([])
  report = {db_access:false,actual_default_experiment_dispatch:true,actual_default_requested_loop:true,
            selectedImpacts:calls[0][0].drop(2),selectedRequestedProcesses:expected.length,
            versions_encryption_outputs_verified:true,all_optional_ids_reachable:true,explicit_id_semantics:true,
            unknown_and_empty_default_fail_closed_before_io:true,scope_hash_and_skip_inventory:true,final_integrity:true}
end
$stdout, $stderr = original_out, original_err
puts JSON.generate(report)
