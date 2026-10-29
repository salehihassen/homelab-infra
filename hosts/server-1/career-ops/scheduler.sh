#!/bin/sh
set -eu
umask 077
# Each invocation processes at most one due job. Upstream's resource/store locks
# also serialize manual Web UI invocations against this worker.
while :; do
    touch /tmp/scheduler-heartbeat
    if node /workspace/web/scripts/scheduled-jobs-runner.mjs; then
        echo 'career-ops scheduler: tick complete'
    else
        echo 'career-ops scheduler: tick failed; inspect scheduled scan history' >&2
    fi
    touch /tmp/scheduler-heartbeat
    sleep 60 &
    wait "$!"
done
