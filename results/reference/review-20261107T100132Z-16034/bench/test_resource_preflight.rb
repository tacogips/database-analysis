# Actual preflight and requested fail-fast boundary; isolated tree/fake Docker.
require 'tmpdir'
require 'fileutils'
require 'json'
require 'stringio'
original_out, original_err = $stdout, $stderr
$stdout, $stderr = StringIO.new, StringIO.new
source = File.expand_path('..', __dir__)
Dir.mktmpdir('resource-preflight-') do |dir|
  %w[lab.rb mysql-images.json].each { |p| FileUtils.cp(File.join(source,p),dir) }
  require File.join(dir,'lab')
  status = Struct.new(:exitstatus) { def success? = exitstatus == 0 }
  lab=Lab.new
  calls=[]
  lab.define_singleton_method(:capture) do |*cmd|
    calls << cmd
    if cmd[1]=='image'
      ["sha256:#{'a'*64}\n",status.new(0)]
    else
      ["Filesystem 1024-blocks Used Available Capacity Mounted on\nvm 9000000 6000000 #{vm_free} 90% /\nhost 150000000 1000000 140000000 1% /lab-host\n",status.new(0)]
    end
  end
  lab.define_singleton_method(:run) { |*cmd| raise 'unexpected mutation/pull of present image' }
  lab.define_singleton_method(:vm_free) { @vm_free || 3000000 }
  lab.prepare_images
  raise 'all5 image identities not inspected' unless calls.count { |c| c.include?('{{.Id}}') }==5
  lab.resource_preflight
  raise 'pass not saved' unless JSON.parse(File.read(File.join(dir,'results/resource_preflight.json')))['ok']
  lab.instance_variable_set(:@vm_free,1)
  begin
    lab.resource_preflight
    raise 'low space accepted'
  rescue SystemExit
    raise 'failure not saved' if JSON.parse(File.read(File.join(dir,'results/resource_preflight.json')))['ok']
  end
  FileUtils.mkdir_p(File.join(dir,'bench/requested'))
  %w[a b c].each { |id| File.write(File.join(dir,"bench/requested/#{id}.py"),'# fake') }
  lab.define_singleton_method(:capture) { |*a| ["sha256:#{'a'*64}\nsha256:#{'b'*64}\n",status.new(0)] }
  lab.define_singleton_method(:dependency_hashes) { |paths| paths.to_h { |p| [File.basename(p,'.py'),{}] } }
  lab.define_singleton_method(:input_fingerprint) { |*a| 'fake' }
  lab.define_singleton_method(:measurement_snapshot) { {} }
  lab.define_singleton_method(:versions_for) { |*a| [nil] }
  lab.define_singleton_method(:raw_artifact_references) { |*a| [] }
  lab.define_singleton_method(:artifact_manifest) { |*a| nil }
  ran=[]
  lab.define_singleton_method(:run_requested) { |id,*a| ran << id; {ok:false,exit_code:1} }
  lab.define_singleton_method(:verify) { raise 'verification started after unknown cleanup' }
  begin
    lab.requested
    raise 'failed request accepted'
  rescue SystemExit
    summary=JSON.parse(File.read(File.join(dir,'results/requested/last_run.json')))
    raise 'later experiment started' unless ran==['a']
    raise 'missing unstarted opportunity records' unless summary['not_run'].map { |r| r['id'] }==%w[b c]
    raise 'failed summary lost' unless summary['status']=='failed' && summary['lab_data_intact']==false
  end
end
$stdout, $stderr = original_out, original_err
puts JSON.generate(db_access:false,all_images_inspected:true,space_pass_and_failure:true,failed_run_blocks_later_experiments:true,all_unstarted_retained:true)
