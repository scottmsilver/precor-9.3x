#!/usr/bin/env bash
# Dependency-free unit tests for build-image.sh. Exit non-zero on first failure.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
fail() { echo "FAIL: $1" >&2; exit 1; }
pass() { echo "ok: $1"; }

# (1) Side-effect-free check: a clean subshell that only sources the script
#     must produce no output and exit 0 (main must NOT auto-run on source).
out=$(bash -c 'source "$1"' _ "$HERE/../build-image.sh" 2>&1) \
  || fail "sourcing build-image.sh errored: $out"
[ -z "$out" ] || fail "sourcing build-image.sh produced output: $out"

# (2) Source into THIS shell (the supported pattern, mirrors test_lib.sh) so
#     later test blocks can call build-image.sh functions directly.
# shellcheck source=/dev/null
source "$HERE/../build-image.sh"
type -t main >/dev/null 2>&1 || fail "main not defined after source"
pass "build-image.sh sources cleanly without running main"

# --- parse_args: defaults, overrides, errors ---
( set -u
  parse_args --image-dir /tmp/x --secrets s.env --pubkey k.pub
  [ "$ARG_IMAGE_DIR" = /tmp/x ] || exit 1
  [ "$ARG_SECRETS" = s.env ] || exit 1
  [ "$ARG_PUBKEY" = k.pub ] || exit 1
  [ "$ARG_FLASH" = "" ] || exit 1
  [ "$ARG_IUNDERSTAND" = 0 ] || exit 1
  [ "$ARG_FORCE" = 0 ] || exit 1
) || fail "parse_args defaults/overrides wrong"
( parse_args --flash /dev/sdZ --i-understand --force
  [ "$ARG_FLASH" = /dev/sdZ ] || exit 1
  [ "$ARG_IUNDERSTAND" = 1 ] || exit 1
  [ "$ARG_FORCE" = 1 ] || exit 1 ) || fail "parse_args flash flags wrong"
( parse_args --image-dir 2>/dev/null ) && fail "missing value not rejected"
rc=0; ( parse_args --image-dir 2>/dev/null ) || rc=$?
[ "$rc" -eq 2 ] || fail "missing value should exit 2 (got $rc)"
( parse_args --bogus 2>/dev/null ) && fail "unknown flag not rejected"
pass "parse_args"

# --- require_deps: present tools pass; a bogus tool fails with hint ---
require_deps bash sh || fail "require_deps rejected present tools"
rc=0; ( require_deps definitely-not-a-real-binary-xyz 2>/dev/null ) || rc=$?
[ "$rc" -ne 0 ] || fail "require_deps accepted a missing tool"
msg=$( require_deps mtools-bogus-mcopy 2>&1 || true )
case "$msg" in *"apt-get install"*) ;; *) fail "missing-dep message lacks install hint: $msg" ;; esac
msg=$( require_deps bogus-tool-alpha bogus-tool-beta 2>&1 || true )
case "$msg" in *bogus-tool-alpha*bogus-tool-beta*) ;; *) fail "require_deps did not accumulate all missing tools: $msg" ;; esac
pass "require_deps"

# --- part1_offset_bytes: read partition-1 start (bytes) from a regular file ---
timg=$(mktemp); truncate -s 16M "$timg"
echo 'start=2048, size=20480, type=c' | sfdisk -q "$timg" >/dev/null 2>&1 \
  || fail "could not create test partition table (sfdisk)"
off=$(part1_offset_bytes "$timg") || fail "part1_offset_bytes returned non-zero"
[ "$off" = "1048576" ] || fail "expected offset 1048576 (2048*512), got [$off]"
rm -f "$timg"
( part1_offset_bytes /nonexistent.img 2>/dev/null ) && fail "missing image not rejected"
# sfdisk fallback must work when partx yields nothing (force via a stub fn,
# which command -v still finds, so the partx branch runs but produces no value)
timg2=$(mktemp); truncate -s 16M "$timg2"
echo 'start=2048, size=20480, type=c' | sfdisk -q "$timg2" >/dev/null 2>&1 \
  || fail "could not create sfdisk-fallback test partition table"
off2=$( partx() { return 1; }; part1_offset_bytes "$timg2" ) \
  || fail "sfdisk fallback returned non-zero"
[ "$off2" = "1048576" ] || fail "sfdisk fallback wrong offset: got [$off2]"
rm -f "$timg2"
pass "part1_offset_bytes"

# --- flash guards (pure, mockable) ---
is_block_device /etc/hostname && fail "regular file treated as block device"
is_block_device /dev/null && fail "/dev/null (char) treated as block device"

# is_removable reads ${SYSFS_ROOT}/block/<name>/removable
sr=$(mktemp -d); mkdir -p "$sr/block/sdZ"
echo 1 > "$sr/block/sdZ/removable"
SYSFS_ROOT="$sr" is_removable sdZ || fail "removable=1 not detected"
echo 0 > "$sr/block/sdZ/removable"
SYSFS_ROOT="$sr" is_removable sdZ && fail "removable=0 treated as removable"
rm -rf "$sr"

# is_system_disk uses root_disk(); override it for the test
root_disk() { echo sda; }
is_system_disk /dev/sda  || fail "system disk /dev/sda not flagged"
is_system_disk /dev/sda1 || fail "partition of system disk not flagged"
is_system_disk /dev/sdZ  && fail "non-system disk wrongly flagged"
unset -f root_disk

# confirm_matches: stdin must equal the expected device path exactly
echo "/dev/sdZ" | confirm_matches /dev/sdZ || fail "exact match rejected"
echo "/dev/sdY" | confirm_matches /dev/sdZ && fail "mismatch accepted"
printf '' | confirm_matches /dev/sdZ && fail "empty confirm accepted"
printf '/dev/sdZ\r\n' | confirm_matches /dev/sdZ && fail "CRLF confirm accepted"

# fail CLOSED: unknown root disk must be treated as the system disk (block)
root_disk() { echo; }
is_system_disk /dev/sda || fail "empty root_disk must fail CLOSED (block)"
unset -f root_disk
# same physical eMMC: hw-managed boot/rpmb partitions are blocked
root_disk() { echo mmcblk0; }
is_system_disk /dev/mmcblk0boot0 || fail "mmcblk0boot0 not flagged as system"
is_system_disk /dev/mmcblk0rpmb  || fail "mmcblk0rpmb not flagged as system"
unset -f root_disk
# safe over-refusal: an unrelated nvme namespace still blocks (acceptable)
root_disk() { echo nvme0n1; }
is_system_disk /dev/nvme0n11 || fail "nvme0n1[0-9]* expected safe over-refusal"
unset -f root_disk
pass "flash guards"

# --- fetch_and_verify: uses a local BASE_URL (file://); checksum + clobber ---
fv=$(mktemp -d); src="$fv/src"; mkdir -p "$src"
printf 'FAKEIMG' | xz -c > "$src/$IMAGE_NAME"
( cd "$src" && sha256sum "$IMAGE_NAME" > "$IMAGE_NAME.sha256" )
dst="$fv/out"; mkdir -p "$dst"
BASE_URL="file://$src" fetch_and_verify "$dst" 0 || fail "valid fetch+verify failed"
[ -f "$dst/${IMAGE_NAME%.xz}" ] || fail "decompressed .img not produced"
# Re-run without --force must refuse to clobber the existing .img
BASE_URL="file://$src" fetch_and_verify "$dst" 0 2>/dev/null \
  && fail "clobber not refused without --force"
BASE_URL="file://$src" fetch_and_verify "$dst" 1 || fail "--force re-decompress failed"
# Corrupt the checksum -> must fail
printf 'deadbeef  %s\n' "$IMAGE_NAME" > "$src/$IMAGE_NAME.sha256"
rm -f "$dst/${IMAGE_NAME%.xz}" "$dst/$IMAGE_NAME"
BASE_URL="file://$src" fetch_and_verify "$dst" 0 2>/dev/null \
  && fail "bad checksum accepted"
# cache hit: a valid cached xz+sha succeeds even with a dead BASE_URL
cdir="$fv/cache"; mkdir -p "$cdir"
cp "$src/$IMAGE_NAME" "$cdir/$IMAGE_NAME"
( cd "$cdir" && sha256sum "$IMAGE_NAME" > "$IMAGE_NAME.sha256" )
BASE_URL="file:///nonexistent-dead-dir" fetch_and_verify "$cdir" 0 \
  || fail "cache hit should succeed without re-download"
[ -f "$cdir/${IMAGE_NAME%.xz}" ] || fail "cache hit did not produce .img"
# S3: secret-bearing build artifacts must not be world-readable.
[ "$(stat -c '%a' "$cdir")" = "700" ] \
  || fail "S3: build dir not mode 700 (got $(stat -c '%a' "$cdir"))"
[ "$(stat -c '%a' "$cdir/${IMAGE_NAME%.xz}")" = "600" ] \
  || fail "S3: .img not mode 600 (got $(stat -c '%a' "$cdir/${IMAGE_NAME%.xz}"))"
[ "$(stat -c '%a' "$cdir/$IMAGE_NAME")" = "600" ] \
  || fail "S3: .xz not mode 600 (got $(stat -c '%a' "$cdir/$IMAGE_NAME"))"
[ "$(stat -c '%a' "$cdir/$IMAGE_NAME.sha256")" = "600" ] \
  || fail "S3: .sha256 not mode 600 (got $(stat -c '%a' "$cdir/$IMAGE_NAME.sha256"))"
# S2: sidecar that lists ONLY a DIFFERENT filename (valid 64-hex hash) must
# be rejected — the hash must be pinned to the bare IMAGE_NAME, not trusted
# for whatever filename the sidecar names.
s2=$(mktemp -d); s2src="$s2/src"; mkdir -p "$s2src"
printf 'FAKEIMG' | xz -c > "$s2src/$IMAGE_NAME"
realhash=$(sha256sum "$s2src/$IMAGE_NAME" | cut -d' ' -f1)
printf '%s  OTHER.img\n' "$realhash" > "$s2src/$IMAGE_NAME.sha256"
s2dst="$s2/out"; mkdir -p "$s2dst"
s2err=$(BASE_URL="file://$s2src" fetch_and_verify "$s2dst" 0 2>&1 >/dev/null); s2rc=$?
[ "$s2rc" -ne 0 ] || fail "S2: sidecar listing only OTHER.img must be rejected"
case "$s2err" in *"does not list $IMAGE_NAME"*) ;; *) fail "S2: wrong refusal msg: [$s2err]" ;; esac
rm -rf "$s2"
rm -rf "$fv"
pass "fetch_and_verify"

# --- stage_and_inject: configure a real FAT image via mtools, no root ---
command -v mformat >/dev/null 2>&1 || fail "mtools (mformat) required for this test"
si=$(mktemp -d); img="$si/t.img"; truncate -s 48M "$img"
echo 'start=2048, size=90112, type=c' | sfdisk -q "$img" >/dev/null 2>&1 \
  || fail "sfdisk failed"
off=$(part1_offset_bytes "$img")
MTOOLS_SKIP_CHECK=1 mformat -i "$img@@$off" -F :: 2>/dev/null || fail "mformat failed"
printf 'gpu_mem=16\n' > "$si/config.txt"
MTOOLS_SKIP_CHECK=1 mcopy -o -i "$img@@$off" "$si/config.txt" :: || fail "seed config.txt failed"

# real secrets + a real throwaway keypair + frozen template fixture
cat > "$si/secrets.env" <<'EOS'
WIFI_SSID="Net24"
WIFI_PSK="correcthorsebattery"
WIFI_KEYMGR="WPA-PSK"
GLOBAL_PASSWORD="s3cret-not-default"
EOS
ssh-keygen -t ed25519 -N '' -q -f "$si/k" </dev/null
tmpl="$HERE/fixtures/template-sample.txt"

DIETPI_DIR="$HERE/.." \
  stage_and_inject "$img" "$off" "$si/secrets.env" "$si/k.pub" "$tmpl" \
  || fail "stage_and_inject failed"

list=$(MTOOLS_SKIP_CHECK=1 mdir -i "$img@@$off" :: 2>/dev/null)
echo "$list" | grep -qi 'dietpi'   || fail "dietpi.txt not written into image"
echo "$list" | grep -qi 'AUTOMAT' || echo "$list" | grep -qi 'Automation' \
  || fail "Automation_Custom_Script.sh not written into image"
MTOOLS_SKIP_CHECK=1 mcopy -i "$img@@$off" ::dietpi.txt "$si/back.txt" 2>/dev/null \
  || fail "could not read back dietpi.txt"
grep -qE '^[A-Za-z_]+=__INJECTED__' "$si/back.txt" && fail "placeholder survived into image"
grep -q '^AUTO_SETUP_SSH_PUBKEY=ssh-' "$si/back.txt" || fail "pubkey not injected in image"
# Prove the exact long filenames (not 8.3-mangled) — DietPi reads them by name
MTOOLS_SKIP_CHECK=1 mcopy -i "$img@@$off" ::dietpi-wifi.txt "$si/back_wifi.txt" 2>/dev/null \
  || fail "could not read back dietpi-wifi.txt by exact LFN (8.3-mangled?)"
MTOOLS_SKIP_CHECK=1 mcopy -i "$img@@$off" ::Automation_Custom_Script.sh "$si/back_auto.sh" 2>/dev/null \
  || fail "could not read back Automation_Custom_Script.sh by exact LFN (8.3-mangled?)"
rm -rf "$si"
pass "stage_and_inject writes injected config into the image (userspace)"

# --- flash_image: guards refuse with the RIGHT reason; dd only after all pass ---
type -t flash_image >/dev/null 2>&1 || fail "flash_image not defined"
fimg=$(mktemp); printf x > "$fimg"

# refuses <expected-stderr-substr> <stdin> -- <flash_image args...>
# Asserts flash_image exits non-zero AND the refusal names the right guard
# (so an unrelated early error can't masquerade as the intended refusal).
refuses() {
  local want=$1 stdin=$2; shift 2
  local err rc
  err=$(printf '%s' "$stdin" | flash_image "$@" 2>&1 >/dev/null); rc=$?
  [ "$rc" -ne 0 ] || fail "flash_image accepted (rc 0) but must refuse: $*"
  case "$err" in *"$want"*) ;; *) fail "wrong refusal: want [$want] got [$err]" ;; esac
}

# S4: a path that cannot be canonicalized (parent dir absent) is refused.
refuses "not a block device" "" "$fimg" /no/such/dev 0
is_block_device() { return 0; }                 # pretend the target is a block dev
# Default lsblk stub for the guard cases: TYPE -> disk (so the type==disk
# guard passes and each case reaches its INTENDED guard), MOUNTPOINT -> empty,
# PKNAME -> empty. Individual cases override this where they must.
lsblk() {
  case "$1" in
    -ndo) case "$2" in TYPE) echo disk ;; PKNAME) : ;; esac ;;
    -no)  : ;;        # MOUNTPOINT: no mounts
    -dno) : ;;        # device summary: quiet
  esac
}
refuses "refusing loop/ram" "" "$fimg" /dev/loop9 0
root_disk() { echo sda; }
refuses "refusing the system disk" "" "$fimg" /dev/sda 0
root_disk() { echo zzz; }                        # not the system disk
is_removable() { return 1; }
refuses "non-removable" "" "$fimg" /dev/sdZ 0
is_removable() { return 0; }
refuses "confirmation did not match" "/dev/WRONG" "$fimg" /dev/sdZ 1
# S4: a partition (TYPE=part) must be refused (fail closed on non-disk).
lsblk() { case "$1" in -ndo) case "$2" in TYPE) echo part ;; esac ;; esac; }
refuses "not a whole disk" "" "$fimg" /dev/sdZ1 1
# mounted partition on the target -> refused (before confirm/dd). lsblk stub:
# TYPE -> disk, PKNAME -> empty (base=sdZ), MOUNTPOINT -> a mountpoint line.
lsblk() {
  case "$1" in
    -ndo) case "$2" in TYPE) echo disk ;; esac ;;
    -no)  echo "/media/usb" ;;
  esac
}
refuses "has mounted partitions" "" "$fimg" /dev/sdZ 1
# S5: lsblk ERROR on the mounted check must fail CLOSED (refuse), never proceed.
lsblk() {
  case "$1" in
    -ndo) case "$2" in TYPE) echo disk ;; esac ;;
    -no)  return 3 ;;            # MOUNTPOINT query errors out
  esac
}
refuses "has mounted partitions" "" "$fimg" /dev/sdZ 1
unset -f lsblk

# HAPPY PATH: all guards pass + exact confirmation -> reaches dd (sudo stubbed
# so NO real device is ever touched). Proves guards don't false-refuse. lsblk
# returns TYPE=disk + no mounts for BOTH the pre-check and the post-sudo
# TOCTOU recheck.
lsblk() {
  case "$1" in
    -ndo) case "$2" in TYPE) echo disk ;; PKNAME) : ;; esac ;;
    -no)  : ;;
    -dno) : ;;
  esac
}
sudo() { echo "SUDO:$*"; }                       # never touch a real device
out=$(printf '/dev/sdZ\n' | flash_image "$fimg" /dev/sdZ 1 2>/dev/null); rc=$?
[ "$rc" -eq 0 ] || fail "happy path should succeed (rc=$rc)"
case "$out" in
  *"SUDO:dd if=$fimg of=/dev/sdZ"*) ;;
  *) fail "dd not invoked on happy path: [$out]" ;;
esac
case "$out" in
  *"SUDO:-v"*) ;;
  *) fail "sudo -v pre-flight not invoked before dd: [$out]" ;;
esac

# S4: symlink alias — readlink -f resolves an alias to the stub disk; the
# happy path still works (canonicalization, type==disk, no mounts, post recheck).
ad=$(mktemp -d); : > "$ad/sdZ"; ln -s "$ad/sdZ" "$ad/by-id-alias"
out=$(printf '%s\n' "$ad/sdZ" | flash_image "$fimg" "$ad/by-id-alias" 1 2>/dev/null); rc=$?
[ "$rc" -eq 0 ] || fail "S4: symlink-alias happy path should succeed (rc=$rc)"
case "$out" in
  *"SUDO:dd if=$fimg of=$ad/sdZ"*) ;;
  *) fail "S4: dd not invoked on canonical dev for symlink alias: [$out]" ;;
esac
rm -rf "$ad"
unset -f is_block_device is_removable root_disk lsblk sudo
rm -f "$fimg"
pass "flash_image: guards refuse with correct reason; dd reached only when all pass"

# --- D1: main's dep set must include ssh-keygen (build path runs prepare-sd.sh
#     which requires ssh-keygen to validate the SSH public key). Capture the
#     deps string by stubbing require_deps to print its args and abort. ---
deps_seen=$(
  require_deps() { echo "DEPS:$*"; return 1; }
  main 2>/dev/null
  true
)
case "$deps_seen" in
  *"DEPS:"*ssh-keygen*) ;;
  *) fail "D1: main deps must include ssh-keygen, got [$deps_seen]" ;;
esac
pass "D1: main requires ssh-keygen"

# --- main: --help exits 0; bad flag exits 2 (no network/disk touched) ---
( main --help >/dev/null 2>&1 ) || fail "main --help should exit 0"
rc=0; ( main --nope 2>/dev/null ) || rc=$?
[ "$rc" -eq 2 ] || fail "main bad flag should exit 2 (got $rc)"
pass "main arg surface"

echo "ALL TESTS PASSED"
