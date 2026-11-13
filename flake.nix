{
  # Minimal dev shell for the pre-commit hook and validation scripts.
  # Not a full toolchain: gitleaks, Docker Compose and the Kubernetes tools
  # still come from the host or scripts/with-*-tools.sh.
  description = "homelab-infra validation shell";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { nixpkgs, ... }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
      forAll = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
    in {
      devShells = forAll (pkgs: {
        default = pkgs.mkShell {
          packages = [ (pkgs.python3.withPackages (p: [ p.pyyaml ])) ];
        };
      });
    };
}
