#!/usr/bin/env bash
# Throwaway Tang server for VM tests. Serves until interrupted.
#
#   tang.sh STATE_DIR LISTEN_ADDRESS [PORT]
#
# Bind it to an address the VM can reach through QEMU's user network (any
# address of this workstation), not to 0.0.0.0. Writes the signing-key
# thumbprint to STATE_DIR/thumbprint.
set -euo pipefail

state="${1:?usage: tang.sh STATE_DIR LISTEN_ADDRESS [PORT]}"
address="${2:?usage: tang.sh STATE_DIR LISTEN_ADDRESS [PORT]}"
port="${3:-7654}"
: "${TANG:?set TANG to the tang package (nix build nixpkgs#tang)}"

mkdir -p "$state/db"
if [[ -z "$(ls -A "$state/db")" ]]; then
  "$TANG/libexec/tangd-keygen" "$state/db"
fi
for key in "$state"/db/*.jwk; do
  if jose fmt -j- -g key_ops -A -o- <"$key" | grep -q '"verify"'; then
    jose jwk thp -i "$key" >"$state/thumbprint"
  fi
done
echo "Tang on http://$address:$port, thumbprint $(cat "$state/thumbprint")"
exec socat "TCP-LISTEN:$port,bind=$address,fork,reuseaddr" "EXEC:$TANG/libexec/tangd $state/db"
