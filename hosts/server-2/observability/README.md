# Remote logging

Alloy sends Docker and host journal logs to an authenticated private HTTPS
Loki push endpoint. Its own HTTP listener is loopback-only. No Loki or Grafana
server is started on this host. Credentials are scoped to log writing and read
from a mode-0600 file; no reader credentials are deployed.

Runtime values live in `/opt/observability/app.env`: `HOST_LABEL`,
`LOKI_PUSH_URL`, `LOKI_USERNAME` and `GOMEMLIMIT`. Keep `HOST_LABEL` stable.
Alloy cursor state is disposable; replay older than 168 hours is dropped.
The Docker socket gives administrative power even through a read-only bind.
The journal mounts provide scheduled-backup unit logs as well as host events.
