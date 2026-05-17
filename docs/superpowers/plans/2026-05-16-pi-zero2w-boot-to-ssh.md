# Pi Zero 2 W — Fast Boot to SSH (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **PROJECT RULE — COMMITS:** This repo's owner requires explicit authorization
> (a password) before any `git commit`. Every "Commit" step below means:
> run the `git add` exactly as written, then **stop and ask the user to
> authorize the commit**. Do not run `git commit` until they do. Never `git push`.

**Goal:** Build a committed, reproducible `provisioning/dietpi/` toolkit that flashes a Pi Zero 2 W to a lean DietPi 64-bit image which boots unattended to a key-only SSH login.

**Architecture:** Static DietPi automation files (`dietpi.txt`, WiFi/secret templates, first-boot hook) plus a thin CLI (`prepare-sd.sh`) that injects secrets and writes them onto the SD's FAT boot partition. All non-trivial logic lives in a sourced, unit-tested `lib.sh` (pure functions: value quoting, key injection, template-key verification, WiFi preflight, idempotent file edits). The CLI is a wrapper; the bring-up/boot itself is a documented manual acceptance step.

**Tech Stack:** Bash (POSIX-ish, coreutils + `awk`/`grep`), DietPi Bookworm aarch64 automation, dependency-free bash test harness.

---

## File Structure

```
provisioning/dietpi/
  README.md                    # download/checksum, steps, failure modes, recovery
  dietpi.txt                   # unattended-install config; pubkey + password are __INJECTED__ placeholders
  dietpi-wifi.txt.example      # DietPi WiFi array-format template (committed)
  secrets.env.example          # WiFi SSID/PSK + GLOBAL_PASSWORD template (committed)
  Automation_Custom_Script.sh  # first-boot hook (cmdline tokens, unit masking) — runs ON the Pi
  lib.sh                       # sourced pure functions (unit-tested)
  prepare-sd.sh                # CLI: default = write to SD; --check = offline validation
  tests/
    test_lib.sh                # dependency-free bash unit tests for lib.sh
    fixtures/
      template-sample.txt      # frozen DietPi-template snippet for key-verification tests
  baseline-boot.txt            # produced post-bring-up (Task 10); not authored by the plan
.gitignore                     # + provisioning/dietpi/secrets.env* + dietpi-wifi.txt* (examples un-ignored)
```

Decomposition rationale: `lib.sh` holds every testable decision so `prepare-sd.sh` stays a thin, obvious wrapper; `Automation_Custom_Script.sh` is separate because it executes in a different environment (on the Pi, post-network) and must not import local-only assumptions.

---

### Task 1: Scaffold directory, gitignore, and committed templates

**Files:**
- Create: `provisioning/dietpi/secrets.env.example`
- Create: `provisioning/dietpi/dietpi-wifi.txt.example`
- Create: `provisioning/dietpi/tests/fixtures/.gitkeep`
- Modify: `.gitignore` (append two lines)

- [ ] **Step 1: Create the secrets template**

Create `provisioning/dietpi/secrets.env.example`:

```bash
# Copy to secrets.env (gitignored) and fill in. Phase-1 WiFi MUST be a
# broadcast 2.4 GHz WPA2-PSK network (Zero 2 W has no 5 GHz; WPA3-only and
# hidden SSIDs fail on a headless first boot).
WIFI_SSID=""
WIFI_PSK=""
WIFI_KEYMGR="WPA-PSK"
# Non-default device password. Replaces DietPi's well-known "dietpi".
# Used for local/sudo; password SSH is disabled regardless.
GLOBAL_PASSWORD=""
```

- [ ] **Step 2: Create the DietPi WiFi array-format template**

Create `provisioning/dietpi/dietpi-wifi.txt.example`:

```bash
# DietPi WiFi config (array format). prepare-sd.sh generates the real
# dietpi-wifi.txt from secrets.env with correct single-quote escaping.
aWIFI_SSID[0]='__INJECTED__'
aWIFI_KEY[0]='__INJECTED__'
aWIFI_KEYMGR[0]='WPA-PSK'
```

- [ ] **Step 3: Keep the fixtures directory tracked**

Create `provisioning/dietpi/tests/fixtures/.gitkeep` (empty file).

- [ ] **Step 4: Update .gitignore**

Append to `.gitignore`:

```
provisioning/dietpi/secrets.env*
!provisioning/dietpi/secrets.env.example
provisioning/dietpi/dietpi-wifi.txt*
!provisioning/dietpi/dietpi-wifi.txt.example
```
(glob form so editor backups like `secrets.env~` are also ignored, while the
committed `.example` templates are explicitly un-ignored.)

- [ ] **Step 5: Verify**

Run:
```bash
test -f provisioning/dietpi/secrets.env.example \
  && test -f provisioning/dietpi/dietpi-wifi.txt.example \
  && grep -q '^provisioning/dietpi/secrets.env\*$' .gitignore \
  && grep -q '^provisioning/dietpi/dietpi-wifi.txt\*$' .gitignore \
  && echo OK
```
Expected: `OK`

- [ ] **Step 6: Commit** (see PROJECT RULE — stage then request authorization)

```bash
git add provisioning/dietpi/secrets.env.example provisioning/dietpi/dietpi-wifi.txt.example provisioning/dietpi/tests/fixtures/.gitkeep .gitignore
# then ask the user to authorize: git commit -m "chore(provisioning): scaffold dietpi dir, templates, gitignore"
```

---

### Task 2: `lib.sh` — `dietpi_quote` (value single-quote escaping)

**Files:**
- Create: `provisioning/dietpi/lib.sh`
- Test: `provisioning/dietpi/tests/test_lib.sh`

- [ ] **Step 1: Write the failing test**

Create `provisioning/dietpi/tests/test_lib.sh`:

```bash
#!/usr/bin/env bash
# Dependency-free unit tests for lib.sh. Exit non-zero on first failure.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=/dev/null
source "$HERE/../lib.sh"

fail() { echo "FAIL: $1" >&2; exit 1; }
pass() { echo "ok: $1"; }

# --- dietpi_quote: output must round-trip back to the original via eval ---
roundtrip() {
  local in=$1 out got
  out=$(dietpi_quote "$in")
  eval "got=$out"
  [ "$got" = "$in" ] || fail "dietpi_quote round-trip: in=[$in] out=[$out] got=[$got]"
}
roundtrip "plainSSID"
roundtrip "has space"
roundtrip "tick'inside"
roundtrip 'dollar$and`tick`'
roundtrip 'back\slash'
roundtrip "it's a 'test'"
pass "dietpi_quote round-trips"

echo "ALL TESTS PASSED"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bash provisioning/dietpi/tests/test_lib.sh`
Expected: FAIL — `lib.sh` does not exist / `dietpi_quote: command not found`

- [ ] **Step 3: Write minimal implementation**

Create `provisioning/dietpi/lib.sh`:

```bash
#!/usr/bin/env bash
# Pure, sourceable helpers for prepare-sd.sh. No side effects at source time.

# Single-quote a value for a DietPi config line. A literal single quote
# becomes the POSIX sequence: close-quote, escaped-quote, reopen-quote.
dietpi_quote() {
  local s=${1-}
  s=${s//\'/\'\\\'\'}
  printf "'%s'" "$s"
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `bash provisioning/dietpi/tests/test_lib.sh`
Expected: `ok: dietpi_quote round-trips` then `ALL TESTS PASSED`

- [ ] **Step 5: Commit**

```bash
git add provisioning/dietpi/lib.sh provisioning/dietpi/tests/test_lib.sh
# then ask the user to authorize: git commit -m "feat(provisioning): dietpi_quote with round-trip tests"
```

---

### Task 3: `lib.sh` — `inject_kv` (exact-key line replacement)

**Files:**
- Modify: `provisioning/dietpi/lib.sh`
- Test: `provisioning/dietpi/tests/test_lib.sh`

- [ ] **Step 1: Write the failing test** — append before the final `echo "ALL TESTS PASSED"` in `tests/test_lib.sh`:

```bash
# --- inject_kv: replaces only the exact key line; missing key is an error ---
tmpf=$(mktemp)
printf 'A=old\nAB=keep\nB=__INJECTED__\n' > "$tmpf"
inject_kv "$tmpf" A "new" || fail "inject_kv A returned non-zero"
inject_kv "$tmpf" B "val" || fail "inject_kv B returned non-zero"
grep -qx 'A=new' "$tmpf" || fail "inject_kv did not set A"
grep -qx 'AB=keep' "$tmpf" || fail "inject_kv clobbered prefix-similar key AB"
grep -qx 'B=val' "$tmpf" || fail "inject_kv did not set B"
inject_kv "$tmpf" MISSING x 2>/dev/null && fail "inject_kv should fail on missing key"
inject_kv "$tmpf" A "new" && grep -qx 'A=new' "$tmpf" || fail "inject_kv not idempotent"
rm -f "$tmpf"
pass "inject_kv exact-match + missing-key error + idempotent"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bash provisioning/dietpi/tests/test_lib.sh`
Expected: FAIL — `inject_kv: command not found`

- [ ] **Step 3: Write minimal implementation** — append to `provisioning/dietpi/lib.sh`:

```bash
# Replace the line beginning exactly "KEY=" with "KEY=VALUE". The value is
# written literally (caller pre-quotes if needed). Missing key => error,
# so an upstream DietPi rename is caught instead of silently ignored.
inject_kv() {
  local file=$1 key=$2 value=$3 tmp
  grep -qE "^${key}=" "$file" || { echo "inject_kv: key '$key' not in $file" >&2; return 1; }
  tmp=$(mktemp)
  awk -v k="$key" -v val="$value" '
    index($0, k "=") == 1 { print k "=" val; next }
    { print }
  ' "$file" > "$tmp" && mv "$tmp" "$file"
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `bash provisioning/dietpi/tests/test_lib.sh`
Expected: `ok: inject_kv exact-match + missing-key error + idempotent` then `ALL TESTS PASSED`

- [ ] **Step 5: Commit**

```bash
git add provisioning/dietpi/lib.sh provisioning/dietpi/tests/test_lib.sh
# then ask the user to authorize: git commit -m "feat(provisioning): inject_kv exact-key replacement"
```

---

### Task 4: `lib.sh` — `verify_keys_against_template`

**Files:**
- Modify: `provisioning/dietpi/lib.sh`
- Create: `provisioning/dietpi/tests/fixtures/template-sample.txt`
- Test: `provisioning/dietpi/tests/test_lib.sh`

- [ ] **Step 1: Create the frozen template fixture**

Create `provisioning/dietpi/tests/fixtures/template-sample.txt` (the keys this plan depends on, as they appear in the current DietPi template — used so the test is offline and deterministic):

```
# AUTO_SETUP_ACCEPT_LICENSE removed — DietPi dropped the EULA key (reconciled 2026-05-17)
AUTO_SETUP_AUTOMATED=0
AUTO_SETUP_NET_HOSTNAME=DietPi
AUTO_SETUP_NET_WIFI_ENABLED=0
AUTO_SETUP_NET_WIFI_COUNTRY_CODE=GB
AUTO_SETUP_HEADLESS=0
AUTO_SETUP_SSH_SERVER_INDEX=-1
AUTO_SETUP_SSH_PUBKEY=
SOFTWARE_DISABLE_SSH_PASSWORD_LOGINS=0
AUTO_SETUP_GLOBAL_PASSWORD=dietpi
AUTO_SETUP_INSTALL_SOFTWARE_ID=
AUTO_SETUP_CUSTOM_SCRIPT_EXEC=0
AUTO_SETUP_BOOT_WAIT_FOR_NETWORK=1
CONFIG_CHECK_DIETPI_UPDATES=1
CONFIG_CHECK_APT_UPDATES=1
SURVEY_OPTED_IN=-1
AUTO_SETUP_SWAPFILE_SIZE=1
```

- [ ] **Step 2: Write the failing test** — append before the final `echo` in `tests/test_lib.sh`:

```bash
# --- verify_keys_against_template: all present => ok; any missing => error ---
FIX="$HERE/fixtures/template-sample.txt"
verify_keys_against_template "$FIX" AUTO_SETUP_AUTOMATED AUTO_SETUP_BOOT_WAIT_FOR_NETWORK \
  SOFTWARE_DISABLE_SSH_PASSWORD_LOGINS AUTO_SETUP_GLOBAL_PASSWORD \
  || fail "verify_keys_against_template rejected keys that exist"
verify_keys_against_template "$FIX" CONFIG_BOOT_WAIT_FOR_NETWORK 2>/dev/null \
  && fail "verify_keys_against_template accepted a non-existent (renamed) key"
pass "verify_keys_against_template detects renamed/missing keys"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `bash provisioning/dietpi/tests/test_lib.sh`
Expected: FAIL — `verify_keys_against_template: command not found`

- [ ] **Step 4: Write minimal implementation** — append to `provisioning/dietpi/lib.sh`:

```bash
# Assert every given key exists in the DietPi template (active OR commented-out).
# A commented key (#KEY=) means the name is still valid (just default-off), NOT
# removed. Matches active KEY=, #KEY=, # KEY=, and leading-whitespace variants.
# Lists all missing keys at once so a rename is loud, not silent.
# (Reconciled 2026-05-17: awk updated from index==1 to strip leading # so that
# commented default-off keys like #AUTO_SETUP_SSH_PUBKEY= are accepted.)
verify_keys_against_template() {
  local tmpl=$1; shift
  local k missing=()
  for k in "$@"; do
    awk -v key="$k" '{ s=$0; sub(/^[ \t]+/,"",s); sub(/^#[ \t]*/,"",s); if (index(s, key "=")==1) f=1 } END { exit(f?0:1) }' "$tmpl" \
      || missing+=("$k")
  done
  if [ "${#missing[@]}" -gt 0 ]; then
    echo "verify: keys absent from DietPi template (renamed/removed?): ${missing[*]}" >&2
    return 1
  fi
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `bash provisioning/dietpi/tests/test_lib.sh`
Expected: `ok: verify_keys_against_template detects renamed/missing keys` then `ALL TESTS PASSED`

- [ ] **Step 6: Commit**

```bash
git add provisioning/dietpi/lib.sh provisioning/dietpi/tests/test_lib.sh provisioning/dietpi/tests/fixtures/template-sample.txt
# then ask the user to authorize: git commit -m "feat(provisioning): verify dietpi keys against template"
```

---

### Task 5: `lib.sh` — `wifi_precheck`

**Files:**
- Modify: `provisioning/dietpi/lib.sh`
- Test: `provisioning/dietpi/tests/test_lib.sh`

- [ ] **Step 1: Write the failing test** — append before the final `echo` in `tests/test_lib.sh`:

```bash
# --- wifi_precheck: enforce Phase-1 preconditions ---
wifi_precheck "MySSID" "agoodpassphrase" "WPA-PSK" || fail "valid WPA2 passphrase rejected"
wifi_precheck "MySSID" "$(printf 'a%.0s' {1..64} | tr a 0)" "WPA-PSK" \
  || fail "valid 64-hex PSK rejected"
wifi_precheck "" "agoodpassphrase" "WPA-PSK" 2>/dev/null && fail "empty SSID accepted"
wifi_precheck "MySSID" "short" "WPA-PSK" 2>/dev/null && fail "too-short PSK accepted"
wifi_precheck "MySSID" "agoodpassphrase" "WPA-EAP" 2>/dev/null && fail "non-WPA-PSK accepted"
pass "wifi_precheck enforces SSID/PSK/keymgr rules"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bash provisioning/dietpi/tests/test_lib.sh`
Expected: FAIL — `wifi_precheck: command not found`

- [ ] **Step 3: Write minimal implementation** — append to `provisioning/dietpi/lib.sh`:

```bash
# Enforce Phase-1 WiFi preconditions: non-empty SSID, WPA-PSK only
# (no first-boot WPA3/EAP path), PSK is an 8-63 char passphrase or 64 hex.
wifi_precheck() {
  local ssid=${1-} psk=${2-} keymgr=${3-} n
  [ -n "$ssid" ] || { echo "wifi: SSID is empty" >&2; return 1; }
  [ "$keymgr" = "WPA-PSK" ] || { echo "wifi: KEYMGR must be WPA-PSK (got '$keymgr'); WPA3-only/EAP unsupported for headless first boot" >&2; return 1; }
  n=${#psk}
  if [ "$n" -ge 8 ] && [ "$n" -le 63 ]; then return 0; fi
  if [ "$n" -eq 64 ] && [[ "$psk" =~ ^[0-9a-fA-F]{64}$ ]]; then return 0; fi
  echo "wifi: PSK must be an 8-63 char passphrase or 64 hex chars (got length $n)" >&2
  return 1
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `bash provisioning/dietpi/tests/test_lib.sh`
Expected: `ok: wifi_precheck enforces SSID/PSK/keymgr rules` then `ALL TESTS PASSED`

- [ ] **Step 5: Commit**

```bash
git add provisioning/dietpi/lib.sh provisioning/dietpi/tests/test_lib.sh
# then ask the user to authorize: git commit -m "feat(provisioning): wifi_precheck preconditions"
```

---

### Task 6: Author `dietpi.txt`

**Files:**
- Create: `provisioning/dietpi/dietpi.txt`
- Test: `provisioning/dietpi/tests/test_lib.sh`

- [ ] **Step 1: Write the failing test** — append before the final `echo` in `tests/test_lib.sh`:

```bash
# --- dietpi.txt must carry every spec-required key + injection placeholders ---
DT="$HERE/../dietpi.txt"
for k in AUTO_SETUP_AUTOMATED AUTO_SETUP_NET_HOSTNAME AUTO_SETUP_NET_WIFI_ENABLED \
         AUTO_SETUP_NET_WIFI_COUNTRY_CODE AUTO_SETUP_HEADLESS AUTO_SETUP_SSH_SERVER_INDEX \
         AUTO_SETUP_SSH_PUBKEY SOFTWARE_DISABLE_SSH_PASSWORD_LOGINS AUTO_SETUP_GLOBAL_PASSWORD \
         AUTO_SETUP_INSTALL_SOFTWARE_ID AUTO_SETUP_CUSTOM_SCRIPT_EXEC \
         AUTO_SETUP_BOOT_WAIT_FOR_NETWORK CONFIG_CHECK_DIETPI_UPDATES CONFIG_CHECK_APT_UPDATES \
         SURVEY_OPTED_IN AUTO_SETUP_SWAPFILE_SIZE; do
  grep -qE "^${k}=" "$DT" || fail "dietpi.txt missing key: $k"
done
grep -qx 'AUTO_SETUP_SSH_PUBKEY=__INJECTED__' "$DT" || fail "pubkey placeholder missing"
grep -qx 'AUTO_SETUP_GLOBAL_PASSWORD=__INJECTED__' "$DT" || fail "password placeholder missing"
grep -qx 'AUTO_SETUP_BOOT_WAIT_FOR_NETWORK=0' "$DT" || fail "wrong/absent wait-for-network value"
grep -qx 'SOFTWARE_DISABLE_SSH_PASSWORD_LOGINS=1' "$DT" || fail "password-login not disabled"
grep -qx 'AUTO_SETUP_SSH_SERVER_INDEX=-2' "$DT" || fail "SSH server not OpenSSH (-2)"
grep -qE '^CONFIG_BOOT_WAIT_FOR_NETWORK=' "$DT" && fail "stale CONFIG_BOOT_WAIT_FOR_NETWORK present"
pass "dietpi.txt has all required keys, correct values, placeholders"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bash provisioning/dietpi/tests/test_lib.sh`
Expected: FAIL — `dietpi.txt missing key: ...`

- [ ] **Step 3: Create `provisioning/dietpi/dietpi.txt`**

```
# Phase-1 DietPi unattended install for Pi Zero 2 W (boots to key-only SSH).
# Pubkey + password are injected by prepare-sd.sh. Keys verified against the
# live DietPi template by `prepare-sd.sh --check`.
# Note: AUTO_SETUP_ACCEPT_LICENSE was removed — DietPi dropped the EULA key
# (reconciled 2026-05-17). AUTO_SETUP_AUTOMATED=1 is the no-interaction guarantee.
AUTO_SETUP_AUTOMATED=1
AUTO_SETUP_NET_HOSTNAME=rpi-zero
AUTO_SETUP_NET_WIFI_ENABLED=1
AUTO_SETUP_NET_WIFI_COUNTRY_CODE=US
AUTO_SETUP_HEADLESS=1
AUTO_SETUP_SSH_SERVER_INDEX=-2
AUTO_SETUP_SSH_PUBKEY=__INJECTED__
SOFTWARE_DISABLE_SSH_PASSWORD_LOGINS=1
AUTO_SETUP_GLOBAL_PASSWORD=__INJECTED__
AUTO_SETUP_INSTALL_SOFTWARE_ID=
AUTO_SETUP_CUSTOM_SCRIPT_EXEC=0
AUTO_SETUP_BOOT_WAIT_FOR_NETWORK=0
CONFIG_CHECK_DIETPI_UPDATES=0
CONFIG_CHECK_APT_UPDATES=0
SURVEY_OPTED_IN=0
AUTO_SETUP_SWAPFILE_SIZE=0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `bash provisioning/dietpi/tests/test_lib.sh`
Expected: `ok: dietpi.txt has all required keys, correct values, placeholders` then `ALL TESTS PASSED`

- [ ] **Step 5: Commit**

```bash
git add provisioning/dietpi/dietpi.txt provisioning/dietpi/tests/test_lib.sh
# then ask the user to authorize: git commit -m "feat(provisioning): author phase-1 dietpi.txt"
```

---

### Task 7: `Automation_Custom_Script.sh` + idempotent edit helpers

**Files:**
- Modify: `provisioning/dietpi/lib.sh`
- Create: `provisioning/dietpi/Automation_Custom_Script.sh`
- Test: `provisioning/dietpi/tests/test_lib.sh`

- [ ] **Step 1: Write the failing test** — append before the final `echo` in `tests/test_lib.sh`:

```bash
# --- detect_fw_dir: finds the dir containing cmdline.txt ---
d=$(mktemp -d)
mkdir -p "$d/firmware"; : > "$d/firmware/cmdline.txt"
[ "$(detect_fw_dir "$d")" = "$d/firmware" ] || fail "detect_fw_dir missed /firmware"
rm -rf "$d"; d=$(mktemp -d); : > "$d/cmdline.txt"
[ "$(detect_fw_dir "$d")" = "$d" ] || fail "detect_fw_dir missed root"
detect_fw_dir "$(mktemp -d)" 2>/dev/null && fail "detect_fw_dir found nonexistent cmdline"

# --- cmdline_ensure_tokens: append only missing tokens, single line ---
cf=$(mktemp); printf 'console=serial0,115200 root=/dev/mmcblk0p2 rootwait\n' > "$cf"
cmdline_ensure_tokens "$cf" quiet loglevel=3
cmdline_ensure_tokens "$cf" quiet loglevel=3   # idempotent
grep -qx 'console=serial0,115200 root=/dev/mmcblk0p2 rootwait quiet loglevel=3' "$cf" \
  || fail "cmdline_ensure_tokens wrong result: $(cat "$cf")"
[ "$(wc -l < "$cf")" -eq 1 ] || fail "cmdline_ensure_tokens produced multiple lines"
rm -f "$cf"
pass "detect_fw_dir + cmdline_ensure_tokens"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bash provisioning/dietpi/tests/test_lib.sh`
Expected: FAIL — `detect_fw_dir: command not found`

- [ ] **Step 3: Add helpers to `provisioning/dietpi/lib.sh`** (append):

```bash
# Return the directory holding cmdline.txt: <root> or <root>/firmware.
detect_fw_dir() {
  local root=$1
  if   [ -f "$root/cmdline.txt" ];          then printf '%s\n' "$root"
  elif [ -f "$root/firmware/cmdline.txt" ]; then printf '%s\n' "$root/firmware"
  else return 1; fi
}

# Ensure each token is present on the single-line kernel cmdline file.
cmdline_ensure_tokens() {
  local f=$1; shift
  local content t
  content=$(cat "$f"); content=${content%$'\n'}
  for t in "$@"; do
    case " $content " in *" $t "*) ;; *) content="$content $t" ;; esac
  done
  printf '%s\n' "$content" > "$f"
}
```

- [ ] **Step 4: Create `provisioning/dietpi/Automation_Custom_Script.sh`**

```bash
#!/bin/dash
# DietPi runs this once at the END of first-run setup (post-network,
# post-install). It cannot rescue a failed first-boot WiFi/SSH; its edits
# take effect from the NEXT boot. Non-critical polish only. Idempotent.
set -e

# Resolve firmware dir on the running Pi (/boot or /boot/firmware).
if   [ -f /boot/cmdline.txt ];          then FW=/boot
elif [ -f /boot/firmware/cmdline.txt ]; then FW=/boot/firmware
else
  echo "Automation_Custom_Script: no cmdline.txt found; skipping cmdline tweak" >&2
  FW=""
fi

if [ -n "$FW" ]; then
  content=$(cat "$FW/cmdline.txt")
  for tok in quiet loglevel=3; do
    case " $content " in *" $tok "*) ;; *) content="$content $tok" ;; esac
  done
  printf '%s\n' "$content" > "$FW/cmdline.txt"
fi

# Mask boot-time units that survive DietPi defaults but are unneeded for a
# headless phase-1 box. Conservative list; ignore absent units. Re-runnable.
for unit in rpi-eeprom-update.service e2scrub_reap.service; do
  systemctl mask "$unit" 2>/dev/null || true
done

exit 0
```

- [ ] **Step 5: Run test to verify it passes**

Run: `bash provisioning/dietpi/tests/test_lib.sh`
Expected: `ok: detect_fw_dir + cmdline_ensure_tokens` then `ALL TESTS PASSED`

- [ ] **Step 6: Lint the on-Pi script**

Run: `bash -n provisioning/dietpi/Automation_Custom_Script.sh && echo SYNTAX_OK`
Expected: `SYNTAX_OK`

- [ ] **Step 7: Commit**

```bash
git add provisioning/dietpi/lib.sh provisioning/dietpi/Automation_Custom_Script.sh provisioning/dietpi/tests/test_lib.sh
# then ask the user to authorize: git commit -m "feat(provisioning): first-boot hook + idempotent cmdline/fw helpers"
```

---

### Task 8: `prepare-sd.sh` CLI (`--check` + write modes)

**Files:**
- Create: `provisioning/dietpi/prepare-sd.sh`
- Test: `provisioning/dietpi/tests/test_lib.sh`

- [ ] **Step 1: Write the failing test** — append before the final `echo` in `tests/test_lib.sh`:

```bash
# --- prepare-sd.sh --check: passes with good secrets, fails on bad ---
PS="$HERE/../prepare-sd.sh"
work=$(mktemp -d)
cp "$HERE/fixtures/template-sample.txt" "$work/template.txt"
cat > "$work/secrets.env" <<'EOS'
WIFI_SSID="HomeNet24"
WIFI_PSK="correcthorsebattery"
WIFI_KEYMGR="WPA-PSK"
GLOBAL_PASSWORD="s3cret-not-default"
EOS
: > "$work/id.pub"; echo "ssh-ed25519 AAAAfake comment" > "$work/id.pub"
DIETPI_DIR="$HERE/.." \
  bash "$PS" --check --secrets "$work/secrets.env" --pubkey "$work/id.pub" \
  --template "$work/template.txt" || fail "--check failed on valid inputs"
# Bad PSK must fail
sed -i 's/correcthorsebattery/short/' "$work/secrets.env"
DIETPI_DIR="$HERE/.." \
  bash "$PS" --check --secrets "$work/secrets.env" --pubkey "$work/id.pub" \
  --template "$work/template.txt" 2>/dev/null && fail "--check passed an invalid PSK"
rm -rf "$work"
pass "prepare-sd.sh --check validates inputs"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bash provisioning/dietpi/tests/test_lib.sh`
Expected: FAIL — cannot open `prepare-sd.sh`

- [ ] **Step 3: Create `provisioning/dietpi/prepare-sd.sh`**

```bash
#!/usr/bin/env bash
# Phase-1 SD provisioning for the Pi Zero 2 W.
#
#   prepare-sd.sh --check [opts]            # offline validation, no SD needed
#   prepare-sd.sh /path/to/mounted/bootfs   # write config onto the SD FAT partition
#
# Options (override defaults; mainly for tests):
#   --secrets FILE   default: $DIETPI_DIR/secrets.env
#   --pubkey FILE    default: first of ~/.ssh/id_ed25519.pub, ~/.ssh/id_rsa.pub
#   --template FILE  DietPi template to verify keys against (default: fetched)
set -euo pipefail

DIETPI_DIR=${DIETPI_DIR:-"$(cd "$(dirname "$0")" && pwd)"}
# shellcheck source=/dev/null
source "$DIETPI_DIR/lib.sh"

REQUIRED_KEYS=(
  AUTO_SETUP_AUTOMATED AUTO_SETUP_NET_HOSTNAME AUTO_SETUP_NET_WIFI_ENABLED
  AUTO_SETUP_NET_WIFI_COUNTRY_CODE AUTO_SETUP_HEADLESS AUTO_SETUP_SSH_SERVER_INDEX
  AUTO_SETUP_SSH_PUBKEY SOFTWARE_DISABLE_SSH_PASSWORD_LOGINS AUTO_SETUP_GLOBAL_PASSWORD
  AUTO_SETUP_INSTALL_SOFTWARE_ID AUTO_SETUP_CUSTOM_SCRIPT_EXEC
  AUTO_SETUP_BOOT_WAIT_FOR_NETWORK CONFIG_CHECK_DIETPI_UPDATES CONFIG_CHECK_APT_UPDATES
  SURVEY_OPTED_IN AUTO_SETUP_SWAPFILE_SIZE
)
TEMPLATE_URL="https://raw.githubusercontent.com/MichaIng/DietPi/master/dietpi.txt"

mode="" boot="" secrets="$DIETPI_DIR/secrets.env" pubkey="" template=""
while [ $# -gt 0 ]; do
  case "$1" in
    --check)     mode=check ;;
    --secrets)   secrets=$2; shift ;;
    --pubkey)    pubkey=$2; shift ;;
    --template)  template=$2; shift ;;
    -*)          echo "unknown option: $1" >&2; exit 2 ;;
    *)           mode=write; boot=$1 ;;
  esac
  shift
done
[ -n "$mode" ] || { echo "usage: prepare-sd.sh --check | <mounted-boot-path>" >&2; exit 2; }

# Resolve pubkey default.
if [ -z "$pubkey" ]; then
  for c in "$HOME/.ssh/id_ed25519.pub" "$HOME/.ssh/id_rsa.pub"; do
    [ -f "$c" ] && { pubkey=$c; break; }
  done
fi
[ -n "$pubkey" ] && [ -f "$pubkey" ] || { echo "no SSH public key (use --pubkey)" >&2; exit 1; }
[ -f "$secrets" ] || { echo "secrets file not found: $secrets (copy secrets.env.example)" >&2; exit 1; }

# Load secrets (WIFI_SSID, WIFI_PSK, WIFI_KEYMGR, GLOBAL_PASSWORD) as DATA.
# load_secrets (in lib.sh) parses the file, never executes it — a crafted
# value like WIFI_PSK="$(rm -rf x)" stays a literal string, not code.
load_secrets "$secrets" || exit 1
: "${WIFI_SSID:?}" ; : "${WIFI_PSK:?}" ; : "${WIFI_KEYMGR:?}" ; : "${GLOBAL_PASSWORD:?}"

wifi_precheck "$WIFI_SSID" "$WIFI_PSK" "$WIFI_KEYMGR"
[ "$GLOBAL_PASSWORD" != "dietpi" ] || { echo "GLOBAL_PASSWORD must not be the default 'dietpi'" >&2; exit 1; }

# Obtain a template to verify key names against.
tmp_tmpl=""
if [ -z "$template" ]; then
  tmp_tmpl=$(mktemp)
  if curl -fsSL --max-time 15 "$TEMPLATE_URL" -o "$tmp_tmpl" 2>/dev/null; then
    template=$tmp_tmpl
  else
    echo "WARN: could not fetch live DietPi template; skipping rename check" >&2
  fi
fi
[ -n "$template" ] && verify_keys_against_template "$template" "${REQUIRED_KEYS[@]}"
[ -n "$tmp_tmpl" ] && rm -f "$tmp_tmpl"

if [ "$mode" = check ]; then
  echo "CHECK OK: secrets valid, pubkey present, keys verified"
  exit 0
fi

# write mode: stage finalized files, then copy to the FAT boot partition.
[ -d "$boot" ] || { echo "boot path not a directory: $boot" >&2; exit 1; }
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT

cp "$DIETPI_DIR/dietpi.txt" "$stage/dietpi.txt"
inject_kv "$stage/dietpi.txt" AUTO_SETUP_SSH_PUBKEY  "$(cat "$pubkey")"
inject_kv "$stage/dietpi.txt" AUTO_SETUP_GLOBAL_PASSWORD "$GLOBAL_PASSWORD"

{
  printf 'aWIFI_SSID[0]=%s\n'   "$(dietpi_quote "$WIFI_SSID")"
  printf 'aWIFI_KEY[0]=%s\n'    "$(dietpi_quote "$WIFI_PSK")"
  printf 'aWIFI_KEYMGR[0]=%s\n' "$(dietpi_quote "$WIFI_KEYMGR")"
} > "$stage/dietpi-wifi.txt"

cp "$DIETPI_DIR/Automation_Custom_Script.sh" "$stage/Automation_Custom_Script.sh"
chmod +x "$stage/Automation_Custom_Script.sh"

# DietPi reads these from the FAT partition root.
cp "$stage/dietpi.txt" "$stage/dietpi-wifi.txt" "$stage/Automation_Custom_Script.sh" "$boot/"
sync
echo "WROTE: dietpi.txt, dietpi-wifi.txt, Automation_Custom_Script.sh -> $boot"
echo "Eject, boot the Zero 2 W, wait ~3-4 min, then: ssh dietpi@rpi-zero.local"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `bash provisioning/dietpi/tests/test_lib.sh`
Expected: `ok: prepare-sd.sh --check validates inputs` then `ALL TESTS PASSED`

- [ ] **Step 5: Syntax + offline self-check**

Run:
```bash
bash -n provisioning/dietpi/prepare-sd.sh && echo SYNTAX_OK
chmod +x provisioning/dietpi/prepare-sd.sh
```
Expected: `SYNTAX_OK`

- [ ] **Step 6: Commit**

```bash
git add provisioning/dietpi/prepare-sd.sh provisioning/dietpi/tests/test_lib.sh
# then ask the user to authorize: git commit -m "feat(provisioning): prepare-sd.sh --check and write modes"
```

---

### Task 9: `README.md` — runbook & failure modes

**Files:**
- Create: `provisioning/dietpi/README.md`

- [ ] **Step 1: Create `provisioning/dietpi/README.md`**

````markdown
# Pi Zero 2 W — Phase 1: Fast Boot to SSH

Reproducible headless DietPi 64-bit bring-up. Spec:
`docs/superpowers/specs/2026-05-16-pi-zero2w-boot-to-ssh-design.md`.

## Prerequisites (hard — a headless box can't tell you it failed)

- A **broadcast, 2.4 GHz, WPA2-PSK** WiFi network. The Zero 2 W has no 5 GHz.
  WPA3-only and hidden SSIDs fail on first boot.
- An SSH keypair on this machine (`~/.ssh/id_ed25519.pub` preferred).

## One-time provisioning

1. Get the current **DietPi for Raspberry Pi (ARMv8, 64-bit)** image from
   <https://dietpi.com/#download> (covers RPi 2/3/4/5 & Zero 2). Record the
   filename and the SHA256 DietPi publishes next to it, and verify:
   ```bash
   sha256sum DietPi_RPi*-ARMv8-Bookworm.img.xz   # compare to the published value
   ```
2. Flash the `.img.xz` to the SD (`Etcher`, or `xz -dc img.xz | sudo dd of=/dev/sdX bs=4M conv=fsync`).
3. Re-mount the SD; locate the **FAT boot partition** mount path (it contains
   `config.txt`).
4. `cp provisioning/dietpi/secrets.env.example provisioning/dietpi/secrets.env`
   and fill in SSID, PSK, and a non-default `GLOBAL_PASSWORD`.
5. Validate offline first:
   ```bash
   provisioning/dietpi/prepare-sd.sh --check
   ```
6. Write to the SD:
   ```bash
   provisioning/dietpi/prepare-sd.sh /path/to/mounted/bootfs
   ```
7. Eject, insert into the Zero 2 W, power on. First boot is unattended
   (~3-4 min incl. a reboot).
8. `ssh dietpi@rpi-zero.local` — succeeds with your key; password SSH is refused.

## Failure modes

1. **No WiFi association** — SSID is 5 GHz-only/band-steered/hidden, or wrong
   PSK/country. Fix `secrets.env`, re-run `prepare-sd.sh`, re-flash config.
2. **`rpi-zero.local` won't resolve** — mDNS not available on your machine.
   Find the DHCP lease on your router, or:
   `ping -c1 rpi-zero.local || nmap -sn 192.168.1.0/24` (adjust subnet), then
   `ssh dietpi@<ip>`.
3. **Box never appears (~5 min)** — re-mount the SD, re-check
   `dietpi-wifi.txt` and `dietpi.txt`; optionally enable a serial console.
   Recovery is manual by design in Phase 1.

## Out of scope (later phases)

Tailscale, treadmill services & ordering, read-only/overlay root, `/data`,
no-initramfs, kernel trimming, the unified custom image, the existing Pi 4.
````

- [ ] **Step 2: Verify required sections exist**

Run:
```bash
grep -q 'Prerequisites' provisioning/dietpi/README.md \
  && grep -q 'Failure modes' provisioning/dietpi/README.md \
  && grep -q 'rpi-zero.local' provisioning/dietpi/README.md \
  && grep -q 'Out of scope' provisioning/dietpi/README.md && echo OK
```
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add provisioning/dietpi/README.md
# then ask the user to authorize: git commit -m "docs(provisioning): phase-1 runbook and failure modes"
```

---

### Task 10: Manual acceptance & baseline capture (documented runbook)

This task is not automatable (it flashes hardware and boots a Pi). It defines
exactly what the operator runs and the pass conditions. No code; produces the
committed `baseline-boot.txt` artifact.

**Files:**
- Create (on bring-up): `provisioning/dietpi/baseline-boot.txt`

- [ ] **Step 1: Provision and flash** following `provisioning/dietpi/README.md`
  steps 1-7, against a real broadcast 2.4 GHz WPA2 SSID.

- [ ] **Step 2: Acceptance — key login works (warm boot)**

Run (after the one-time first boot completes and the Pi has rebooted once):
```bash
ssh -o PreferredAuthentications=publickey -o BatchMode=yes dietpi@rpi-zero.local 'echo OK; uname -m'
```
Expected: `OK` then `aarch64`.

- [ ] **Step 3: Acceptance — password SSH is refused (key-only enforced)**

Run:
```bash
ssh -o PreferredAuthentications=password -o PubkeyAuthentication=no dietpi@rpi-zero.local true
```
Expected: connection denied / `Permission denied (publickey)` — i.e. password auth is rejected.

- [ ] **Step 4: Acceptance — default password no longer valid**

Confirm interactively that the DietPi default password `dietpi` is rejected
for `dietpi`/`root` (local or sudo); only the configured `GLOBAL_PASSWORD` works.

- [ ] **Step 5: Capture the baseline**

Run:
```bash
ssh dietpi@rpi-zero.local '
  echo "# DietPi Zero 2 W phase-1 baseline — $(date -u +%FT%TZ)";
  echo "## systemd-analyze"; systemd-analyze;
  echo "## blame"; systemd-analyze blame;
  echo "## critical-chain"; systemd-analyze critical-chain
' > provisioning/dietpi/baseline-boot.txt
cat provisioning/dietpi/baseline-boot.txt
```
Expected: a populated file (non-empty `systemd-analyze` line with a boot time).

- [ ] **Step 6: Commit the baseline artifact**

```bash
git add provisioning/dietpi/baseline-boot.txt
# then ask the user to authorize: git commit -m "chore(provisioning): record phase-1 boot baseline"
```

---

## Self-Review

**1. Spec coverage**

| Spec section | Task(s) |
|---|---|
| Architecture (dir layout, lib.sh split, secrets.env) | 1, 2-8 |
| Provisioning flow (flash → prepare-sd → boot → ssh) | 8, 9, 10 |
| `dietpi.txt` load-bearing entries (incl. password/key-only/wait-for-network/headless/custom-script-exec) | 6 |
| `Automation_Custom_Script.sh` (cmdline tokens, boot-path detect, masking, idempotent) | 7 |
| Secrets (array format, quoting/escaping, gitignore) | 1, 2, 8 |
| `prepare-sd.sh --check` (template-key verify, secrets, quote round-trip, WiFi preconditions) | 4, 5, 8 |
| Error handling/robustness (2.4 GHz/WPA2/broadcast, mDNS fallback, recovery) | 5, 9 |
| Testing & validation (unit tier + manual acceptance + baseline) | 2-8 (unit), 10 (manual+baseline) |
| Out of scope respected (no treadmill/overlay/Tailscale/Pi 4) | enforced — no such tasks |

No gaps.

**2. Placeholder scan:** `__INJECTED__` is an intentional, tested config
sentinel (Tasks 6/8), not a plan placeholder. No "TBD/TODO/handle edge cases"
language. Every code step contains complete code.

**3. Type/name consistency:** `lib.sh` function names are stable across tasks —
`dietpi_quote`, `inject_kv`, `verify_keys_against_template`, `wifi_precheck`,
`detect_fw_dir`, `cmdline_ensure_tokens` are defined once and referenced with
the same signatures in `prepare-sd.sh` and the tests. `REQUIRED_KEYS` in
`prepare-sd.sh` matches the key set asserted in Task 6 and the fixture in Task 4.

Self-review passed.
