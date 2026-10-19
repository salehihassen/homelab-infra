#!/usr/bin/env sh
set -eu

printf '%s\n' 'Optical block devices:'
lsblk -o NAME,PATH,TYPE,VENDOR,MODEL,TRAN | awk 'NR == 1 || $3 == "rom"'
printf '\n%s\n' 'Matching Linux SCSI generic devices (often required by MakeMKV):'
for block in /sys/class/block/sr*; do
  [ -e "$block/device" ] || continue
  block_name=${block##*/}
  block_path=/dev/$block_name
  block_device=$(readlink -f "$block/device")
  for generic in /sys/class/scsi_generic/sg*; do
    [ -e "$generic/device" ] || continue
    if [ "$(readlink -f "$generic/device")" = "$block_device" ]; then
      generic_path=/dev/${generic##*/}
      printf '%s (matches %s)\n' "$generic_path" "$block_path"

      stable_block=
      for candidate in /dev/disk/by-id/*; do
        [ -L "$candidate" ] || continue
        if [ "$(readlink -f "$candidate")" = "$(readlink -f "$block_path")" ]; then
          stable_block=$candidate
          case $candidate in
            /dev/disk/by-id/usb-*) break ;;
          esac
        fi
      done

      serial=$(udevadm info --query=property --name="$block_path" 2>/dev/null |
        sed -n 's/^ID_SERIAL_SHORT=//p' | head -n 1)

      if [ -n "$stable_block" ] && [ -n "$serial" ]; then
        stable_generic=/dev/makemkv/sg-$serial
        printf '\n%s\n' "Recommended stable mapping for $block_path:"
        printf 'MEDIA_DVD_DEVICE=%s\n' "$stable_block"
        printf 'MEDIA_DVD_SG_DEVICE=%s\n' "$stable_generic"
        if [ ! -e "$stable_generic" ]; then
          printf '%s\n' '  (The SCSI-generic alias is not installed; see README.md.)'
        fi
      fi
    fi
  done
done
