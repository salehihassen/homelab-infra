# Antigravity quotas in Home Assistant

One Home Assistant command sensor polls the official Antigravity CLI every
15 minutes. Eight template sensors expose the remaining percentage and reset
timestamp for the Gemini five-hour/weekly and Claude/GPT five-hour/weekly
buckets. Each percentage also has a `resets_at` attribute. No systemd timer,
exporter daemon, proxy integration, or separate Google OAuth client is needed.

The host CLI must support `agy -p /usage --output-format json` (introduced in
1.1.11) and already have a working Google login. The command is a read-only
slash command, not an agent prompt. Automatic updates are disabled per poll.
See the official [usage guide](https://antigravity.google/docs/cli/commands/usage/)
and [release notes](https://antigravity.google/releases?tab=cli&version=1.1.11).

## Host access from the container

Home Assistant is host-networked, but its container cannot execute the host's
CLI directly. It uses localhost SSH with a dedicated Ed25519 key, restricted
to the usage command. Google credentials stay with the existing host login;
they are not mounted or copied into Home Assistant.

1. Create a private directory under the mounted HA config at `.ssh/` (0700),
   with a dedicated `antigravity_usage` SSH key (private file 0600). It appears
   inside the container at `/config/.ssh/antigravity_usage`.
2. Pin the host's SSH public key in `.ssh/antigravity_known_hosts` as a
   `127.0.0.1` entry. Obtain it from the host's public key file, rather than
   accepting an unverified network scan.
3. Add the new public key to the CLI user's `authorized_keys`, with the options
   below. Substitute the host executable and lock paths. This is a single line:

   ```text
   restrict,from="127.0.0.1,::1",command="cd /tmp && exec /usr/bin/flock -n /path/to/private/antigravity-usage.lock /usr/bin/env AGY_CLI_DISABLE_AUTO_UPDATE=true BROWSER=/bin/false /usr/bin/timeout --kill-after=5s 90s /path/to/agy -p /usage --output-format json" ssh-ed25519 replace-with-public-key ha-antigravity-usage
   ```

   The forced command ignores requested SSH commands. Forwarding and PTYs are
   disabled, and only connections from loopback are accepted. The host login
   must work without an interactive keyring prompt, using the CLI's normal SSH
   login/token handling. If it expires, sign in with the official CLI on the
   host; the sensor does not implement OAuth refresh or start an interactive
   login. The CLI retains its own normal credential handling.

4. Merge `command_line.example.yaml` into the existing command-line include,
   replacing the SSH username and host executable path. Merge the eight
   entries in `template.example.yaml` into the existing `template.sensor`
   list, not the `binary_sensor` list. Preserve unrelated configuration.
5. Validate with `python3 -m homeassistant --script check_config --config
   /config` inside the container, then invoke `template.reload` and
   `command_line.reload`. A reload starts an initial poll; subsequent polls
   are scheduled by HA every 900 seconds.

## Data and failures

`sensor.antigravity_usage_updated` is the last successful sample timestamp,
with four normalized buckets in its `buckets` attribute. The command accepts
only successful `usage` reports, validates all four fractions, and exports only
percentages and reset times. It suppresses raw command diagnostics.

The command has a 110-second HA timeout, a 100-second SSH subprocess timeout,
and a 90-second host CLI timeout with a five-second termination grace period.
A nonblocking host lock prevents concurrent poll commands. Failures retain
the previous sample without changing its timestamp; template sensors become
unavailable after 45 minutes. Polling resumes at the next 15-minute interval;
there is no immediate retry loop. This interval is a local choice, not a
Google-published polling allowance.

Epoch reset timestamps become null. Their timestamp sensors remain unavailable
until Google supplies a valid reset time, while the remaining percentage stays
usable. Missing or malformed buckets reject the sample instead of inventing
allowances. The config directory, including this dedicated SSH key, is already
covered by the home-automation backup declaration; treat its backups as secret.

Entity suffixes beneath `sensor.antigravity_`:

- `gemini_5h_remaining`, `gemini_5h_resets_at`
- `gemini_weekly_remaining`, `gemini_weekly_resets_at`
- `claude_gpt_5h_remaining`, `claude_gpt_5h_resets_at`
- `claude_gpt_weekly_remaining`, `claude_gpt_weekly_resets_at`

Dashboards remain UI-managed. These entities can be selected in cards through
the dashboard editor; the integration does not change dashboard storage.

## Rollback

Remove only the new command-line item, eight template entries, and dedicated
`authorized_keys` entry. Reload the two integrations, then remove this
integration's SSH key and pinned-host file. Leave other SSH and HA settings
intact. No systemd unit, Compose mount, or Google credential needs removal.
