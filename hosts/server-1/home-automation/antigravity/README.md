# Antigravity quotas in Home Assistant

One HA command sensor runs `agy -p /usage --output-format json` every 15 minutes
through localhost SSH. Eight template sensors expose Gemini and Claude/GPT
five-hour/weekly percentages and reset timestamps. Google credentials stay on
the host; automatic CLI updates are disabled per poll.

Setup:

1. Sign in with the host CLI. Give HA a dedicated SSH key at
   `/config/.ssh/antigravity_usage` (0600) and pin the host key in
   `/config/.ssh/antigravity_known_hosts`. Use [authorized_keys.example](authorized_keys.example)
   with your host paths, username and public key. It restricts access to loopback
   and the exact usage request, clears the environment, and closes stdin.
   Keep its timeout and lock; the requested command must match the forced check.
2. Merge [command_line.example.yaml](command_line.example.yaml), replacing the
   username and CLI path. Merge [template.example.yaml](template.example.yaml)
   into `template.sensor`.
3. Run HA's `check_config`, then reload `template` and `command_line`.

Failures retain the last sample; entities expire after 45 minutes. Epoch reset
times are unavailable. If authentication fails, sign in again on the host.

Rollback: remove these config entries and the dedicated SSH authorization/key,
then reload both integrations. Dashboards remain UI-managed.
