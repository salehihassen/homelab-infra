#!/bin/sh
# Build Caddy's imports from sites whose routing variables are available.
# The optional env files contain routing/auth settings, not application secrets.
set -eu

site_root="${CADDY_SITE_ROOT:-/etc/caddy/stacks}"
site_imports="${CADDY_SITE_IMPORTS:-/tmp/active-sites.caddy}"
temporary_imports="$(mktemp "${site_imports}.XXXXXX")"
trap 'rm -f "$temporary_imports"' EXIT HUP INT TERM

for site in "$site_root"/*/site.caddy; do
  [ -f "$site" ] || continue
  missing_variables=""
  variables="$(grep -oE '\{\$[A-Z][A-Z0-9_]*\}' "$site" | sed 's/^{\$//; s/}$//' | sort -u)"
  for variable in $variables; do
    if [ -z "$(printenv "$variable" || true)" ]; then
      missing_variables="$missing_variables $variable"
    fi
  done
  if [ -n "$missing_variables" ]; then
    printf 'Caddy: skipping %s; missing routing variables:%s\n' \
      "$(basename "$(dirname "$site")")" "$missing_variables" >&2
    continue
  fi
  printf 'import "%s"\n' "$site" >> "$temporary_imports"
done

mv "$temporary_imports" "$site_imports"
trap - EXIT HUP INT TERM
exec "$@"
