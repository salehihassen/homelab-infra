#!/usr/bin/env bash
# Enroll unattended and recovery unlock on a node's root LUKS2 device.
# Runs as root on the node (Ubuntu or NixOS); `just enroll` copies it there.
#
#   luks-enroll.sh [--device DEV] --old-key FILE --tang-url URL
#                  [--tang-thp THP] [--pcr-ids 7] [--no-tpm]
#                  [--recovery FILE]... [--jwe-out PATH] [--header-out PATH]
#                  [--keep-old-key] [--rebind]
#
# --device defaults to the LUKS device under the root filesystem.
# When the old key no longer opens the device (already enrolled), the first
# --recovery passphrase authorizes the changes instead.
# --rebind replaces an existing clevis binding, for example after a TPM or
# motherboard replacement or a Tang key rotation: it binds a new keyslot,
# verifies it, then removes the old one.
#
# 1. Adds each recovery passphrase (file contents, no trailing newline).
# 2. Binds clevis sss with threshold 1: TPM2 or Tang.
# 3. Proves the binding by decrypting it and testing the result.
# 4. Optionally exports the binding as a JWE file (NixOS initrd reads a file).
# 5. Removes the old (install) passphrase, only after 1-3 succeeded.
# 6. Optionally writes a LUKS header backup.
#
# Safe to rerun: passphrases that already work are not added again, and an
# existing clevis binding is reused unless --rebind is given.
set -euo pipefail
umask 077

device="" old_key="" tang_url="" tang_thp="" pcr_ids="7" use_tpm=1
jwe_out="" header_out="" keep_old_key=0 rebind=0
recovery=()

die() { printf 'luks-enroll: %s\n' "$*" >&2; exit 1; }
log() { printf 'luks-enroll: %s\n' "$*"; }

while (($#)); do
  case "$1" in
    --device) device="$2"; shift 2 ;;
    --old-key) old_key="$2"; shift 2 ;;
    --tang-url) tang_url="$2"; shift 2 ;;
    --tang-thp) tang_thp="$2"; shift 2 ;;
    --pcr-ids) pcr_ids="$2"; shift 2 ;;
    --no-tpm) use_tpm=0; shift ;;
    --recovery) recovery+=("$2"); shift 2 ;;
    --jwe-out) jwe_out="$2"; shift 2 ;;
    --header-out) header_out="$2"; shift 2 ;;
    --keep-old-key) keep_old_key=1; shift ;;
    --rebind) rebind=1; shift ;;
    *) die "unknown argument: $1" ;;
  esac
done

((EUID == 0)) || die "run as root"
[[ -n "$old_key" && -n "$tang_url" ]] || die "--old-key and --tang-url are required"
if [[ -z "$device" ]]; then
  device="$(lsblk -nrpso NAME,FSTYPE "$(findmnt -nvo SOURCE /)" | awk '$2 == "crypto_LUKS" {print $1; exit}')"
  [[ -n "$device" ]] || die "no LUKS device under / ; pass --device"
  log "root LUKS device: $device"
fi
for tool in cryptsetup clevis jose; do
  command -v "$tool" >/dev/null || die "$tool is not installed"
done
cryptsetup isLuks --type luks2 "$device" || die "$device is not LUKS2"
((use_tpm == 0)) || [[ -e /dev/tpmrm0 ]] || die "no TPM2 (/dev/tpmrm0); pass --no-tpm to bind Tang only"

opens() { cryptsetup open --test-passphrase --key-file "$1" "$device" 2>/dev/null; }

auth="$old_key" old_key_present=1
if ! opens "$old_key"; then
  old_key_present=0
  if ! ((${#recovery[@]})) || ! opens "${recovery[0]}"; then
    die "neither the old key nor the first recovery passphrase opens $device"
  fi
  auth="${recovery[0]}"
  log "old key already removed; authorizing with $(basename "$auth")"
fi

# 1. Recovery passphrases.
for file in "${recovery[@]}"; do
  [[ -s "$file" ]] || die "empty recovery file: $file"
  [[ "$(tail -c1 "$file" | od -An -c | tr -d ' ')" != '\n' ]] || die "$file ends with a newline; it wouldn't match a typed passphrase"
  if opens "$file"; then
    log "recovery passphrase $(basename "$file") already enrolled"
  else
    cryptsetup luksAddKey --key-file "$auth" "$device" "$file"
    opens "$file" || die "recovery passphrase $(basename "$file") was added but doesn't open $device"
    log "added recovery passphrase $(basename "$file")"
  fi
done

# 2. Clevis binding: any one of TPM2 or Tang unlocks.
# jose fmt -s sets TOP into the object below it; -U then drops TOP.
tang_pin="$(jose fmt -j '{}' -q "$tang_url" -s url -U ${tang_thp:+-q "$tang_thp" -s thp -U} -o-)"
if ((use_tpm)); then
  tpm_pin="$(jose fmt -j '{}' -q sha256 -s pcr_bank -U -q "$pcr_ids" -s pcr_ids -U -o-)"
  pins="$(jose fmt -j '{}' -j "$tpm_pin" -s tpm2 -U -j "[$tang_pin]" -s tang -U -o-)"
else
  pins="$(jose fmt -j '{}' -j "[$tang_pin]" -s tang -U -o-)"
fi
sss="$(jose fmt -j '{}' -j 1 -s t -U -j "$pins" -s pins -U -o-)"

sss_slots() { clevis luks list -d "$device" 2>/dev/null | awk -F: '/sss/ {print $1}'; }
old_slots="$(sss_slots)"
if [[ -n "$old_slots" && "$rebind" == 0 ]]; then
  slot="${old_slots%%$'\n'*}"
  log "reusing clevis binding in keyslot $slot (--rebind replaces it)"
else
  trust=()
  [[ -n "$tang_thp" ]] || trust=(-y)  # no pinned thumbprint: trust the first answer
  clevis luks bind "${trust[@]}" -d "$device" -k "$auth" sss "$sss"
  slot="$(comm -13 <(sort <<<"$old_slots") <(sss_slots | sort) | head -n1)"
  [[ -n "$slot" ]] || die "clevis bind reported success but no new sss binding is listed"
  log "bound clevis sss (TPM2 or Tang) to keyslot $slot"
fi

# 3. Prove the binding decrypts to a working passphrase right now.
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
clevis luks pass -d "$device" -s "$slot" >"$work/pass"
opens "$work/pass" || die "clevis binding in keyslot $slot doesn't open $device"
log "clevis binding verified"
if ((rebind)); then
  for old in $old_slots; do
    [[ "$old" == "$slot" ]] && continue
    clevis luks unbind -f -d "$device" -s "$old"
    log "removed the replaced clevis binding in keyslot $old"
  done
fi

# 4. NixOS: export the binding as a compact JWE for boot.initrd.clevis.
if [[ -n "$jwe_out" ]]; then
  token=""
  for id in $(seq 0 31); do
    json="$(cryptsetup token export --token-id "$id" "$device" 2>/dev/null)" || continue
    if [[ "$(jose fmt -j- -g type -u- <<<"$json")" == clevis ]] \
      && jose fmt -j- -g keyslots -A -o- <<<"$json" | grep -q "\"$slot\""; then
      token="$json"
      break
    fi
  done
  [[ -n "$token" ]] || die "no clevis token for keyslot $slot"
  jose fmt -j- -Og jwe -o- <<<"$token" | jose jwe fmt -i- -c >"$work/cryptroot.jwe"
  clevis decrypt <"$work/cryptroot.jwe" | cmp -s - "$work/pass" || die "exported JWE doesn't decrypt to the bound passphrase"
  install -D -m 0600 "$work/cryptroot.jwe" "$jwe_out"
  log "exported JWE to $jwe_out"
fi

# 5. Remove the install passphrase.
if ((old_key_present == 0)); then
  :
elif ((keep_old_key)); then
  log "keeping the old key (--keep-old-key)"
else
  for file in "${recovery[@]}"; do
    cmp -s "$file" "$old_key" && die "a recovery file equals the old key; refusing to remove it"
  done
  cryptsetup luksRemoveKey "$device" "$old_key"
  ! opens "$old_key" || die "the old key still opens $device"
  log "removed the old key"
fi

# 6. Header backup (it contains keyslots; store it like a secret).
if [[ -n "$header_out" ]]; then
  rm -f "$header_out"
  cryptsetup luksHeaderBackup "$device" --header-backup-file "$header_out"
  log "header backup written to $header_out"
fi

cryptsetup luksDump "$device" | sed -n '/^Keyslots:/,/^Tokens:/p' | grep -E '^  [0-9]+: '
