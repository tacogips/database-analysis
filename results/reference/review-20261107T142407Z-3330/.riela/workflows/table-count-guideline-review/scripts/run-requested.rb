#!/usr/bin/env ruby
# frozen_string_literal: true
require "json"
require "open3"
require "fileutils"
require "digest"
require "time"
require "rbconfig"

root = File.expand_path("../../../..", __dir__)
source_files = lambda do
  (Dir[File.join(root, "bench", "**", "*.{py,rb,json,retired,versions,encrypted,outputs}")] +
   Dir[File.join(root, "bench", "keyring", "*")] + Dir[File.join(root, "sql", "*.sql")] +
   %w[lab.rb compose.yaml mysql-images.json mise.toml bench/Dockerfile
      .riela/workflows/table-count-guideline-review/scripts/run-requested.rb
      .riela/workflows/table-count-guideline-review/scripts/run-white-all.sh].map { |p| File.join(root, p) })
    .select { |p| File.file?(p) }.uniq.sort
end
fingerprints = lambda do
  source_files.call.to_h { |file| [file.delete_prefix(root + "/"), Digest::SHA256.file(file).hexdigest] }
end
reviews = File.join(root, "results", "reviews")
FileUtils.mkdir_p(reviews)
log_path = File.join(reviews, "final-all-run.log") # reset preserves reviews, including this open log
candidate_path = File.join(reviews, "final-all-candidate.json")
proof_path = File.join(reviews, "final-all-proof.json")
archive_stamp = "#{Time.now.utc.strftime('%Y%m%dT%H%M%SZ')}-#{Process.pid}"
# Retain the previous complete attempt before replacing the working paths.
[candidate_path, log_path].each do |path|
  FileUtils.cp(path, File.join(reviews, "previous-#{archive_stamp}-#{File.basename(path)}")) if File.file?(path)
end
if File.file?(proof_path)
  FileUtils.mv(proof_path, File.join(reviews, "previous-final-all-proof-#{Time.now.utc.strftime('%Y%m%dT%H%M%SZ')}-#{Process.pid}.json"))
end
command = ["mise", "run", "all"]
summary_path = File.join(root, "results", "requested", "last_run.json")
signature = lambda do |path|
  next nil unless File.file?(path)
  stat = File.stat(path)
  { sha256: Digest::SHA256.file(path).hexdigest, bytes: stat.size, inode: stat.ino,
    mtime: [stat.mtime.to_i, stat.mtime.nsec], ctime: [stat.ctime.to_i, stat.ctime.nsec] }
end
summary_before = signature.call(summary_path)
candidate = { status: "running", meaning: "Process evidence only, never final acceptance. Owner must audit semantic completeness and unchanged final benchmark source before creating final-all-proof.json.",
              startedAt: Time.now.utc.iso8601, command: command, aggregateBudgetMinutes: 0,
              controllerRuby: RUBY_VERSION, controllerRubyPath: RbConfig.ruby, requestedSummaryBefore: summary_before,
              log: "results/reviews/final-all-run.log", measurementSourceBefore: fingerprints.call }
File.write(candidate_path, JSON.pretty_generate(candidate))
begin
  File.open(log_path, "w") do |log|
    log.puts("controller Ruby #{RUBY_VERSION} #{RbConfig.ruby}; cwd=#{root}; command=#{command.join(' ')}")
    log.flush
    Open3.popen2e({ "LAB_REQUESTED_BUDGET_MINUTES" => "0" }, *command, chdir: root) do |stdin, output, wait|
      stdin.close
      output.each_line { |line| log.write(line); log.flush }
      status = wait.value
      candidate[:exitCode], candidate[:signal] = status.exitstatus, status.termsig
      candidate[:processSucceeded] = status.success?
    end
  end
rescue StandardError => error
  candidate[:processSucceeded] = false
  candidate[:runnerError] = "#{error.class}: #{error.message}"
end
candidate[:finishedAt] = Time.now.utc.iso8601
candidate[:measurementSourceAfter] = fingerprints.call
candidate[:measurementSourceUnchanged] = candidate[:measurementSourceBefore] == candidate[:measurementSourceAfter]
candidate[:status] = candidate[:processSucceeded] && candidate[:measurementSourceUnchanged] ? "process_completed_pending_owner_audit" : "failed"
candidate[:logSha256] = Digest::SHA256.file(log_path).hexdigest if File.file?(log_path)
summary_after = signature.call(summary_path)
candidate[:requestedSummaryAfter] = summary_after
candidate[:requestedSummaryFresh] = !summary_after.nil? && summary_after != summary_before
summary = begin
  candidate[:requestedSummaryFresh] ? JSON.parse(File.read(summary_path)) :
    { "status" => "not_reached", "ran" => [], "lab_data_intact" => false,
      "reason" => "No new requested summary from this attempt; any pre-existing summary is uncredited." }
rescue JSON::ParserError => error
  { "status" => "invalid", "ran" => [], "lab_data_intact" => false, "parseError" => error.message }
end
candidate[:requestedStatus] = summary["status"]
candidate[:status] = "failed" unless summary["status"] == "completed" && summary["lab_data_intact"]
File.write(candidate_path, JSON.pretty_generate(candidate))
# Preserve all-run evidence before the integrate step edits only documentation.
snapshot = "results/reference/review-#{Time.now.utc.strftime('%Y%m%dT%H%M%SZ')}-#{Process.pid}"
manifest = { meaning: "Collection after a white-all attempt, including partial or pre-run files on early failure. Presence is not proof of production in this attempt. Not acceptance: owner must inspect process exit, fresh summary, source stability, required scope and semantic results. Historical reference/reviews were retained but not used as DB inputs.",
             collectionMayIncludePreRunFiles: true, requestedSummaryFresh: candidate[:requestedSummaryFresh],
             runnerSummary: summary, whiteAllCandidate: candidate, files: {} }
files = Dir[File.join(root, "results", "*.{json,log,md,txt}")] + Dir[File.join(root, "results", "requested", "*.{json,log}")] +
        Dir[File.join(root, "results", "matrix_cleanup", "*.json")] +
        Dir[File.join(root, "results", "raw", "**", "*")] +
        source_files.call + [log_path, candidate_path]
files.select { |file| File.file?(file) }.uniq.each do |file|
  relative = file.delete_prefix(root + "/")
  destination = File.join(root,snapshot,relative)
  FileUtils.mkdir_p(File.dirname(destination))
  FileUtils.cp(file,destination)
  manifest[:files][relative] = Digest::SHA256.file(destination).hexdigest
end
File.write(File.join(root,snapshot,"manifest.json"),JSON.pretty_generate(manifest))
puts JSON.generate(when: { always: true }, payload: { **summary.transform_keys(&:to_sym), requestedStatus: summary["status"],
                   status: candidate[:status], runnerExitOk: candidate[:status] == "process_completed_pending_owner_audit",
                   runnerExitCode: candidate[:exitCode], runnerLog: candidate[:log], measurementSnapshot: snapshot,
                   whiteAllCandidatePath: "results/reviews/final-all-candidate.json", measurementSourceUnchanged: candidate[:measurementSourceUnchanged] })
