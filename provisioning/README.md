# Host provisioning

Reproducible setup for Ubuntu Server 26.04 and NixOS hosts in the private K3s
cluster. Ubuntu hosts are configured with Ansible. NixOS hosts are installed
with disko + nixos-anywhere from this repository's flake.

Status: phase 3. NixOS nodes install, unlock, enroll and grow storage end to
end in a local UEFI + TPM2 VM. Ubuntu Autoinstall media and the Ubuntu side of
enrollment come next; K3s comes after that.

## Layout

```
provisioning/
  justfile                 # harvest, check, apply, lint; iso, secrets, install,
                           # enroll, vdo, deploy for NixOS nodes
  harvest/harvest-host.sh  # read-only record of an existing host's setup
  enroll/luks-enroll.sh    # recovery passphrases + clevis TPM2-or-Tang (both OSes)
  nixos/
    modules/node.nix       # base node: boot, SSH, admin user, Tailscale, portable-core
    modules/storage.nix    # disko layout and the optional VDO mount
    modules/unlock.nix     # initrd clevis, initrd SSH fallback
    site.example/          # placeholder site flake; the real one is local/nixos-site
  vm-test/                 # UEFI + TPM2 QEMU VM and a throwaway Tang server
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

## NixOS nodes

Each node is a host entry in the untracked site flake `local/nixos-site/`
(start from `nixos/site.example/`): hostname, admin user and SSH keys, disk,
Tang URL and thumbprint, the hardware report and the `my-nixos-setup` input
that provides portable-core. Without that input a node gets no Home Manager
profile. The justfile overrides the
tracked placeholder `site` input with it, so private values never reach Git.

Storage follows the cluster contract: EFI (`/efi`), XBOOTLDR (`/boot`), then
LUKS2 -> LVM `vg_node` -> `root` XFS and `appdata` XFS (`/srv/appdata`). The
rest of the volume group stays free. Root unlocks with clevis (TPM2 or Tang,
either is enough); if both fail, type a recovery passphrase at the console or
over SSH to the initrd on port 2222.

```bash
nix develop .#provisioning
just --justfile provisioning/justfile iso                  # installer image; flash once
just --justfile provisioning/justfile secrets server-4     # local/secrets/server-4/; back it up
# save a single-use Tailscale auth key as local/secrets/server-4/tailscale-auth.key
# boot the node from the USB on the LAN, then:
just --justfile provisioning/justfile install server-4 <installer-address>
just --justfile provisioning/justfile enroll server-4 <admin>@server-4
just --justfile provisioning/justfile deploy server-4 <admin>@server-4   # later changes
```

- `install` saves the hardware report beside the site flake (add
  `hardware.facter.reportPath` to the host), shows the target disk and asks
  you to type the alias before erasing it. The first boot unlocks through a
  Tang-only binding of the throwaway install key.
- `enroll` adds both recovery passphrases, binds clevis TPM2-or-Tang, proves
  the binding decrypts, removes the install key and saves a LUKS header backup
  to `local/secrets/<alias>/`. It can be run again; after a TPM or motherboard
  swap or a Tang key rotation, run it with `REBIND=1` (it authorizes with
  recovery passphrase 1 once the install key is gone).
- VDO: set `homelab.node.storage.vdo.enable`, `deploy`, then
  `just vdo <alias> <target> <physical> <virtual>`. It never reformats.
- Grow later with LVM, online: `lvextend -r -L +50G vg_node/appdata`,
  `lvextend -L +100G vg_node/vpool0` (VDO physical) and
  `lvextend -r -L +300G vg_node/vdo` (VDO virtual), or `lvcreate` a new LV.
  Never run disko against an installed node.
- Dotfiles come read-only from the `my-nixos-setup` input that the site flake
  declares and pins (its URL names the owner, so it stays out of this repo).
  For a quick fix, build from a local clone:
  `NIX_FLAGS="--override-input site/my-nixos-setup path:/path/to/clone" just deploy ...`,
  then push the change there, `just update-dotfiles` and `just deploy`.
- Admin access is SSH keys only, with passwordless sudo for the admin user.

### Testing in a VM

`vm-test/run-vm.sh` starts a UEFI VM with a software TPM2 and the disk serial
`vmnode0`; `vm-test/tang.sh` runs a throwaway Tang server. Both run from
`nix develop .#vm-test` without root. Add a `vm-node` host to the local site,
then run the same recipes with `NODE_SSH_OPTS="-o Port=2201 ..."`. Tested so
far: install, unattended first boot through Tang, enrollment, unlock with Tang
up, TPM2 only, and recovery passphrase over initrd SSH with a blank TPM,
`REBIND=1` after a TPM swap, VDO creation, online growth and reboots.

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
- `install` is the only recipe that erases a disk, and only after you type
  the node's alias. Keep `local/secrets/<alias>/` backed up off the machine:
  it holds the recovery passphrases and LUKS header backups.
