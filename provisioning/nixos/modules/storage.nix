{ config, lib, ... }:

# Storage contract for new nodes: EFI, /boot, LUKS2 -> LVM -> root XFS and
# appdata XFS. The rest of the volume group stays free for growth and for the
# VDO pool, which `just storage` adds after install (disko has no VDO type).
#
# disko only formats at install time. Afterwards, grow with
# `lvextend -r -L +NG vg_node/<lv>`; never run disko's destroy mode on a node.
let
  cfg = config.homelab.node.storage;
in
{
  options.homelab.node.storage = {
    disk = lib.mkOption {
      type = lib.types.str;
      example = "/dev/disk/by-id/nvme-Example_SSD_123";
      description = "Whole disk to install on. Prefer a /dev/disk/by-id path.";
    };
    rootSize = lib.mkOption {
      type = lib.types.str;
      default = "64G";
    };
    appdataSize = lib.mkOption {
      type = lib.types.str;
      default = "128G";
      description = "Mounted at /srv/appdata; K3s local-path volumes live here.";
    };
    vdo = {
      enable = lib.mkEnableOption "the post-install VDO volume at /srv/vdo";
      mountPoint = lib.mkOption {
        type = lib.types.str;
        default = "/srv/vdo";
      };
    };
  };

  config = {
    disko.devices = {
      disk.main = {
        type = "disk";
        device = cfg.disk;
        content = {
          type = "gpt";
          partitions = {
            esp = {
              size = "512M";
              type = "EF00";
              content = {
                type = "filesystem";
                format = "vfat";
                mountpoint = "/efi";
                mountOptions = [ "umask=0077" ];
              };
            };
            # Extended Boot Loader Partition (XBOOTLDR) holds kernels and initrds.
            boot = {
              size = "2G";
              type = "EA00";
              content = {
                type = "filesystem";
                format = "vfat";
                mountpoint = "/boot";
                mountOptions = [ "umask=0077" ];
              };
            };
            luks = {
              size = "100%";
              content = {
                type = "luks";
                name = "cryptroot";
                # Throwaway install passphrase, passed by nixos-anywhere with
                # --disk-encryption-keys and removed by `just enroll`.
                passwordFile = "/tmp/install.key";
                extraFormatArgs = [
                  "--type"
                  "luks2"
                  "--pbkdf"
                  "argon2id"
                ];
                settings.allowDiscards = true;
                content = {
                  type = "lvm_pv";
                  vg = "vg_node";
                };
              };
            };
          };
        };
      };
      lvm_vg.vg_node = {
        type = "lvm_vg";
        lvs = {
          root = {
            size = cfg.rootSize;
            content = {
              type = "filesystem";
              format = "xfs";
              mountpoint = "/";
              mountOptions = [ "noatime" ];
            };
          };
          appdata = {
            size = cfg.appdataSize;
            content = {
              type = "filesystem";
              format = "xfs";
              mountpoint = "/srv/appdata";
              mountOptions = [ "noatime" ];
            };
          };
        };
      };
    };

    services.lvm.boot.vdo.enable = cfg.vdo.enable;
    # LVM monitors VDO pool usage through dmeventd.
    services.lvm.dmeventd.enable = cfg.vdo.enable;
    fileSystems = lib.mkIf cfg.vdo.enable {
      ${cfg.vdo.mountPoint} = {
        device = "/dev/vg_node/vdo";
        fsType = "xfs";
        options = [
          "noatime"
          "nofail"
          "x-systemd.device-timeout=30s"
        ];
      };
    };
  };
}
