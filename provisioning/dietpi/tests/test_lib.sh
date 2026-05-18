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
  pass "dietpi_quote: [$in]"
}
roundtrip "plainSSID"
roundtrip "has space"
roundtrip "tick'inside"
roundtrip 'dollar$and`tick`'
roundtrip 'back\slash'
roundtrip "it's a 'test'"
roundtrip ""
# no-arg guard path (set -u safe): must produce '' and round-trip to empty
naq=$(dietpi_quote)
eval "naqv=$naq"
[ "$naqv" = "" ] || fail "dietpi_quote no-arg: out=[$naq] got=[$naqv]"
pass "dietpi_quote: no-arg guard"

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
inject_kv "$tmpf" B 'pass\word' || fail "inject_kv backslash returned non-zero"
grep -qxF 'B=pass\word' "$tmpf" || fail "inject_kv corrupted backslash value"
inject_kv "$tmpf" "" x 2>/dev/null && fail "inject_kv accepted empty key"
inject_kv "$tmpf" B "$(printf 'a\nb')" 2>/dev/null && fail "inject_kv accepted newline value"
rm -f "$tmpf"
pass "inject_kv exact-match + missing-key error + idempotent"

# --- verify_keys_against_template: all present => ok; any missing => error ---
FIX="$HERE/fixtures/template-sample.txt"
verify_keys_against_template "$FIX" AUTO_SETUP_AUTOMATED AUTO_SETUP_BOOT_WAIT_FOR_NETWORK \
  SOFTWARE_DISABLE_SSH_PASSWORD_LOGINS AUTO_SETUP_GLOBAL_PASSWORD \
  || fail "verify_keys_against_template rejected keys that exist"
verify_keys_against_template "$FIX" CONFIG_BOOT_WAIT_FOR_NETWORK 2>/dev/null \
  && fail "verify_keys_against_template accepted a non-existent (renamed) key"
pass "verify_keys_against_template detects renamed/missing keys"

# --- verify_keys_against_template: commented (default-off) keys must be accepted ---
# Build a temp template with: one ACTIVE key, one COMMENTED key (as DietPi ships it),
# and NOT containing AUTO_SETUP_ACCEPT_LICENSE.
tmp_vk=$(mktemp)
printf 'AUTO_SETUP_AUTOMATED=1\n#AUTO_SETUP_SSH_PUBKEY=ssh-ed25519 AAAA... x\n' > "$tmp_vk"
# Commented key must count as present (this FAILS before FIX 1, proving the bug):
verify_keys_against_template "$tmp_vk" AUTO_SETUP_AUTOMATED AUTO_SETUP_SSH_PUBKEY \
  || fail "verify_keys_against_template rejected a commented (default-off) key as absent"
# Truly-absent key must still be rejected (guards against over-broad matching):
verify_keys_against_template "$tmp_vk" AUTO_SETUP_ACCEPT_LICENSE 2>/dev/null \
  && fail "verify_keys_against_template accepted a truly-absent key AUTO_SETUP_ACCEPT_LICENSE"
rm -f "$tmp_vk"
pass "verify_keys_against_template accepts commented (default-off) keys, still rejects absent"

# --- wifi_precheck: enforce Phase-1 preconditions ---
wifi_precheck "MySSID" "agoodpassphrase" "WPA-PSK" || fail "valid WPA2 passphrase rejected"
wifi_precheck "MySSID" "$(printf 'a%.0s' {1..64} | tr a 0)" "WPA-PSK" \
  || fail "valid 64-hex PSK rejected"
wifi_precheck "" "agoodpassphrase" "WPA-PSK" 2>/dev/null && fail "empty SSID accepted"
wifi_precheck "MySSID" "short" "WPA-PSK" 2>/dev/null && fail "too-short PSK accepted"
wifi_precheck "MySSID" "agoodpassphrase" "WPA-EAP" 2>/dev/null && fail "non-WPA-PSK accepted"
wifi_precheck "MySSID" "agoodpassphrase" "WEP" 2>/dev/null && fail "WEP accepted"
wifi_precheck "MySSID" "agoodpassphrase" "NONE" 2>/dev/null && fail "open network accepted"
wifi_precheck "$(printf 'a\nb')" "agoodpassphrase" "WPA-PSK" 2>/dev/null && fail "newline SSID accepted"
pass "wifi_precheck enforces SSID/PSK/keymgr rules"

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
grep -qx 'AUTO_SETUP_AUTOMATED=1' "$DT" || fail "AUTO_SETUP_AUTOMATED=1 missing — interactive first-run on a headless Pi = brick"
grep -qE '^CONFIG_BOOT_WAIT_FOR_NETWORK=' "$DT" && fail "stale CONFIG_BOOT_WAIT_FOR_NETWORK present"
pass "dietpi.txt has all required keys, correct values, placeholders"

# --- detect_fw_dir: finds the dir containing cmdline.txt ---
d=$(mktemp -d)
mkdir -p "$d/firmware"; : > "$d/firmware/cmdline.txt"
[ "$(detect_fw_dir "$d")" = "$d/firmware" ] || fail "detect_fw_dir missed /firmware"
: > "$d/cmdline.txt"   # both root and firmware now have cmdline.txt
[ "$(detect_fw_dir "$d")" = "$d" ] || fail "detect_fw_dir wrong precedence (root must win)"
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

# --- prepare-sd.sh rejects bad pubkey and non-boot target ---
PSG="$HERE/../prepare-sd.sh"; wg=$(mktemp -d)
cp "$HERE/fixtures/template-sample.txt" "$wg/t.txt"
printf 'WIFI_SSID="N";WIFI_PSK="abcdefgh";WIFI_KEYMGR="WPA-PSK";GLOBAL_PASSWORD="notdietpi"\n' > "$wg/s.env"
: > "$wg/empty.pub"
DIETPI_DIR="$HERE/.." bash "$PSG" --check --secrets "$wg/s.env" --pubkey "$wg/empty.pub" --template "$wg/t.txt" 2>/dev/null \
  && fail "accepted an empty/invalid pubkey"
printf 'ssh-ed25519\n' > "$wg/typeonly.pub"
DIETPI_DIR="$HERE/.." bash "$PSG" --check --secrets "$wg/s.env" --pubkey "$wg/typeonly.pub" --template "$wg/t.txt" 2>/dev/null \
  && fail "accepted a bare key-type token (no key material)"
printf -- '-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----\n' > "$wg/priv.pub"
DIETPI_DIR="$HERE/.." bash "$PSG" --check --secrets "$wg/s.env" --pubkey "$wg/priv.pub" --template "$wg/t.txt" 2>/dev/null \
  && fail "accepted a private key file"
ssh-keygen -t ed25519 -N '' -q -f "$wg/realkey" </dev/null; cp "$wg/realkey.pub" "$wg/ok.pub"; nb="$wg/notboot"; mkdir -p "$nb"
DIETPI_DIR="$HERE/.." bash "$PSG" "$nb" --secrets "$wg/s.env" --pubkey "$wg/ok.pub" --template "$wg/t.txt" 2>/dev/null \
  && fail "wrote to a dir with no config.txt"
rm -rf "$wg"
pass "prepare-sd.sh rejects bad pubkey + non-boot target"

# --- prepare-sd.sh write mode: injects creds literally, quotes wifi, +x script ---
PSW="$HERE/../prepare-sd.sh"
w2=$(mktemp -d); fb="$w2/boot"; mkdir -p "$fb"
: > "$fb/config.txt"
cp "$HERE/fixtures/template-sample.txt" "$w2/template.txt"
cat > "$w2/secrets.env" <<'EOS'
WIFI_SSID="Net'24"
WIFI_PSK="pa\\ss'wo\$rd"
WIFI_KEYMGR="WPA-PSK"
GLOBAL_PASSWORD="p\\ass'w\$d"
EOS
ssh-keygen -t ed25519 -N '' -q -f "$w2/realkey" </dev/null; cp "$w2/realkey.pub" "$w2/id.pub"
DIETPI_DIR="$HERE/.." bash "$PSW" "$fb" --secrets "$w2/secrets.env" \
  --pubkey "$w2/id.pub" --template "$w2/template.txt" >/dev/null \
  || fail "write mode failed"
grep -qE '^[A-Za-z_]+=__INJECTED__' "$fb/dietpi.txt" && fail "un-substituted placeholder value left in dietpi.txt"
# Expected values computed via the SAME data parser prepare-sd.sh now uses
# (load_secrets, not `source`) so backslash/dollar survive VERBATIM — a
# stronger property than source's escape-processing (S1: secrets are data).
( set +u; load_secrets "$w2/secrets.env"
  want_pk=$(sed -e 's/\r$//' -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' "$w2/id.pub" | grep -m1 .)
  gk=$(awk 'index($0,"AUTO_SETUP_SSH_PUBKEY=")==1{sub(/^AUTO_SETUP_SSH_PUBKEY=/,"");print;exit}' "$fb/dietpi.txt")
  [ "$gk" = "$want_pk" ] || { echo "pubkey got=[$gk] want=[$want_pk]"; exit 1; }
  gp=$(awk 'index($0,"AUTO_SETUP_GLOBAL_PASSWORD=")==1{sub(/^AUTO_SETUP_GLOBAL_PASSWORD=/,"");print;exit}' "$fb/dietpi.txt")
  [ "$gp" = "$GLOBAL_PASSWORD" ] || { echo "pw got=[$gp] want=[$GLOBAL_PASSWORD]"; exit 1; }
  unset aWIFI_SSID aWIFI_KEY aWIFI_KEYMGR
  . "$fb/dietpi-wifi.txt"
  [ "$aWIFI_SSID" = "$WIFI_SSID" ] || { echo "ssid got=[$aWIFI_SSID]"; exit 1; }
  [ "$aWIFI_KEY" = "$WIFI_PSK" ] || { echo "psk got=[$aWIFI_KEY] want=[$WIFI_PSK]"; exit 1; }
) || fail "write mode: injected/quoted values not literal-correct"
[ -x "$fb/Automation_Custom_Script.sh" ] || fail "automation script not copied +x"
rm -rf "$w2"
pass "prepare-sd.sh write mode injects + quotes correctly"

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
ssh-keygen -t ed25519 -N '' -q -f "$work/realkey" </dev/null; cp "$work/realkey.pub" "$work/id.pub"
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

# --- load_secrets (S1): data parser, never executes the secrets file ---
ls_dir=$(mktemp -d)
# Malicious: a command-substitution value must NOT execute, and the literal
# string must survive verbatim (sourcing would run touch + lose the literal).
cat > "$ls_dir/mal.env" <<'EOS'
WIFI_SSID="MyNet"
WIFI_PSK="$(touch SENTINEL_FILE)"
WIFI_KEYMGR="WPA-PSK"
GLOBAL_PASSWORD="notdietpi"
EOS
(
  cd "$ls_dir"
  unset WIFI_SSID WIFI_PSK WIFI_KEYMGR GLOBAL_PASSWORD
  load_secrets "$ls_dir/mal.env" || exit 3
  [ ! -e "$ls_dir/SENTINEL_FILE" ] || exit 1
  [ "$WIFI_PSK" = '$(touch SENTINEL_FILE)' ] || exit 2
) || fail "load_secrets executed/expanded a command-substitution value (rc $?)"
[ ! -e "$ls_dir/SENTINEL_FILE" ] || fail "load_secrets created SENTINEL_FILE (code execution)"

# Non-allowlisted line: must hard-fail and never execute it.
cat > "$ls_dir/evil.env" <<'EOS'
WIFI_SSID="MyNet"
WIFI_PSK="agoodpassphrase"
WIFI_KEYMGR="WPA-PSK"
GLOBAL_PASSWORD="notdietpi"
EVIL=$(touch X)
EOS
( cd "$ls_dir"; load_secrets "$ls_dir/evil.env" 2>/dev/null ) \
  && fail "load_secrets accepted a non-allowlisted line"
[ ! -e "$ls_dir/X" ] || fail "load_secrets executed a non-allowlisted line (created X)"

# Happy: quoted, single-quoted, bare (with spaces + '#'), and 'export ' form.
cat > "$ls_dir/ok.env" <<'EOS'
# a comment
   # indented comment

export WIFI_SSID=foo
WIFI_PSK='single quoted value'
WIFI_KEYMGR="WPA-PSK"
GLOBAL_PASSWORD=bare value with # hash and  spaces
EOS
(
  unset WIFI_SSID WIFI_PSK WIFI_KEYMGR GLOBAL_PASSWORD
  load_secrets "$ls_dir/ok.env" || exit 9
  [ "$WIFI_SSID" = "foo" ] || { echo "ssid=[$WIFI_SSID]"; exit 1; }
  [ "$WIFI_PSK" = "single quoted value" ] || { echo "psk=[$WIFI_PSK]"; exit 2; }
  [ "$WIFI_KEYMGR" = "WPA-PSK" ] || { echo "km=[$WIFI_KEYMGR]"; exit 3; }
  [ "$GLOBAL_PASSWORD" = 'bare value with # hash and  spaces' ] \
    || { echo "gp=[$GLOBAL_PASSWORD]"; exit 4; }
) || fail "load_secrets did not parse valid quoted/bare/export values correctly"
rm -rf "$ls_dir"
pass "load_secrets parses as data (no exec), allowlists keys, strips one quote pair"

# --- load_secrets: fail-closed gap (FIX A), comment/blank classification, #-in-values, CRLF ---
fc_dir=$(mktemp -d)

# (1) Fail-closed regression: a line with leading whitespace + embedded # must hard-fail,
#     NOT be silently skipped as a "comment". Before FIX A this returns 0 (gap).
printf 'WIFI_SSID=testnet\nWIFI_PSK=agoodpass\nWIFI_KEYMGR=WPA-PSK\nGLOBAL_PASSWORD=testpw\n  EVIL=$(touch %s) # trailing comment\n' \
  "$fc_dir/SENT_A" > "$fc_dir/fc.env"
( cd "$fc_dir"; unset WIFI_SSID WIFI_PSK WIFI_KEYMGR GLOBAL_PASSWORD
  load_secrets "$fc_dir/fc.env" 2>/dev/null && exit 1; exit 0 ) \
  || fail "load_secrets silently skipped a non-comment line with embedded # (fail-closed gap)"
[ ! -e "$fc_dir/SENT_A" ] || fail "load_secrets executed embedded command in non-comment line"
pass "load_secrets fail-closed: embedded-# non-comment line is rejected, not skipped"

# (2) Legitimate full-line comments and blank/whitespace-only lines still skipped.
printf '# c1\n   # c2 with $(stuff)\n\n   \nexport WIFI_SSID=net1\nWIFI_PSK=agoodpass\nWIFI_KEYMGR=WPA-PSK\nGLOBAL_PASSWORD=testpw\n' \
  > "$fc_dir/comments.env"
( unset WIFI_SSID WIFI_PSK WIFI_KEYMGR GLOBAL_PASSWORD
  load_secrets "$fc_dir/comments.env" || exit 9
  [ "$WIFI_SSID" = "net1" ] || { echo "ssid=[$WIFI_SSID]"; exit 1; }
  [ "$WIFI_PSK" = "agoodpass" ] || { echo "psk=[$WIFI_PSK]"; exit 2; }
  [ "$WIFI_KEYMGR" = "WPA-PSK" ] || { echo "km=[$WIFI_KEYMGR]"; exit 3; }
  [ "$GLOBAL_PASSWORD" = "testpw" ] || { echo "gp=[$GLOBAL_PASSWORD]"; exit 4; }
) || fail "load_secrets rejected a full-line comment or blank/whitespace-only line"
pass "load_secrets skips full-line comments (# first non-ws char) and blank lines"

# (3) # inside allowlisted values must be preserved verbatim (not treated as comment).
printf 'WIFI_SSID=my#net\nWIFI_PSK="a # b"\nWIFI_KEYMGR=WPA-PSK\nGLOBAL_PASSWORD=testpw\n' \
  > "$fc_dir/hash_val.env"
( unset WIFI_SSID WIFI_PSK WIFI_KEYMGR GLOBAL_PASSWORD
  load_secrets "$fc_dir/hash_val.env" || exit 9
  [ "$WIFI_SSID" = "my#net" ] || { echo "ssid=[$WIFI_SSID]"; exit 1; }
  [ "$WIFI_PSK" = "a # b" ] || { echo "psk=[$WIFI_PSK]"; exit 2; }
) || fail "load_secrets corrupted a # character inside an allowlisted value"
pass "load_secrets preserves # inside allowlisted values verbatim"

# (4) CRLF (FIX A2): values must have no trailing \r after load.
printf 'WIFI_SSID=ssid\r\nWIFI_PSK=psk\r\nWIFI_KEYMGR=WPA-PSK\r\nGLOBAL_PASSWORD=pw\r\n' \
  > "$fc_dir/crlf.env"
( unset WIFI_SSID WIFI_PSK WIFI_KEYMGR GLOBAL_PASSWORD
  load_secrets "$fc_dir/crlf.env" || exit 9
  [ "$WIFI_KEYMGR" = "WPA-PSK" ] || { echo "km=[$WIFI_KEYMGR] (has trailing cr?)" | cat -A; exit 1; }
  [ "$WIFI_SSID" = "ssid" ] || { echo "ssid=[$WIFI_SSID]" | cat -A; exit 2; }
  [ "$WIFI_PSK" = "psk" ] || { echo "psk=[$WIFI_PSK]" | cat -A; exit 3; }
  [ "$GLOBAL_PASSWORD" = "pw" ] || { echo "gp=[$GLOBAL_PASSWORD]" | cat -A; exit 4; }
) || fail "load_secrets left a trailing carriage return from CRLF input"
pass "load_secrets strips CRLF line endings (no trailing \\r in values)"

rm -rf "$fc_dir"
pass "load_secrets: fail-closed / comment classification / #-in-values / CRLF"

echo "ALL TESTS PASSED"
