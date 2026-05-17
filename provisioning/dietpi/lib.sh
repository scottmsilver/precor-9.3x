#!/usr/bin/env bash
# Pure, sourceable helpers for prepare-sd.sh. No side effects at source time.

# Single-quote a value for a DietPi config line. A literal single quote
# becomes the POSIX sequence: close-quote, escaped-quote, reopen-quote.
dietpi_quote() {
  local s=${1-}
  s=${s//\'/\'\\\'\'}
  printf "'%s'" "$s"
}

# Replace the line beginning exactly "KEY=" with "KEY=VALUE". The value is
# written literally via awk ENVIRON (awk -v would process backslash escapes
# and corrupt passwords). Missing key => error, so an upstream DietPi rename
# is caught instead of silently ignored. awk index()==1 is the single
# matcher for detect + replace (no regex; prefix-safe; no key metachar risk).
# Accepted LOW (security audit 2026-05-16): value transits _IKV_VAL in the awk
# child env, briefly readable via /proc/<pid>/environ by same-UID/root. Nil
# escalation in the single-operator threat model (secret is also in secrets.env,
# dietpi.txt, and on the SD). Revisit (fd/temp-file) if ever run in CI/multi-tenant.
inject_kv() {
  local file=$1 key=$2 value=$3 tmp
  [ -n "$key" ] || { echo "inject_kv: empty key" >&2; return 1; }
  case $value in *$'\n'*) echo "inject_kv: value contains a newline" >&2; return 1 ;; esac
  tmp=$(mktemp)
  if ! _IKV_VAL="$value" awk -v k="$key" '
    BEGIN { val = ENVIRON["_IKV_VAL"]; found = 0 }
    index($0, k "=") == 1 { print k "=" val; found = 1; next }
    { print }
    END { exit(found ? 0 : 1) }
  ' "$file" > "$tmp"; then
    echo "inject_kv: key '$key' not in $file" >&2
    rm -f "$tmp"
    return 1
  fi
  mv "$tmp" "$file"
}

# Assert every given key exists as a "KEY=" line in the DietPi template.
# Uses awk index()==1 (no regex; no key-metachar risk; matches inject_kv).
# Lists ALL missing keys at once so an upstream rename is loud, not silent.
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

# Enforce Phase-1 WiFi preconditions: non-empty SSID, WPA-PSK only
# (no first-boot WPA3/EAP path), PSK is an 8-63 char passphrase or 64 hex.
# NOTE: cannot detect hidden-SSID or 2.4-vs-5 GHz — those fail at runtime.
wifi_precheck() {
  local ssid=${1-} psk=${2-} keymgr=${3-} n
  [ -n "$ssid" ] || { echo "wifi: SSID is empty" >&2; return 1; }
  [ "$keymgr" = "WPA-PSK" ] || { echo "wifi: KEYMGR must be WPA-PSK (got '$keymgr'); WPA3-only/EAP unsupported for headless first boot" >&2; return 1; }
  case $ssid$psk in *$'\n'*) echo "wifi: SSID/PSK must not contain a newline" >&2; return 1 ;; esac
  n=${#psk}
  if [ "$n" -ge 8 ] && [ "$n" -le 63 ]; then return 0; fi
  if [ "$n" -eq 64 ] && [[ "$psk" =~ ^[0-9a-fA-F]{64}$ ]]; then return 0; fi
  echo "wifi: PSK must be an 8-63 char passphrase or 64 hex chars (got length $n)" >&2
  return 1
}

# Parse a secrets file as DATA — never as shell. Replaces `source` so a
# crafted value like WIFI_PSK="$(rm -rf x)" is a literal string, not code.
# Allowlists keys, strips at most one matching outer quote pair, performs NO
# expansion / command substitution / eval. Any non-blank, non-comment line
# that is not an allowlisted assignment is a hard failure (fail closed).
# A line is skipped ONLY if, after stripping leading whitespace, it is (a)
# empty/whitespace-only, or (b) its first non-whitespace character is '#'.
# A '#' that appears INSIDE an allowlisted value is preserved verbatim.
# CRLF line endings are tolerated (trailing \r stripped before parsing).
load_secrets() {
  local file=$1 line key val trimmed
  [ -f "$file" ] || { echo "secrets file not found: $file" >&2; return 1; }
  while IFS= read -r line || [ -n "$line" ]; do
    line=${line%$'\r'}                               # FIX A2: strip trailing CR (CRLF files)
    trimmed=${line#"${line%%[![:space:]]*}"}         # strip leading whitespace for classification only
    [ -z "$trimmed" ] && continue                   # blank / whitespace-only
    case $trimmed in '#'*) continue ;; esac         # full-line comment (first non-ws char is #)
    if [[ "$line" =~ ^[[:space:]]*(export[[:space:]]+)?(WIFI_SSID|WIFI_PSK|WIFI_KEYMGR|GLOBAL_PASSWORD)= ]]; then
      key=${BASH_REMATCH[2]}
      val=${line#*=}                               # everything after first '='
      case "$val" in
        '"'*'"') [ "${#val}" -ge 2 ] && val=${val:1:${#val}-2} ;;
        "'"*"'") [ "${#val}" -ge 2 ] && val=${val:1:${#val}-2} ;;
      esac
      case "$key" in
        WIFI_SSID)       printf -v WIFI_SSID       '%s' "$val" ;;
        WIFI_PSK)        printf -v WIFI_PSK        '%s' "$val" ;;
        WIFI_KEYMGR)     printf -v WIFI_KEYMGR     '%s' "$val" ;;
        GLOBAL_PASSWORD) printf -v GLOBAL_PASSWORD '%s' "$val" ;;
      esac
    else
      echo "secrets file: unrecognized line: $line" >&2
      return 1
    fi
  done < "$file"
}

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
