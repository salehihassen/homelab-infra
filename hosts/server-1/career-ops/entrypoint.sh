#!/bin/sh
set -eu
umask 077
if [ "${1:-}" = init ]; then
    # git clone refuses a nonempty destination. Never seed over user data.
    git clone --no-hardlinks /app /workspace
    git -C /workspace remote set-url origin https://github.com/career-ops-hq/career-ops.git
    ln -s /app/node_modules /workspace/node_modules
    ln -s /app/web/node_modules /workspace/web/node_modules
    ln -s /app/web/.next /workspace/web/.next
    mkdir -p /home/node/.codex
    exit 0
fi
expected="$(git -C /app rev-parse HEAD)"
actual="$(git -C /workspace rev-parse HEAD)"
if [ "$expected" != "$actual" ]; then
    echo 'Career Ops workspace/image revision mismatch; follow README upgrade/rollback steps.' >&2
    exit 1
fi
for pair in 'node_modules:/app/node_modules' 'web/node_modules:/app/web/node_modules' 'web/.next:/app/web/.next'; do
    link="${pair%%:*}"
    target="${pair#*:}"
    if [ "$(readlink "/workspace/$link")" != "$target" ]; then
        echo "Career Ops requires the image dependency/build link: $link" >&2
        exit 1
    fi
done
exec "$@"
