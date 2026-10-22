#!/bin/sh
set -eu

device_number() {
  major_hex=$(stat -c '%t' "$1")
  minor_hex=$(stat -c '%T' "$1")
  printf '%d:%d\n' "$((0x$major_hex))" "$((0x$minor_hex))"
}

expose_kernel_name() {
  staged_device=$1
  sysfs_class=$2
  kernel_pattern=$3
  wanted_number=$(device_number "$staged_device")

  for sysfs_device in /sys/class/$sysfs_class/$kernel_pattern; do
    [ -e "$sysfs_device/dev" ] || continue
    [ "$(cat "$sysfs_device/dev")" = "$wanted_number" ] || continue

    kernel_device=/dev/${sysfs_device##*/}
    if [ -e "$kernel_device" ] || [ -L "$kernel_device" ]; then
      [ "$(device_number "$kernel_device")" = "$wanted_number" ] || {
        printf 'Refusing to replace mismatched device %s.\n' "$kernel_device" >&2
        exit 1
      }
    else
      ln -s "$staged_device" "$kernel_device"
    fi

    printf 'Exposed %s as kernel-reported device %s.\n' "$staged_device" "$kernel_device"
    return 0
  done

  printf 'No sysfs device with number %s found for %s.\n' "$wanted_number" "$staged_device" >&2
  exit 1
}

expose_kernel_name /dev/media-extract-sr block 'sr*'
expose_kernel_name /dev/media-extract-sg scsi_generic 'sg*'

# The image stats the kernel-name symlinks without following them when it
# discovers supplementary groups. Pass the actual device groups explicitly
# so the unprivileged app can open the targets after /init drops privileges.
SUP_GROUP_IDS="${SUP_GROUP_IDS:+$SUP_GROUP_IDS,}$(stat -c '%g' /dev/media-extract-sr),$(stat -c '%g' /dev/media-extract-sg)"
export SUP_GROUP_IDS

exec "$@"
