{
  config,
  lib,
  pkgs,
  inputs,
  ...
}:

# Base for every NixOS node in the private K3s cluster. Per-host values
# (hostname, admin user, keys, disk, Tang) come from the untracked site flake.
let
  cfg = config.homelab.node;
  dotfiles = inputs.site.inputs.my-nixos-setup or null;
in
{
  imports = [
    ./storage.nix
    ./unlock.nix
  ];

  options.homelab.node = {
    adminUser = lib.mkOption {
      type = lib.types.str;
      description = "Login user with sudo and the portable-core Home Manager profile.";
    };
    adminAuthorizedKeys = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      description = "SSH public keys for the admin user and initrd unlock.";
    };
    tailscaleAuthKeyFile = lib.mkOption {
      type = lib.types.str;
      default = "/var/lib/homelab-enroll/tailscale-auth.key";
      description = ''
        Single-use Tailscale auth key copied in at install time. tailscaled
        only reads it while the node isn't logged in.
      '';
    };
  };

  config = {
    system.stateVersion = "25.11";
    nixpkgs.hostPlatform = lib.mkDefault "x86_64-linux";

    boot.loader.systemd-boot = {
      enable = true;
      xbootldrMountPoint = "/boot";
      configurationLimit = 10;
    };
    boot.loader.efi = {
      efiSysMountPoint = "/efi";
      canTouchEfiVariables = true;
    };

    networking.useNetworkd = true;
    systemd.network.networks."10-ethernet" = {
      matchConfig.Type = "ether";
      networkConfig.DHCP = "yes";
    };
    networking.firewall.enable = true;

    services.openssh = {
      enable = true;
      settings = {
        PasswordAuthentication = false;
        KbdInteractiveAuthentication = false;
        PermitRootLogin = "no";
      };
    };

    users.users.${cfg.adminUser} = {
      isNormalUser = true;
      extraGroups = [ "wheel" ];
      openssh.authorizedKeys.keys = cfg.adminAuthorizedKeys;
    };
    # Key-only SSH with passwordless sudo, so `nixos-rebuild --target-host`
    # and Ansible-style automation need no password on the node.
    security.sudo.wheelNeedsPassword = false;
    security.sudo.execWheelOnly = true;
    nix.settings = {
      experimental-features = [
        "nix-command"
        "flakes"
      ];
      trusted-users = [ "@wheel" ];
    };
    nix.gc = {
      automatic = true;
      dates = "weekly";
      options = "--delete-older-than 30d";
    };

    services.tailscale = {
      enable = true;
      authKeyFile = cfg.tailscaleAuthKeyFile;
      openFirewall = true;
    };

    time.timeZone = lib.mkDefault "UTC";
    zramSwap.enable = true;
    services.fstrim.enable = true;
    services.smartd.enable = lib.mkDefault true;

    environment.systemPackages = with pkgs; [
      clevis
      cryptsetup
      jose
      tpm2-tools
      xfsprogs
      smartmontools
      ethtool
      pciutils
      usbutils
    ];

    # portable-core from my-nixos-setup, which the site flake pins, with
    # dotfiles linked read-only from that input (see its README for editing).
    home-manager = lib.mkIf (dotfiles != null) {
      useGlobalPkgs = true;
      useUserPackages = true;
      extraSpecialArgs = {
        username = cfg.adminUser;
        dotfilesRoot = "/home/${cfg.adminUser}/.dotfiles-unused";
      };
      users.${cfg.adminUser} = {
        imports = [ dotfiles.homeModules.portable-core ];
        dotfiles.source = dotfiles;
      };
    };
  };
}
