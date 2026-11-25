{
  # Placeholder site for NixOS nodes. The real one lives in the untracked
  # local/nixos-site/ (copy this directory there) and replaces this input:
  #   --override-input site path:./local/nixos-site
  # The provisioning justfile passes that flag for you.
  description = "Private K3s node site settings (example)";

  # The real site adds the shared Home Manager modules, pinned in its own
  # flake.lock (nodes without it get no Home Manager profile):
  #   inputs.my-nixos-setup.url = "github:<owner>/my-nixos-setup";

  outputs = _: {
    # Root SSH keys baked into `just iso`, the installer USB image.
    installer.authorizedKeys = [ "ssh-ed25519 replace-with-public-key admin@example" ];

    hosts.example-node = {
      system = "x86_64-linux";
      module = {
        networking.hostName = "example-node";
        time.timeZone = "UTC";
        homelab.node = {
          adminUser = "admin";
          adminAuthorizedKeys = [ "ssh-ed25519 replace-with-public-key admin@example" ];
          storage.disk = "/dev/disk/by-id/replace-with-disk-id";
          unlock.tang.url = "http://tang.private.example";
        };
        # Generated during install by `just install`; keep it beside this file.
        # hardware.facter.reportPath = ./example-node.facter.json;
      };
    };
  };
}
