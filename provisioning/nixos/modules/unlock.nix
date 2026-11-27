{ config, lib, ... }:

# Root unlock: clevis (TPM2 or Tang) first, then a passphrase typed at the
# console or over SSH in the initrd. The JWE file is copied in at install time
# (Tang-only, wrapping the install passphrase) and replaced by `just enroll`
# with a TPM2-or-Tang binding exported from the LUKS header.
#
# Tang only works on the LAN: the initrd has no Tailscale.
let
  cfg = config.homelab.node.unlock;
  ethernet = {
    matchConfig.Type = "ether";
    networkConfig.DHCP = "yes";
  };
in
{
  options.homelab.node.unlock = {
    clevis.enable = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = ''
        Unlock with the JWE at /var/lib/homelab-unlock/cryptroot.jwe. The file
        must exist on the node, or the bootloader install fails.
      '';
    };
    tang = {
      url = lib.mkOption {
        type = lib.types.str;
        example = "http://tang.private.example";
        description = "Tang server reachable from the node's LAN.";
      };
      thumbprint = lib.mkOption {
        type = lib.types.nullOr lib.types.str;
        default = null;
        description = ''
          Tang signing-key thumbprint (`tang-show-keys` on the server). Pins
          the server at enrollment instead of trusting its first answer.
        '';
      };
    };
    tpm2.pcrIds = lib.mkOption {
      type = lib.types.str;
      default = "7";
      description = "PCRs the TPM2 pin seals against (7 = Secure Boot state).";
    };
    initrdSsh.port = lib.mkOption {
      type = lib.types.port;
      default = 2222;
    };
  };

  config = {
    boot.initrd.network.enable = true;
    boot.initrd.systemd.network = {
      enable = true;
      networks."10-ethernet" = ethernet;
    };

    # Fallback unlock: `ssh -p 2222 root@<node>` answers the passphrase prompt.
    # The host key is copied in at install time, never generated into Git.
    boot.initrd.network.ssh = {
      enable = true;
      port = cfg.initrdSsh.port;
      hostKeys = [ "/etc/secrets/initrd/ssh_host_ed25519_key" ];
      authorizedKeys = config.homelab.node.adminAuthorizedKeys;
    };

    boot.initrd.clevis = lib.mkIf cfg.clevis.enable {
      enable = true;
      useTang = true;
      devices.cryptroot.secretFile = "/var/lib/homelab-unlock/cryptroot.jwe";
    };
  };
}
