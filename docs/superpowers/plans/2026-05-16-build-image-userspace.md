# build-image.sh (Userspace DietPi Image Builder) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **PROJECT RULE — COMMITS:** No `git commit` until the repo owner authorizes it (a password). Every "Commit" step means: run the `git add` as written, then STOP and ask the owner to authorize. Never `git push`.

**Goal:** A `provisioning/dietpi/build-image.sh` that turns the stock DietPi image into a fully-configured, ready-to-flash `.img` with zero root for the build (via `mtools`), reusing the audited `prepare-sd.sh`, plus an opt-in heavily-guarded `--flash`.

**Architecture:** Pure/guard functions are defined at file top and are unit-testable by `source`-ing the script (a `[ "${BASH_SOURCE[0]}" = "$0" ]` guard prevents `main` from running on source; `set -euo pipefail` lives *inside* `main`, not at file top, so sourcing doesn't mutate the test shell). `mtools` (`mcopy`/`mformat`/`mdir`) reads/writes the FAT partition at a byte offset inside the regular `.img` — no loop device, no mount, no root. Credential injection is delegated unchanged to `prepare-sd.sh` against a temp staging dir. Only the optional `--flash` path uses `sudo` (for `dd` to a real disk), behind strong guards.

**Tech Stack:** Bash, `mtools`, util-linux (`sfdisk`/`partx`/`lsblk`), `curl`, `xz`, `sha256sum`; dependency-free bash test harness.

---

## File Structure

```
provisioning/dietpi/
  build-image.sh                 # NEW — orchestration + pure guard fns (sourceable for tests)
  tests/test_build_image.sh      # NEW — dependency-free harness for build-image.sh
  prepare-sd.sh                  # UNCHANGED — reused via subprocess
  lib.sh, dietpi.txt, ...        # UNCHANGED
```

`build-image.sh` keeps one responsibility (orchestrate fetch→stage→inject→optional-flash). No build logic leaks into `prepare-sd.sh`. `test_build_image.sh` mirrors the existing `test_lib.sh` style (`fail`/`pass`, ends with `echo "ALL TESTS PASSED"`, exit non-zero on first failure) and is separate from `test_lib.sh` because it tests a different component.

---

### Task 1: Skeleton + sourceable test harness

**Files:**
- Create: `provisioning/dietpi/build-image.sh`
- Create: `provisioning/dietpi/tests/test_build_image.sh`

- [ ] **Step 1: Write the failing test**

Create `provisioning/dietpi/tests/test_build_image.sh`:

```bash
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

echo "ALL TESTS PASSED"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bash provisioning/dietpi/tests/test_build_image.sh`
Expected: FAIL — `sourcing build-image.sh errored` (file does not exist).

- [ ] **Step 3: Write minimal implementation**

Create `provisioning/dietpi/build-image.sh`:

```bash
#!/usr/bin/env bash
# build-image.sh — produce a configured, ready-to-flash DietPi image for the
# Pi Zero 2 W, fully in userspace (mtools; no loop device, no mount, no root).
# Optional --flash writes to a real SD (the only sudo; guarded).
# set -euo pipefail lives in main() so sourcing for tests is side-effect free.

IMAGE_NAME="DietPi_RPi234-ARMv8-Bookworm.img.xz"
BASE_URL="${BASE_URL:-https://dietpi.com/downloads/images}"

main() {
  set -euo pipefail
  echo "build-image: not yet implemented" >&2
  return 0
}

if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
  main "$@"
fi
```

- [ ] **Step 4: Run test to verify it passes**

Run: `bash provisioning/dietpi/tests/test_build_image.sh`
Expected: `ok: build-image.sh sources cleanly without running main` then `ALL TESTS PASSED`.

- [ ] **Step 5: Commit**

```bash
git add provisioning/dietpi/build-image.sh provisioning/dietpi/tests/test_build_image.sh
# then ask the owner to authorize: git commit -m "feat(provisioning): build-image.sh skeleton + test harness"
```

---

### Task 2: `parse_args`

**Files:**
- Modify: `provisioning/dietpi/build-image.sh`
- Test: `provisioning/dietpi/tests/test_build_image.sh`

- [ ] **Step 1: Write the failing test** — append before the final `echo "ALL TESTS PASSED"`:

```bash
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bash provisioning/dietpi/tests/test_build_image.sh`
Expected: FAIL — `parse_args: command not found` / defaults wrong.

- [ ] **Step 3: Write minimal implementation** — add to `build-image.sh` (above `main`):

```bash
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `bash provisioning/dietpi/tests/test_build_image.sh`
Expected: `ok: parse_args` then `ALL TESTS PASSED`.

- [ ] **Step 5: Commit**

```bash
git add provisioning/dietpi/build-image.sh provisioning/dietpi/tests/test_build_image.sh
# then ask the owner to authorize: git commit -m "feat(provisioning): build-image.sh arg parsing"
```

---

### Task 3: `require_deps`

**Files:**
- Modify: `provisioning/dietpi/build-image.sh`
- Test: `provisioning/dietpi/tests/test_build_image.sh`

- [ ] **Step 1: Write the failing test** — append before the final echo:

```bash
# --- require_deps: present tools pass; a bogus tool fails with hint ---
require_deps bash sh || fail "require_deps rejected present tools"
rc=0; ( require_deps definitely-not-a-real-binary-xyz 2>/dev/null ) || rc=$?
[ "$rc" -ne 0 ] || fail "require_deps accepted a missing tool"
msg=$( require_deps mtools-bogus-mcopy 2>&1 || true )
case "$msg" in *"apt-get install"*) ;; *) fail "missing-dep message lacks install hint: $msg" ;; esac
msg=$( require_deps bogus-tool-alpha bogus-tool-beta 2>&1 || true )
case "$msg" in *bogus-tool-alpha*bogus-tool-beta*) ;; *) fail "require_deps did not accumulate all missing tools: $msg" ;; esac
pass "require_deps"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bash provisioning/dietpi/tests/test_build_image.sh`
Expected: FAIL — `require_deps: command not found`.

- [ ] **Step 3: Write minimal implementation** — add to `build-image.sh`:

```bash
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `bash provisioning/dietpi/tests/test_build_image.sh`
Expected: `ok: require_deps` then `ALL TESTS PASSED`.

- [ ] **Step 5: Commit**

```bash
git add provisioning/dietpi/build-image.sh provisioning/dietpi/tests/test_build_image.sh
# then ask the owner to authorize: git commit -m "feat(provisioning): build-image.sh dep check"
```

---

### Task 4: `part1_offset_bytes`

**Files:**
- Modify: `provisioning/dietpi/build-image.sh`
- Test: `provisioning/dietpi/tests/test_build_image.sh`

- [ ] **Step 1: Write the failing test** — append before the final echo:

```bash
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bash provisioning/dietpi/tests/test_build_image.sh`
Expected: FAIL — `part1_offset_bytes: command not found`.

- [ ] **Step 3: Write minimal implementation** — add to `build-image.sh`:

```bash
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `bash provisioning/dietpi/tests/test_build_image.sh`
Expected: `ok: part1_offset_bytes` then `ALL TESTS PASSED`.

- [ ] **Step 5: Commit**

```bash
git add provisioning/dietpi/build-image.sh provisioning/dietpi/tests/test_build_image.sh
# then ask the owner to authorize: git commit -m "feat(provisioning): partition-1 offset reader"
```

---

### Task 5: Flash guard predicates

**Files:**
- Modify: `provisioning/dietpi/build-image.sh`
- Test: `provisioning/dietpi/tests/test_build_image.sh`

- [ ] **Step 1: Write the failing test** — append before the final echo:

```bash
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bash provisioning/dietpi/tests/test_build_image.sh`
Expected: FAIL — `is_block_device: command not found`.

- [ ] **Step 3: Write minimal implementation** — add to `build-image.sh`:

```bash
# True iff path is a block-special device.
is_block_device() { [ -b "$1" ]; }

# True iff /sys says the whole-disk device is removable. Sysfs root is
# overridable for tests. NOTE: f is assigned on its own line — a single
# `local name=$1 f="...$name..."` expands $name before it is in scope and
# aborts under `set -u` (the harness sources this with set -u).
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `bash provisioning/dietpi/tests/test_build_image.sh`
Expected: `ok: flash guards` then `ALL TESTS PASSED`.

- [ ] **Step 5: Commit**

```bash
git add provisioning/dietpi/build-image.sh provisioning/dietpi/tests/test_build_image.sh
# then ask the owner to authorize: git commit -m "feat(provisioning): flash safety guard predicates"
```

---

### Task 6: `fetch_and_verify`

**Files:**
- Modify: `provisioning/dietpi/build-image.sh`
- Test: `provisioning/dietpi/tests/test_build_image.sh`

- [ ] **Step 1: Write the failing test** — append before the final echo:

```bash
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
rm -rf "$fv"
pass "fetch_and_verify"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bash provisioning/dietpi/tests/test_build_image.sh`
Expected: FAIL — `fetch_and_verify: command not found`.

- [ ] **Step 3: Write minimal implementation** — add to `build-image.sh`:

```bash
# Download (cached) + sha256 -c + decompress into <dir>. force=1 allows
# overwriting an existing .img. Uses curl, which supports file:// for tests.
# Assumes the DietPi .sha256 sidecar names the bare IMAGE_NAME (we cd into
# $dir so the bare name resolves). curl -fsSL: silent meter, real errors kept.
fetch_and_verify() {
  local dir=$1 force=$2
  mkdir -p "$dir"
  local xzf="$dir/$IMAGE_NAME" shaf="$dir/$IMAGE_NAME.sha256"
  local img="$dir/${IMAGE_NAME%.xz}"
  if [ -e "$img" ] && [ "$force" != "1" ]; then
    echo "refusing to overwrite existing image: $img (use --force)" >&2
    return 1
  fi
  if ! { [ -s "$xzf" ] && [ -s "$shaf" ] && ( cd "$dir" && sha256sum -c "$IMAGE_NAME.sha256" >/dev/null 2>&1 ); }; then
    curl -fsSL --retry 2 -o "$xzf"  "$BASE_URL/$IMAGE_NAME"        || { echo "download failed: $BASE_URL/$IMAGE_NAME" >&2; return 1; }
    curl -fsSL --retry 2 -o "$shaf" "$BASE_URL/$IMAGE_NAME.sha256" || { echo "checksum download failed" >&2; return 1; }
  fi
  ( cd "$dir" && sha256sum -c "$IMAGE_NAME.sha256" ) >/dev/null 2>&1 \
    || { echo "SHA256 verification FAILED for $xzf" >&2; return 1; }
  # Atomic: decompress to a temp then mv, so a failed xz never leaves a
  # truncated .img that would block the next run with the clobber guard.
  local tmp="$img.tmp.$$"
  xz -dk -c "$xzf" > "$tmp" || { rm -f "$tmp"; echo "decompress failed" >&2; return 1; }
  mv -f "$tmp" "$img"
  printf '%s\n' "$img"
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `bash provisioning/dietpi/tests/test_build_image.sh`
Expected: `ok: fetch_and_verify` then `ALL TESTS PASSED`.

> **Security hardening (audit 2026-05-17):** `fetch_and_verify` no longer
> trusts whatever filename the `.sha256` sidecar lists. It extracts the hash
> whose filename field equals **exactly** `$IMAGE_NAME` (rejecting the sidecar
> with `checksum sidecar does not list $IMAGE_NAME` if absent), then verifies
> by constructing the `sha256sum -c` input itself — applied on both the
> cache-hit and post-download paths. After the `.img`/`.xz`/`.sha256` exist it
> `chmod 600`s them and `chmod 700`s the build dir (they embed the PSK,
> device password, and SSH key); `main()` also sets `umask 077` early.

- [ ] **Step 5: Commit**

```bash
git add provisioning/dietpi/build-image.sh provisioning/dietpi/tests/test_build_image.sh
# then ask the owner to authorize: git commit -m "feat(provisioning): cached fetch + checksum + decompress"
```

---

### Task 7: `stage_and_inject` (mtools, no root)

**Files:**
- Modify: `provisioning/dietpi/build-image.sh`
- Test: `provisioning/dietpi/tests/test_build_image.sh`

- [ ] **Step 1: Write the failing test** — append before the final echo. This builds a real partitioned FAT image with mtools (`mformat`, no root, no dosfstools) and verifies the three files land in it:

```bash
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
for f in DIETPI~1.TXT DIETPI~2.TXT AUTOMA~1.SH; do :; done   # 8.3 noise tolerated
echo "$list" | grep -qi 'dietpi'   || fail "dietpi.txt not written into image"
echo "$list" | grep -qi 'AUTOMAT' || echo "$list" | grep -qi 'Automation' \
  || fail "Automation_Custom_Script.sh not written into image"
# pull dietpi.txt back out and confirm injection happened (no placeholder)
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bash provisioning/dietpi/tests/test_build_image.sh`
Expected: FAIL — `stage_and_inject: command not found`.

- [ ] **Step 3: Write minimal implementation** — add to `build-image.sh`:

```bash
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
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `bash provisioning/dietpi/tests/test_build_image.sh`
Expected: `ok: stage_and_inject writes injected config into the image (userspace)` then `ALL TESTS PASSED`.

- [ ] **Step 5: Commit**

```bash
git add provisioning/dietpi/build-image.sh provisioning/dietpi/tests/test_build_image.sh
# then ask the owner to authorize: git commit -m "feat(provisioning): userspace mtools stage+inject"
```

---

### Task 8: `flash_image` (guarded; the only sudo)

**Files:**
- Modify: `provisioning/dietpi/build-image.sh`
- Test: `provisioning/dietpi/tests/test_build_image.sh`

- [ ] **Step 1: Write the failing test** — append before the final echo. Tests only the refusal paths (no real disk, no dd):

```bash
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

refuses "not a block device" "" "$fimg" /no/such/dev 0
is_block_device() { return 0; }                 # pretend the target is a block dev
refuses "refusing loop/ram" "" "$fimg" /dev/loop9 0
root_disk() { echo sda; }
refuses "refusing the system disk" "" "$fimg" /dev/sda 0
root_disk() { echo zzz; }                        # not the system disk
is_removable() { return 1; }
refuses "non-removable" "" "$fimg" /dev/sdZ 0
is_removable() { return 0; }
refuses "confirmation did not match" "/dev/WRONG" "$fimg" /dev/sdZ 1
# mounted partition on the target -> refused (before confirm/dd). lsblk stub:
# -ndo PKNAME -> empty (base=sdZ); -no MOUNTPOINT -> a mountpoint line.
lsblk() { case "$1" in -no) echo "/media/usb" ;; esac; }
refuses "has mounted partitions" "" "$fimg" /dev/sdZ 1
unset -f lsblk

# HAPPY PATH: all guards pass + exact confirmation -> reaches dd (sudo stubbed
# so NO real device is ever touched). Proves guards don't false-refuse.
lsblk() { :; }                                   # no mounted parts, quiet device info
sudo() { echo "SUDO:$*"; }                       # never touch a real device
out=$(printf '/dev/sdZ\n' | flash_image "$fimg" /dev/sdZ 1 2>/dev/null); rc=$?
[ "$rc" -eq 0 ] || fail "happy path should succeed (rc=$rc)"
case "$out" in
  *"SUDO:dd if=$fimg of=/dev/sdZ"*) ;;
  *) fail "dd not invoked on happy path: [$out]" ;;
esac
unset -f is_block_device is_removable root_disk lsblk sudo
rm -f "$fimg"
pass "flash_image: guards refuse with correct reason; dd reached only when all pass"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bash provisioning/dietpi/tests/test_build_image.sh`
Expected: FAIL — `flash_image: command not found`.

- [ ] **Step 3: Write minimal implementation** — add to `build-image.sh`:

```bash
# Guarded raw write to a real SD. Args: img dev i_understand mounted_ok(unused
# placeholder kept 0). Returns non-zero (refuses) unless ALL guards pass; only
# then does it sudo dd. Confirmation is read from stdin (typed device path).
flash_image() {
  local img=$1 dev=$2 i_understand=$3
  is_block_device "$dev" || { echo "not a block device: $dev" >&2; return 1; }
  case "$dev" in */loop*|/dev/ram*|/dev/zram*) echo "refusing loop/ram/zram device: $dev" >&2; return 1 ;; esac
  if is_system_disk "$dev"; then echo "refusing the system disk: $dev" >&2; return 1; fi
  local base; base=$(lsblk -ndo PKNAME "$dev" 2>/dev/null); [ -z "$base" ] && base=${dev#/dev/}
  if ! is_removable "$base"; then
    [ "$i_understand" = "1" ] || { echo "refusing non-removable $dev (pass --i-understand to override)" >&2; return 1; }
  fi
  if lsblk -no MOUNTPOINT "$dev" 2>/dev/null | grep -q . ; then
    echo "refusing: $dev has mounted partitions — unmount first" >&2; return 1
  fi
  echo "About to OVERWRITE this device:" >&2
  lsblk -dno NAME,MODEL,SIZE,TRAN,RM "$dev" >&2 2>/dev/null || true
  echo "Type the exact device path to confirm (or anything else to abort):" >&2
  confirm_matches "$dev" || { echo "confirmation did not match — aborted" >&2; return 1; }
  echo "Pre-flighting sudo (required only to write the raw device $dev) ..." >&2
  sudo -v || { echo "sudo authentication failed — aborted" >&2; return 1; }
  echo "Flashing $img -> $dev (sudo) ..." >&2
  sudo dd if="$img" of="$dev" bs=4M conv=fsync status=progress && sudo sync
}
```

Note: `sudo -v` is placed after ALL guards and the typed-confirmation so refusal paths never prompt for a password. This pre-flight satisfies the design spec requirement (spec line 94: "The script pre-flights `sudo -v` only on this path and states why").

- [ ] **Step 4: Run test to verify it passes**

Run: `bash provisioning/dietpi/tests/test_build_image.sh`
Expected: `ok: flash_image: guards refuse with correct reason; dd reached only
when all pass` then `ALL TESTS PASSED`.

> **Security hardening (audit 2026-05-17):** the guard order was reworked.
> `flash_image` first **canonicalizes** the device with `readlink -f`
> (resolving `/dev/disk/by-id/...` aliases; an unresolvable path is refused),
> then runs every check on the canonical path: block device → `lsblk -ndo
> TYPE` must be exactly `disk` (rejects partitions like `/dev/sdb1`; an lsblk
> error fails **closed**) → loop/ram/zram on the canonical kernel name →
> system/root disk → non-removable (needs `--i-understand`) → mounted check
> (an lsblk error fails **closed**, never read as "not mounted") → typed
> confirmation of the canonical path → `sudo -v`. Immediately before
> `sudo dd` it re-resolves and re-asserts type==disk and not-mounted, aborting
> on any change (closes the TOCTOU window).

- [ ] **Step 5: Commit**

```bash
git add provisioning/dietpi/build-image.sh provisioning/dietpi/tests/test_build_image.sh
# then ask the owner to authorize: git commit -m "feat(provisioning): guarded --flash"
```

---

### Task 9: `main` wiring + manual runbook note

**Files:**
- Modify: `provisioning/dietpi/build-image.sh`
- Test: `provisioning/dietpi/tests/test_build_image.sh`

- [ ] **Step 1: Write the failing test** — append before the final echo:

```bash
# --- main: --help exits 0; bad flag exits 2 (no network/disk touched) ---
( main --help >/dev/null 2>&1 ) || fail "main --help should exit 0"
rc=0; ( main --nope 2>/dev/null ) || rc=$?
[ "$rc" -eq 2 ] || fail "main bad flag should exit 2 (got $rc)"
pass "main arg surface"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bash provisioning/dietpi/tests/test_build_image.sh`
Expected: FAIL — `main --help` prints the stub line / wrong rc.

- [ ] **Step 3: Write minimal implementation** — replace the stub `main()` in `build-image.sh` with:

```bash
main() {
  set -euo pipefail
  umask 077                       # build artifacts embed PSK/password/SSH key
  parse_args "$@"
  # ssh-keygen: build path runs prepare-sd.sh which validates the SSH pubkey.
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
```

Note: the two command-substitution lines use explicit `|| { echo ...; return 1; }` guards so failures propagate unconditionally even when `set -e` is suppressed (bash suppresses `set -e` inside functions called in certain conditional contexts).

(Note: `stage_and_inject` is called with an empty template arg so `prepare-sd.sh` does its real network key-verify; tests pass an explicit fixture template instead.)

- [ ] **Step 4: Run test to verify it passes**

Run: `bash provisioning/dietpi/tests/test_build_image.sh`
Expected: `ok: main arg surface` then `ALL TESTS PASSED`.

- [ ] **Step 5: Static checks**

Run: `bash -n provisioning/dietpi/build-image.sh && chmod +x provisioning/dietpi/build-image.sh && echo SYNTAX_OK`
Expected: `SYNTAX_OK`.

- [ ] **Step 6: Manual runbook (documented; operator runs the real end-to-end)**

The full network + real-SD path is operator-run (like the prior plan's Task 10). Document the one-liner in the report, do not execute here:
`provisioning/dietpi/build-image.sh` (build only) then optionally `provisioning/dietpi/build-image.sh --flash /dev/sdX`.

- [ ] **Step 7: Commit**

```bash
git add provisioning/dietpi/build-image.sh provisioning/dietpi/tests/test_build_image.sh
# then ask the owner to authorize: git commit -m "feat(provisioning): wire build-image.sh main"
```

---

## Self-Review

**1. Spec coverage**

| Spec requirement | Task |
|---|---|
| `provisioning/dietpi/build-image.sh`, sourceable, sibling to prepare-sd.sh | 1 |
| Interface flags (--image-dir/--secrets/--pubkey/--flash/--i-understand/--force) + defaults | 2 |
| Dep-check incl. mtools + install hint; lsblk only when flashing | 3, 9 |
| Userspace partition offset from a regular file (no root) | 4 |
| Flash guards: block / removable / system-disk / typed-confirm | 5, 8 |
| Cached fetch + sha256 -c + decompress + clobber/--force | 6 |
| Userspace mtools stage+inject reusing unchanged prepare-sd.sh | 7 |
| Guarded `--flash` is the only sudo; refuses before dd | 8 |
| `main` order; build path 100% userspace; manual runbook | 9 |
| Teardown = `rm -rf` temp only (no privileged resource) | 7 (RETURN trap) |
| set -euo pipefail inside main; no side effects on source | 1, 9 |
| Out of scope (no recompress, RPi234 only, no README/prepare-sd.sh change) | enforced — no such tasks |

No gaps.

**2. Placeholder scan:** `__INJECTED__` is the real config sentinel asserted by Task 7, not a plan placeholder. No "TBD/handle errors" prose; every code step has complete code.

**3. Type/name consistency:** `ARG_*` globals set in Task 2 are consumed unchanged in Task 9. `part1_offset_bytes`, `is_block_device`, `is_removable`, `root_disk`, `is_system_disk`, `confirm_matches`, `fetch_and_verify`, `stage_and_inject`, `flash_image`, `require_deps`, `parse_args`, `usage`, `main` keep stable signatures across tasks. `IMAGE_NAME`/`BASE_URL` defined in Task 1 used in 6/7/9. `flash_image` arg order (img, dev, i_understand) matches its Task 8 test and the Task 9 call.

Self-review passed.
