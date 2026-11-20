#!/bin/sh
# Grafana entrypoint: load the Personal Ping Me Discord settings into this
# process's environment, then start Grafana. Alert provisioning references
# them as ${PING_ME_*}; the values stay out of Compose, tracked files and
# `docker inspect`. Fails closed so a broken file stops Grafana visibly
# instead of provisioning an empty token.
set -eu

config=/run/secrets/ping_me_config

fail() {
	echo "ping-me-entrypoint: $1" >&2
	exit 1
}

[ -r "$config" ] || fail "cannot read $config (check the ai-agents group_add)"

# The file is a flat JSON object of string fields written by ping_me.py.
field() {
	sed -n "s/.*\"$1\"[[:space:]]*:[[:space:]]*\"\([^\"\\\\]*\)\".*/\1/p" "$config" | head -n 1
}

PING_ME_BOT_TOKEN="$(field bot_token)"
PING_ME_CHANNEL_ID="$(field channel_id)"
PING_ME_USER_ID="$(field user_id)"

case "$PING_ME_BOT_TOKEN" in
'' | *[!A-Za-z0-9._-]*) fail "bot_token is missing or malformed" ;;
esac
for id in "$PING_ME_CHANNEL_ID" "$PING_ME_USER_ID"; do
	case "$id" in
	'' | *[!0-9]*) fail "channel_id and user_id must be numeric Discord IDs" ;;
	esac
done

export PING_ME_BOT_TOKEN PING_ME_CHANNEL_ID PING_ME_USER_ID
exec /run.sh "$@"
