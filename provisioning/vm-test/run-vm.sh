#!/usr/bin/env bash
# Disposable UEFI machine with a software TPM2, for testing installs and
# unlock enrollment on a workstation. Needs KVM; no root.
#
#   run-vm.sh STATE_DIR [ISO]
#
# STATE_DIR keeps the disk, UEFI variables and TPM state between runs, so a
# reboot keeps TPM bindings. The disk shows up as /dev/disk/by-id/virtio-vmnode0.
# SSH: 127.0.0.1:2201 -> 22 (system), 127.0.0.1:2202 -> 2222 (initrd).
# Serial console log: STATE_DIR/serial.log. Stop with: kill "$(cat STATE_DIR/qemu.pid)"
set -euo pipefail

state="${1:?usage: run-vm.sh STATE_DIR [ISO]}"
iso="${2:-}"
: "${OVMF_FD:?set OVMF_FD to the OVMF firmware directory (nix build nixpkgs#OVMF.fd)}"

mkdir -p "$state/tpm"
[[ -f "$state/disk.qcow2" ]] || qemu-img create -q -f qcow2 "$state/disk.qcow2" 40G
if [[ ! -f "$state/OVMF_VARS.fd" ]]; then
  install -m 0600 "$OVMF_FD/FV/OVMF_VARS.fd" "$state/OVMF_VARS.fd"
fi

if [[ ! -S "$state/swtpm.sock" ]] || ! pgrep -f "swtpm.*$state/swtpm.sock" >/dev/null; then
  rm -f "$state/swtpm.sock"
  swtpm socket --tpm2 --daemon \
    --tpmstate "dir=$state/tpm" \
    --ctrl "type=unixio,path=$state/swtpm.sock" \
    --pid "file=$state/swtpm.pid"
fi

cdrom=()
[[ -z "$iso" ]] || cdrom=(-drive "file=$iso,media=cdrom,readonly=on" -boot once=d)

qemu-system-x86_64 \
  -enable-kvm -machine q35 -cpu host -smp 4 -m 4096 \
  -drive "if=pflash,format=raw,readonly=on,file=$OVMF_FD/FV/OVMF_CODE.fd" \
  -drive "if=pflash,format=raw,file=$state/OVMF_VARS.fd" \
  -chardev "socket,id=chrtpm,path=$state/swtpm.sock" \
  -tpmdev emulator,id=tpm0,chardev=chrtpm -device tpm-tis,tpmdev=tpm0 \
  -drive "file=$state/disk.qcow2,if=none,id=disk0,discard=unmap" \
  -device virtio-blk-pci,drive=disk0,serial=vmnode0 \
  -netdev "user,id=net0,hostfwd=tcp:127.0.0.1:2201-:22,hostfwd=tcp:127.0.0.1:2202-:2222" \
  -device virtio-net-pci,netdev=net0 \
  "${cdrom[@]}" \
  -display none -serial "file:$state/serial.log" \
  -daemonize -pidfile "$state/qemu.pid"
echo "VM started; serial log: $state/serial.log"
