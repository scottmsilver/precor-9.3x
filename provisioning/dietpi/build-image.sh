#!/usr/bin/env bash
# build-image.sh — produce a configured, ready-to-flash DietPi image for the
# Pi Zero 2 W, fully in userspace (mtools; no loop device, no mount, no root).
# Optional --flash writes to a real SD (the only sudo; guarded).
# set -euo pipefail lives in main() so sourcing for tests is side-effect free.

IMAGE_NAME="DietPi_RPi234-ARMv8-Bookworm.img.xz"
BASE_URL="${BASE_URL:-https://dietpi.com/downloads/images}"

# Parse CLI into ARG_* globals. Unknown flag or missing value => exit 2.
parse_args() {
  ARG_IMAGE_DIR="$HOME/dietpi-build"
  ARG_SECRETS=""; ARG_PUBKEY=""
  ARG_FLASH=""; ARG_IUNDERSTAND=0; ARG_FORCE=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --image-dir) [ $# -ge 2 ] || { echo "usage: --image-dir requires DIR" >&2; exit 2; }; ARG_IMAGE_DIR=$2; shift ;;
      --secrets)   [ $# -ge 2 ] || { echo "usage: --secrets requires FILE" >&2; exit 2; }; ARG_SECRETS=$2; shift ;;
      --pubkey)    [ $# -ge 2 ] || { echo "usage: --pubkey requires FILE" >&2; exit 2; }; ARG_PUBKEY=$2; shift ;;
      --flash)     [ $# -ge 2 ] || { echo "usage: --flash requires /dev/sdX" >&2; exit 2; }; ARG_FLASH=$2; shift ;;
      --i-understand) ARG_IUNDERSTAND=1 ;;
      --force)     ARG_FORCE=1 ;;
      -h|--help)   usage; exit 0 ;;
      *)           echo "unknown argument: $1" >&2; exit 2 ;;
    esac
    shift
  done
}

usage() {
  cat >&2 <<'EOF'
build-image.sh [--image-dir DIR] [--secrets FILE] [--pubkey FILE]
               [--flash /dev/sdX] [--i-understand] [--force]
Builds a configured DietPi image (userspace). --flash optionally writes an SD.
EOF
}

# Verify each given command is on PATH. Accumulates ALL missing tools, then
# prints a single actionable message (with an apt-get hint) and returns non-zero.
require_deps() {
  local missing="" t
  for t in "$@"; do
    command -v "$t" >/dev/null 2>&1 || missing="$missing $t"
  done
  if [ -n "$missing" ]; then
    echo "missing required tools:$missing" >&2
    echo "install them, e.g.: sudo apt-get install mtools util-linux xz-utils curl coreutils" >&2
    return 1
  fi
}

# Byte offset of partition 1 in a regular image file. No root (it is a file).
# partx/sfdisk report START in 512-byte sectors; we multiply by 512. (partx
# -b only changes the SIZE column, not START, so START is always sectors.)
part1_offset_bytes() {
  local img=$1 off="" sectors=""
  [ -f "$img" ] || { echo "image not found: $img" >&2; return 1; }
  if command -v partx >/dev/null 2>&1; then
    sectors=$(partx -g -o START -n 1:1 "$img" 2>/dev/null | tr -dc '0-9') || true
    # 2>/dev/null: silence "integer expression expected" if tr yielded ""
    [ -n "$sectors" ] && [ "$sectors" -gt 0 ] 2>/dev/null && off=$(( sectors * 512 ))
  fi
  if [ -z "$off" ] && command -v sfdisk >/dev/null 2>&1; then
    # sfdisk -d pads with spaces: "dev1 : start=    2048, size=..." — grab the
    # first integer of partition 1's line; exit ensures we never read p2/p3.
    sectors=$(sfdisk -d "$img" 2>/dev/null \
      | awk -F'start=' '/^[^#].*start=/{ match($2,/[0-9]+/); print substr($2,RSTART,RLENGTH); exit }')
    [ -n "$sectors" ] && off=$(( sectors * 512 ))
  fi
  [ -n "$off" ] && [ "$off" -gt 0 ] 2>/dev/null \
    || { echo "could not read partition 1 offset from $img" >&2; return 1; }
  printf '%s\n' "$off"
}

# True iff path is a block-special device.
is_block_device() { [ -b "$1" ]; }

# True iff /sys says the whole-disk device is removable. Sysfs root is
# overridable for tests.
is_removable() {
  local name=$1 f
  f="${SYSFS_ROOT:-/sys}/block/$name/removable"
  [ -r "$f" ] && [ "$(cat "$f" 2>/dev/null)" = "1" ]
}

# Whole-disk device backing "/", e.g. "sda" / "mmcblk0" / "nvme0n1".
root_disk() {
  local src base
  src=$(findmnt -no SOURCE / 2>/dev/null) || src=""
  base=$(lsblk -no PKNAME "$src" 2>/dev/null | head -1)
  [ -z "$base" ] && base=$(lsblk -no NAME "$src" 2>/dev/null | head -1)
  printf '%s\n' "$base"
}

# True iff DEV is the system disk or one of its partitions (refuse to flash it).
# Fails CLOSED: if the root disk can't be determined (e.g. /dev/root on a Pi
# running DietPi, overlayfs, NFS root) treat the target as dangerous and block.
# Over-refusal (e.g. an unrelated nvme0n1<N> namespace matching nvme0n1[0-9]*)
# is the safe direction for a destructive guard. Also blocks the same-chip
# eMMC hw-managed partitions (mmcblk0boot*, mmcblk0rpmb).
is_system_disk() {
  local dev=${1#/dev/} rd; rd=$(root_disk)
  [ -n "$rd" ] || return 0
  case "$dev" in
    "$rd"|"$rd"[0-9]*|"$rd"p[0-9]*|"$rd"boot[0-9]*|"$rd"rpmb) return 0 ;;
  esac
  return 1
}

# Read one line from stdin; succeed only if it exactly equals the arg.
confirm_matches() {
  local want=$1 got=""
  IFS= read -r got || true
  [ "$got" = "$want" ]
}

# Extract the sha256 whose filename field equals exactly $IMAGE_NAME (S2):
# trust ONLY that hash, never whatever name the sidecar happens to list.
# Prints the 64-hex hash on stdout; empty + rc 1 if no such line exists.
# sha256sum format: "<64hex>␠␠<name>" (text) or "<64hex>␠*<name>" (binary).
pinned_sha256_hash() {
  local shaf=$1 h
  h=$(awk -v want="$IMAGE_NAME" '
    { hh=$1; sub(/^[0-9a-fA-F]+[ ]+[ *]?/, "", $0);
      if (length(hh)==64 && hh ~ /^[0-9a-fA-F]+$/ && $0==want) { print hh; exit } }
  ' "$shaf" 2>/dev/null)
  [ -n "$h" ] || return 1
  printf '%s\n' "$h"
}

# Verify $IMAGE_NAME in $dir against the filename-pinned hash. We build the
# check input ourselves so a sidecar pointing at a different (attacker-chosen)
# file cannot satisfy verification. rc 0 ok, rc 1 hash mismatch / missing file.
verify_pinned_sha256() {
  local dir=$1 expected
  expected=$(pinned_sha256_hash "$dir/$IMAGE_NAME.sha256") || return 1
  printf '%s  %s\n' "$expected" "$IMAGE_NAME" | ( cd "$dir" && sha256sum -c - ) >/dev/null 2>&1
}

# Download (cached) + sha256 -c + decompress into <dir>. force=1 allows
# overwriting an existing .img. Uses curl, which supports file:// for tests.
# The sha256 sidecar is filename-pinned to the bare IMAGE_NAME (S2).
# curl -fsSL: silent meter, real errors kept.
fetch_and_verify() {
  local dir=$1 force=$2
  mkdir -p "$dir"
  local xzf="$dir/$IMAGE_NAME" shaf="$dir/$IMAGE_NAME.sha256"
  local img="$dir/${IMAGE_NAME%.xz}"
  if [ -e "$img" ] && [ "$force" != "1" ]; then
    echo "refusing to overwrite existing image: $img (use --force)" >&2
    return 1
  fi
  if ! { [ -s "$xzf" ] && [ -s "$shaf" ] && verify_pinned_sha256 "$dir"; }; then
    curl -fsSL --retry 2 -o "$xzf"  "$BASE_URL/$IMAGE_NAME"        || { echo "download failed: $BASE_URL/$IMAGE_NAME" >&2; return 1; }
    curl -fsSL --retry 2 -o "$shaf" "$BASE_URL/$IMAGE_NAME.sha256" || { echo "checksum download failed" >&2; return 1; }
  fi
  pinned_sha256_hash "$shaf" >/dev/null \
    || { echo "checksum sidecar does not list $IMAGE_NAME" >&2; return 1; }
  verify_pinned_sha256 "$dir" \
    || { echo "SHA256 verification FAILED for $xzf" >&2; return 1; }
  # Atomic: decompress to a temp then mv, so a failed xz never leaves a
  # truncated .img that would block the next run with the clobber guard.
  local tmp="$img.tmp.$$"
  xz -dk -c "$xzf" > "$tmp" || { rm -f "$tmp"; echo "decompress failed" >&2; return 1; }
  mv -f "$tmp" "$img"
  # S3: the .img embeds the WiFi PSK + device password + SSH key; the .xz /
  # .sha256 are the same payload. Lock them and the dir down (idempotent,
  # tolerant if a path does not exist yet — e.g. cache-only sidecar).
  chmod 700 "$dir" 2>/dev/null || true
  local f
  for f in "$img" "$xzf" "$shaf"; do [ -e "$f" ] && chmod 600 "$f" 2>/dev/null || true; done
  printf '%s\n' "$img"
}

# Configure the FAT partition inside $img at byte $off, fully in userspace:
# extract the stock config.txt, run the audited prepare-sd.sh against a temp
# staging dir, then write the three generated files back via mtools.
stage_and_inject() {
  local img=$1 off=$2 secrets=$3 pubkey=$4 template=$5
  local dir="${DIETPI_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
  local stage; stage=$(mktemp -d)
  # Self-removing: a RETURN trap set in a function is NOT function-scoped in
  # bash — without this `trap - RETURN` it would re-fire when the *caller*
  # (main) returns, with $stage unbound under set -u, failing a good build.
  trap 'rm -rf "$stage"; trap - RETURN' RETURN
  export MTOOLS_SKIP_CHECK=1

  mcopy -i "$img@@$off" ::config.txt "$stage/" 2>/dev/null \
    || { echo "could not read config.txt from image (not a DietPi boot partition?)" >&2; return 1; }

  local args=( "$stage" )
  [ -n "$secrets" ]  && args+=( --secrets  "$secrets" )
  [ -n "$pubkey" ]   && args+=( --pubkey   "$pubkey" )
  [ -n "$template" ] && args+=( --template "$template" )
  "$dir/prepare-sd.sh" "${args[@]}" >/dev/null \
    || { echo "prepare-sd.sh refused — not writing image" >&2; return 1; }

  mcopy -o -i "$img@@$off" \
    "$stage/dietpi.txt" "$stage/dietpi-wifi.txt" "$stage/Automation_Custom_Script.sh" :: \
    || { echo "writing config into image failed" >&2; return 1; }
  # Fast-boot artifacts (prepare-sd.sh produced the tarball in the stage dir).
  if [ -f "$stage/fastboot.tgz" ]; then
    mcopy -o -i "$img@@$off" "$stage/fastboot.tgz" :: \
      || { echo "writing fastboot.tgz into image failed" >&2; return 1; }
  fi
  if [ -f "$stage/family.tgz" ]; then
    mcopy -o -i "$img@@$off" "$stage/family.tgz" :: \
      || { echo "writing family.tgz into image failed" >&2; return 1; }
  fi
}

# lsblk TYPE must be exactly "disk" (S4). Fails CLOSED: if lsblk errors OR
# the type is anything else (part/loop/rom/empty) the caller refuses. This
# rejects partitions like /dev/sdb1 and any non-whole-disk target.
dev_type_is_disk() {
  local dev=$1 t rc
  t=$(lsblk -ndo TYPE -- "$dev" 2>/dev/null); rc=$?
  [ "$rc" -eq 0 ] && [ "$t" = "disk" ]
}

# True iff the device has any mounted partition. Fails CLOSED: an lsblk error
# (rc != 0) is treated as "mounted/unknown" so the caller refuses — an error
# must never be misread as "not mounted" on a destructive path (S5).
dev_has_mounts_or_unknown() {
  local dev=$1 out rc
  out=$(lsblk -no MOUNTPOINT -- "$dev" 2>/dev/null); rc=$?
  [ "$rc" -ne 0 ] && return 0
  printf '%s' "$out" | grep -q .
}

# Guarded raw write to a real SD. Args: img dev i_understand. Returns non-zero
# (refuses) unless ALL guards pass; only then does it sudo dd. Confirmation is
# read from stdin (typed device path). Guard order (S4+S5): canonicalize the
# device first (resolve /dev/disk/by-id aliases), then every check runs on the
# CANONICAL path, type must be exactly "disk", lsblk errors fail CLOSED, and
# the destructive preconditions are re-asserted immediately before dd to close
# the TOCTOU window between confirmation and the write.
flash_image() {
  local img=$1 dev=$2 i_understand=$3
  # 1. Canonicalize (resolves /dev/disk/by-id/... and other symlinks).
  local cdev; cdev=$(readlink -f -- "$dev" 2>/dev/null) || cdev=""
  [ -n "$cdev" ] || { echo "cannot resolve device path (not a block device): $dev" >&2; return 1; }
  dev=$cdev
  local name; name=$(basename -- "$dev")
  # 2. Must be a block device.
  is_block_device "$dev" || { echo "not a block device: $dev" >&2; return 1; }
  # 3. lsblk TYPE must be exactly "disk" (rejects /dev/sdb1; fail CLOSED).
  dev_type_is_disk "$dev" || { echo "refusing: $dev is not a whole disk (type != disk) — refusing" >&2; return 1; }
  # 4. loop/ram/zram refusal on the CANONICAL kernel name.
  case "$name" in loop*|ram*|zram*) echo "refusing loop/ram/zram device: $dev" >&2; return 1 ;; esac
  # 5. System / root-disk refusal on the canonical dev.
  if is_system_disk "$dev"; then echo "refusing the system disk: $dev" >&2; return 1; fi
  # 6. Non-removable requires --i-understand (canonical dev).
  local base; base=$(lsblk -ndo PKNAME "$dev" 2>/dev/null); [ -z "$base" ] && base=$name
  if ! is_removable "$base"; then
    [ "$i_understand" = "1" ] || { echo "refusing non-removable $dev (pass --i-understand to override)" >&2; return 1; }
  fi
  # 7. Mounted-partitions check; an lsblk error fails CLOSED (refuse).
  if dev_has_mounts_or_unknown "$dev"; then
    echo "refusing: $dev has mounted partitions — unmount first" >&2; return 1
  fi
  # 8. Show summary; typed confirmation must match the CANONICAL dev string.
  echo "About to OVERWRITE this device:" >&2
  lsblk -dno NAME,MODEL,SIZE,TRAN,RM "$dev" >&2 2>/dev/null || true
  echo "Type the exact device path to confirm (or anything else to abort):" >&2
  confirm_matches "$dev" || { echo "confirmation did not match — aborted" >&2; return 1; }
  # 9. Pre-flight sudo.
  echo "Pre-flighting sudo (required only to write the raw device $dev) ..." >&2
  sudo -v || { echo "sudo authentication failed — aborted" >&2; return 1; }
  # 10. TOCTOU close: re-resolve + re-assert type==disk and not-mounted on the
  # canonical dev immediately before dd. Any change/failure aborts before dd.
  local rdev; rdev=$(readlink -f -- "$dev" 2>/dev/null) || rdev=""
  [ -n "$rdev" ] && [ "$rdev" = "$dev" ] \
    || { echo "device path changed before write — aborted" >&2; return 1; }
  dev_type_is_disk "$dev" \
    || { echo "device is no longer a whole disk before write — aborted" >&2; return 1; }
  if dev_has_mounts_or_unknown "$dev"; then
    echo "device gained a mount before write — aborted" >&2; return 1
  fi
  echo "Flashing $img -> $dev (sudo) ..." >&2
  sudo dd if="$img" of="$dev" bs=4M conv=fsync status=progress && sudo sync
}

main() {
  set -euo pipefail
  # S3: build artifacts embed WiFi PSK + device password + SSH key. Restrict
  # every file/dir we create to the owner before anything is written.
  umask 077
  parse_args "$@"
  # mdir/mformat are listed alongside mcopy so a partial mtools install (only
  # some applets on PATH) is caught. ssh-keygen: the build path runs
  # prepare-sd.sh, which requires it to validate the SSH public key (D1).
  local deps="curl xz sha256sum mcopy mdir mformat ssh-keygen"
  command -v sfdisk >/dev/null 2>&1 || deps="$deps partx"
  [ -n "$ARG_FLASH" ] && deps="$deps lsblk sudo"
  require_deps $deps
  local img; img=$(fetch_and_verify "$ARG_IMAGE_DIR" "$ARG_FORCE") \
    || { echo "fetch_and_verify failed" >&2; return 1; }
  local off; off=$(part1_offset_bytes "$img") \
    || { echo "part1_offset_bytes failed" >&2; return 1; }
  stage_and_inject "$img" "$off" "$ARG_SECRETS" "$ARG_PUBKEY" ""
  echo "Configured image ready: $img"
  if [ -n "$ARG_FLASH" ]; then
    flash_image "$img" "$ARG_FLASH" "$ARG_IUNDERSTAND"
  else
    echo "To flash: re-run with --flash /dev/sdX, or: sudo dd if=$img of=/dev/sdX bs=4M conv=fsync status=progress"
  fi
}

if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
  main "$@"
fi
