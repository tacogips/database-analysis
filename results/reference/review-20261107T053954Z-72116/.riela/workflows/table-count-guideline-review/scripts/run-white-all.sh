#!/bin/sh
set -eu
runner_dir=${0%/*}
exec mise exec -- ruby "$runner_dir/run-requested.rb"
