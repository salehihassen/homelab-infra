# Host provisioning

Reproducible setup for Ubuntu Server 26.04 and NixOS hosts in the private K3s
cluster. Ubuntu hosts are configured with Ansible. NixOS hosts will use
disko + nixos-anywhere from this repository's flake (not yet added).

Status: phase 2. The Ubuntu base roles exist and are checked against
server-1 in dry-run mode only. Installer media, storage/unlock enrollment,
Home Manager and K3s roles come in later phases.

## Layout

```
provisioning/
  justfile                 # harvest, check, apply, lint
  harvest/harvest-host.sh  # read-only record of an existing host's setup
  ansible/
    ansible.cfg
    site.yaml              # which roles each inventory group gets
    group_vars/ubuntu.yaml # become with classic sudo (sudo-rs breaks prompt detection)
    inventory.example.yaml # copy to local/ansible/inventory.yaml
    roles/
      base/                # packages, timezone, admin groups, unattended upgrades
      ssh/                 # authorized keys, sshd authentication drop-in
      tailscale/           # apt repository and package; joining happens at enrollment
      docker/              # Docker Engine for Compose hosts, mount-ordering drop-in
  k3s/                     # reviewed K3s config examples
```

Real host names, addresses, the admin user and per-host settings live only in
the untracked `local/ansible/inventory.yaml` and
`local/ansible/host_vars/<alias>.yaml`. Secrets never go in either: roles that
need one (Tailscale auth keys, K3s tokens, LUKS passphrases) will read it from
a file generated at enrollment time.

## Tooling

Everything runs from the flake's `provisioning` shell. It pins ansible-core,
the community collections, ansible-lint, just and shellcheck through
`flake.lock`, so nothing is downloaded from Ansible Galaxy. ansible-core and the
community collections are GPLv3; no Red Hat subscription is involved.

```bash
nix develop .#provisioning
just --justfile provisioning/justfile lint
just --justfile provisioning/justfile check server-1   # dry run with diffs
just --justfile provisioning/justfile apply server-1   # only after reading the diff
```

`check` and `apply` prompt for the host's sudo password. Ubuntu 26.04 uses
sudo-rs by default, whose prompt Ansible can't detect, so
`ansible/group_vars/ubuntu.yaml` makes Ansible use the classic `sudo.ws`.

## Recording an existing host

`just harvest <ssh-target> <alias>` copies `harvest/harvest-host.sh` to the
host, runs its user sections, then its root sections (prompting for sudo), and
saves the result to `local/harvest/<alias>-<date>/`. It records:

- OS, kernel and boot settings
- manually installed apt packages, snaps, flatpaks, apt sources and keyring names
- enabled system and user units and timers, crontabs, lingering
- sshd, sysctl, module, fstab, crypttab, journald and Docker settings
- LUKS keyslots and clevis bindings, LVM layout, firewall rules
- changed package-owned files (names only)
- home directory entries, which the Home Manager conflict preflight uses

It changes nothing on the host. Files that can hold credentials (netplan,
sudoers, cryptsetup keys) are recorded by name only, and credential-like
assignments in copied files are redacted. The output still identifies the
host, so it must stay under `local/`.

Use the harvest to sort a host's setup into:

| Group | Where it goes |
|---|---|
| Every node | `base`, `ssh`, `tailscale` role defaults |
| A role some nodes share | a role plus an inventory group (for example `compose_hosts`) |
| One host only | that host's untracked `host_vars` |
| User tools and dotfiles | the `portable-core` Home Manager profile from my-nixos-setup |
| Out of scope | desktop environments, applications already declared under `hosts/` |

## Safety rules

- Run `just check <host>` and read the diff before every `apply`.
- Roles never wipe disks, reformat volumes, restart Docker or remove existing
  SSH keys. The ssh role refuses to turn off password logins unless the admin
  user has an authorized key.
- server-1 is never reinstalled. It is a documented exception to the storage
  layout (ext4 root).
