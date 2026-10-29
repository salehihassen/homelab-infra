# Nested Codex sandbox

Codex 0.164.0 requires bubblewrap to isolate agent shell commands. Docker's
default seccomp and AppArmor policies prevent it from creating namespaces and
mounting the isolated filesystem. These profiles keep Codex's inner sandbox
enabled without granting the application any host capabilities, privileged mode,
an unconfined security profile, host namespaces or Docker socket access.

Both profiles are adapted from Moby profiles revision
`4b8214fc4971e06172938a1f1554ccff6647ee80`, licensed under Apache-2.0; see
[LICENSE.moby](LICENSE.moby). Original sources:

- [seccomp/default.json](https://github.com/moby/profiles/blob/4b8214fc4971e06172938a1f1554ccff6647ee80/seccomp/default.json)
- [apparmor/template.go](https://github.com/moby/profiles/blob/4b8214fc4971e06172938a1f1554ccff6647ee80/apparmor/template.go)

The seccomp profile retains the upstream default-deny policy and adds only:

- `clone` when creating a user namespace and not creating a cgroup namespace.
- `unshare` with only user, mount, PID, network, IPC and UTS namespace flags.
- `mount`, `umount2` and `pivot_root` for bubblewrap's filesystem setup.

These calls remain subject to Linux capability checks. The outer container has
zero capabilities; mounts become possible only inside the nested user namespace.
`clone3` retains Moby's ENOSYS fallback, and operations such as `bpf`, kernel module
loading and entering another namespace remain denied by the default policy.

The AppArmor profile uses Moby's ABI 3.0 template and retains its file, network,
signal and ptrace rules. It replaces the blanket mount denial with permission
for mount and pivot-root operations, whose capability checks still apply.
The profile is named `career-ops-sandbox`; it does not modify `docker-default`
or the host's global user-namespace restrictions. ABI 3.0 preserves the template's
compatibility with nested user namespaces and Unix sockets.

Install the files from the repository root before starting Career Ops:

```bash
install -d -m 0700 /opt/career-ops/security
install -m 0600 hosts/server-1/career-ops/security/seccomp.json /opt/career-ops/security/seccomp.json
sudo install -m 0644 hosts/server-1/career-ops/security/career-ops-sandbox /etc/apparmor.d/career-ops-sandbox
sudo apparmor_parser --replace /etc/apparmor.d/career-ops-sandbox
```

AppArmor loads the persistent profile at boot. Compose fails closed if the named
profile or seccomp file is absent. Reload the profile and recreate only Career Ops
services when changing either policy; see the stack README for service commands.
The `/opt/career-ops/security` copy is covered by the stack's backup declaration;
restore/reinstall the host AppArmor profile from this repository during recovery.

Verify actual sandbox enforcement without any model call:

```bash
python3 hosts/server-1/career-ops/test-sandbox.py
```

For a credential-free predeployment test, add
`--image <career-ops-image>`. This uses a disposable container with no network,
no application bind mounts and empty tmpfs workspace/home directories. Tests cover
read-only access, allowed workspace writes, denied writes to the separate home
mount, URL fetching, blocked networking and absence of outer mount privileges.
Fixtures are removed in both modes.

Career Ops still reports partial restriction for Codex workers that must fetch
URLs while intending only to read. Upstream deliberately gives those workers
`workspace-write` plus network access. This deployment makes that policy work;
it does not hide the warning or turn a writable workspace into a read-only one.
