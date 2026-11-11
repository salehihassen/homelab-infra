# Sourced by the hooks: decide which validation suites a change needs.
# Both functions read changed paths, one per line, on stdin.

# True when no path can affect Compose, Caddy, Alloy, Prometheus, Loki or the
# unit tests. Stack READMEs are checked by check-stacks.py, so Markdown only
# counts outside hosts/. Anything unlisted still gets the full suite.
docs_or_kubernetes_only() {
  awk 'NF && !/^(docs|kubernetes|provisioning\/k3s)\// && !(/\.md$/ && !/^hosts\//) { other = 1 }
       END { exit other }'
}

# True when any path feeds the Kubernetes validation.
touches_kubernetes() {
  grep -qE '^(kubernetes/|provisioning/k3s/|scripts/(check-kubernetes\.py|validate-kubernetes\.sh|with-kubernetes-tools\.sh|render-flux-operator\.sh)|\.github/workflows/validate-kubernetes\.yml)'
}
