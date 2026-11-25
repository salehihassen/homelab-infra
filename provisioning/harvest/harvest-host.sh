#!/usr/bin/env bash
# Record how an existing Ubuntu host is set up, read-only, so the setup can be
# reproduced with Ansible on other hosts. Complements the nightly backup's
# recovery inventory (block devices, every package, Docker state).
#
#   bash harvest-host.sh OUTDIR               # as the admin user
#   sudo bash harvest-host.sh --privileged OUTDIR   # root-only sections
#
# The output identifies the host (names, addresses, paths), so OUTDIR must be
# outside Git or under the untracked local/ directory. Secret file contents are
# never copied: only vetted non-secret configuration is, with credential-like
# assignments redacted, and everything else is recorded as names only.
set -uo pipefail

privileged=0
if [[ "${1:-}" == "--privileged" ]]; then
  privileged=1
  shift
fi
out="${1:?usage: harvest-host.sh [--privileged] OUTDIR}"

if ((privileged)) && ((EUID != 0)); then
  echo "--privileged must run as root (sudo)." >&2
  exit 2
fi
if ((!privileged)) && ((EUID == 0)); then
  echo "Run the user sections as the admin user, without sudo." >&2
  exit 2
fi
mkdir -p "$(dirname -- "${out}")"
if git -C "$(dirname -- "${out}")" rev-parse --is-inside-work-tree >/dev/null 2>&1 &&
  ! git -C "$(dirname -- "${out}")" check-ignore -q "${out}"; then
  echo "Refusing to write host details to a Git-tracked location: ${out}" >&2
  exit 2
fi

umask 077
mkdir -p "${out}"
section="user"
((privileged)) && section="root"
dest="${out}/${section}"
mkdir -p "${dest}"

# Redact values of assignments whose key looks like a credential, and
# credentials embedded in URLs.
redact() {
  sed -E \
    -e 's#(://)[^/@[:space:]]+@#\1REDACTED@#g' \
    -e 's#^([^#]*(pass(word)?|passwd|secret|token|api[_-]?key|auth[_-]?key|psk|credential)[A-Za-z0-9_.-]*[[:space:]]*[=:][[:space:]]*).*#\1REDACTED#I'
}

# run NAME COMMAND...: save stdout to NAME, errors to NAME.err; never abort.
run() {
  local name="$1"
  shift
  "$@" >"${dest}/${name}" 2>"${dest}/${name}.err"
  [[ -s "${dest}/${name}.err" ]] || rm -f "${dest}/${name}.err"
}

# copy_config NAME PATH...: concatenate readable regular files, redacted.
copy_config() {
  local name="$1" file
  shift
  for file in "$@"; do
    [[ -f "${file}" && -r "${file}" ]] || continue
    printf '===== %s\n' "${file}"
    redact <"${file}"
  done >"${dest}/${name}"
}

# list_names NAME DIR...: file names and metadata only, never contents.
list_names() {
  local name="$1"
  shift
  find "$@" -maxdepth 1 -mindepth 1 -printf '%M %u:%g %p\n' 2>/dev/null | sort >"${dest}/${name}"
}

date --iso-8601=seconds >"${dest}/generated-at.txt"

if ((!privileged)); then
  # Operating system and kernel
  copy_config os-release.txt /etc/os-release
  run kernel.txt uname -a
  run kernel-cmdline.txt cat /proc/cmdline
  run timedate.txt timedatectl show
  run locale.txt localectl status
  run modules-loaded.txt lsmod

  # Software
  run apt-manual.txt apt-mark showmanual
  run snaps.txt snap list
  command -v flatpak >/dev/null && run flatpaks.txt flatpak list --columns=application,origin
  copy_config apt-sources.txt /etc/apt/sources.list /etc/apt/sources.list.d/*
  list_names apt-keyrings.txt /etc/apt/keyrings /usr/share/keyrings /etc/apt/trusted.gpg.d
  copy_config apt-preferences.txt /etc/apt/preferences /etc/apt/preferences.d/*
  copy_config apt-upgrades.txt /etc/apt/apt.conf.d/20auto-upgrades /etc/apt/apt.conf.d/50unattended-upgrades
  list_names usr-local-bin.txt /usr/local/bin /usr/local/sbin
  list_names opt.txt /opt
  list_names home-local-bin.txt "${HOME}/.local/bin"
  command -v dkms >/dev/null && run dkms.txt dkms status

  # Services
  run units-enabled.txt systemctl list-unit-files --state=enabled --no-pager
  run units-failed.txt systemctl list-units --state=failed --no-pager
  run timers.txt systemctl list-timers --all --no-pager
  run user-units-enabled.txt systemctl --user list-unit-files --state=enabled --no-pager
  run user-timers.txt systemctl --user list-timers --all --no-pager
  run linger.txt loginctl show-user "${USER}" --property=Linger
  list_names systemd-units-local.txt /etc/systemd/system "${HOME}/.config/systemd/user"
  run crontab-user.txt crontab -l
  list_names cron-dirs.txt /etc/cron.d /etc/cron.daily /etc/cron.weekly

  # Users and access
  run id.txt id
  copy_config ssh-config.txt /etc/ssh/sshd_config /etc/ssh/sshd_config.d/*
  # shellcheck disable=SC2016 # expanded by the inner shell
  run authorized-key-types.txt sh -c 'awk "{print \$1, \$NF}" "$HOME/.ssh/authorized_keys"'
  list_names sudoers-d.txt /etc/sudoers.d

  # Kernel, network and storage settings
  copy_config sysctl.txt /etc/sysctl.conf /etc/sysctl.d/*
  copy_config modules-load.txt /etc/modules /etc/modules-load.d/* /etc/modprobe.d/*
  copy_config fstab.txt /etc/fstab
  copy_config crypttab.txt /etc/crypttab
  copy_config grub-default.txt /etc/default/grub /etc/default/grub.d/*
  copy_config journald.txt /etc/systemd/journald.conf /etc/systemd/journald.conf.d/*
  list_names netplan.txt /etc/netplan
  run lsblk.txt lsblk -o NAME,TYPE,SIZE,FSTYPE,FSVER,UUID,MOUNTPOINTS
  run findmnt.txt findmnt --real -o TARGET,SOURCE,FSTYPE,OPTIONS
  run df.txt df -hT -x tmpfs -x devtmpfs -x squashfs -x overlay
  run listening.txt ss -H -ltnu
  run tpm.txt sh -c 'ls /sys/class/tpm; cat /sys/class/tpm/tpm0/tpm_version_major 2>/dev/null'
  # shellcheck disable=SC2016 # dpkg-query format fields, not shell variables
  run initramfs-tool.txt sh -c 'dpkg-query -W -f="\${binary:Package} \${Version} \${db:Status-Abbrev}\n" initramfs-tools dracut clevis clevis-luks clevis-initramfs clevis-dracut clevis-tpm2 tang 2>/dev/null'

  # Container and VPN tooling
  copy_config docker-daemon.txt /etc/docker/daemon.json
  command -v docker >/dev/null && run docker-info.txt docker info --format \
    'version={{.ServerVersion}} storage={{.Driver}} cgroup={{.CgroupDriver}}/{{.CgroupVersion}} root={{.DockerRootDir}} logging={{.LoggingDriver}}'
  command -v docker >/dev/null && run docker-compose-version.txt docker compose version
  command -v tailscale >/dev/null && run tailscale-version.txt tailscale version
  command -v tailscale >/dev/null && run tailscale-serve.txt tailscale serve status
  command -v nix >/dev/null && run nix-version.txt nix --version

  # Home directory names, for the Home Manager conflict preflight
  list_names home-dotfiles.txt "${HOME}"
  list_names home-config.txt "${HOME}/.config"
else
  run luks-devices.txt sh -c 'blkid -t TYPE=crypto_LUKS -o device'
  : >"${dest}/luks.txt"
  while read -r device; do
    {
      printf '===== %s\n' "${device}"
      # Header metadata only: version, keyslot numbers and types, tokens.
      cryptsetup luksDump "${device}" | grep -E '^(Version|Keyslots|Tokens|Digests|  [0-9]+: )'
      command -v clevis >/dev/null && clevis luks list -d "${device}"
    } >>"${dest}/luks.txt" 2>&1
  done <"${dest}/luks-devices.txt"
  run lvm-pvs.txt pvs --units g
  run lvm-vgs.txt vgs --units g -o vg_name,vg_size,vg_free,lv_count
  run lvm-lvs.txt lvs --units g -o lv_name,vg_name,lv_size,segtype,devices
  run ufw.txt ufw status verbose
  run nft.txt nft list ruleset
  run sshd-effective.txt sshd -T
  run crontab-root.txt crontab -l
  # Changed package-owned files; names only (the files may hold secrets).
  run dpkg-verify.txt dpkg --verify
  run sudoers-names.txt ls -1 /etc/sudoers.d
fi

echo "Wrote ${dest}"
