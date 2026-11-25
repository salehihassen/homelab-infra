{
  # Dev shells for the pre-commit hook, validation scripts and host
  # provisioning, plus NixOS configurations for private K3s nodes.
  # Not a full toolchain: gitleaks, Docker Compose and the Kubernetes tools
  # still come from the host or scripts/with-*-tools.sh.
  description = "homelab-infra validation shell and NixOS nodes";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    home-manager = {
      url = "github:nix-community/home-manager";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    disko = {
      url = "github:nix-community/disko";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    # Per-host private values. The tracked example is a placeholder; the
    # provisioning justfile overrides it with the untracked local/nixos-site,
    # which also pins the my-nixos-setup input (portable-core, dotfiles).
    site.url = "path:./provisioning/nixos/site.example";
  };

  outputs =
    {
      nixpkgs,
      home-manager,
      disko,
      site,
      ...
    }@inputs:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
      forAll = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
      mkNode =
        host:
        nixpkgs.lib.nixosSystem {
          specialArgs = { inherit inputs; };
          modules = [
            { nixpkgs.hostPlatform = host.system; }
            disko.nixosModules.disko
            home-manager.nixosModules.home-manager
            ./provisioning/nixos/modules/node.nix
            host.module
          ];
        };
      # NixOS minimal installer that accepts the site's SSH keys for root, so
      # `just install` can reach a freshly booted USB without console setup.
      installer = nixpkgs.lib.nixosSystem {
        modules = [
          "${nixpkgs}/nixos/modules/installer/cd-dvd/installation-cd-minimal.nix"
          {
            nixpkgs.hostPlatform = "x86_64-linux";
            users.users.root.openssh.authorizedKeys.keys = site.installer.authorizedKeys;
          }
        ];
      };
    in
    {
      nixosConfigurations = nixpkgs.lib.mapAttrs (_: mkNode) site.hosts;
      packages.x86_64-linux.installer-iso = installer.config.system.build.isoImage;

      devShells = forAll (pkgs: {
        default = pkgs.mkShell {
          packages = [ (pkgs.python3.withPackages (p: [ p.pyyaml ])) ];
        };
        # Host provisioning (provisioning/README.md): ansible-core plus the
        # community collections, pinned by flake.lock instead of Galaxy, and
        # nixos-anywhere for NixOS nodes.
        provisioning = pkgs.mkShell {
          packages = [
            (pkgs.python3.withPackages (p: [ p.pyyaml p.ansible-core p.ansible ]))
            pkgs.ansible-lint
            pkgs.just
            pkgs.shellcheck
            pkgs.nixos-anywhere
            pkgs.clevis
            pkgs.jose
            pkgs.xkcdpass
          ];
        };
        # Local UEFI + TPM2 VMs and a throwaway Tang server
        # (provisioning/vm-test/).
        vm-test = pkgs.mkShell {
          packages = [
            pkgs.qemu_kvm
            pkgs.swtpm
            pkgs.socat
            pkgs.jose
            pkgs.just
          ];
          OVMF_FD = "${pkgs.OVMF.fd}";
          TANG = "${pkgs.tang}";
        };
      });
    };
}
