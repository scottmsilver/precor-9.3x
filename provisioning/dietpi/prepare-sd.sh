#!/usr/bin/env bash
# Phase-1 SD provisioning for the Pi Zero 2 W.
#
#   prepare-sd.sh --check [opts]            # validate config, no SD needed
#                                           # (needs network for the live
#                                           #  DietPi template, or --template)
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
    --secrets)   [ $# -ge 2 ] || { echo "usage: --secrets requires a FILE" >&2; exit 2; }; secrets=$2; shift ;;
    --pubkey)    [ $# -ge 2 ] || { echo "usage: --pubkey requires a FILE"  >&2; exit 2; }; pubkey=$2;  shift ;;
    --template)  [ $# -ge 2 ] || { echo "usage: --template requires a FILE" >&2; exit 2; }; template=$2; shift ;;
    -*)          echo "unknown option: $1" >&2; exit 2 ;;
    *)           [ -z "$boot" ] || { echo "only one boot path allowed (got '$boot' then '$1')" >&2; exit 2; }; mode=write; boot=$1 ;;
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
# C1: require a real single-line SSH *public* key. ssh-keygen validates the
# actual key material (rejects empty / bare type token / non-base64 body);
# the type allowlist additionally rejects private keys (first line
# -----BEGIN...) and unsupported types.
command -v ssh-keygen >/dev/null 2>&1 \
  || { echo "ssh-keygen not found — required to validate the SSH public key" >&2; exit 1; }
ssh-keygen -l -f "$pubkey" >/dev/null 2>&1 \
  || { echo "not a valid SSH public key (ssh-keygen rejected it): $pubkey" >&2; exit 1; }
read -r _pk_type _pk_blob _ < "$pubkey" 2>/dev/null || true
case "$_pk_type" in
  ssh-ed25519|ssh-rsa|ssh-dss|ecdsa-sha2-nistp256|ecdsa-sha2-nistp384|ecdsa-sha2-nistp521|sk-ssh-ed25519@openssh.com|sk-ecdsa-sha2-nistp256@openssh.com) ;;
  *) echo "not a valid SSH public key (first field: '${_pk_type:-<empty>}'): $pubkey" >&2; exit 1 ;;
esac
[ -n "$_pk_blob" ] || { echo "SSH public key has no key material: $pubkey" >&2; exit 1; }
[ -f "$secrets" ] || { echo "secrets file not found: $secrets (copy secrets.env.example)" >&2; exit 1; }

# Load secrets (WIFI_SSID, WIFI_PSK, WIFI_KEYMGR, GLOBAL_PASSWORD) as DATA.
# load_secrets parses, never executes — a crafted value cannot run code.
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
    rm -f "$tmp_tmpl"
    echo "ERROR: could not fetch the live DietPi template ($TEMPLATE_URL)." >&2
    echo "       Key-rename verification is mandatory (prevents a headless brick)." >&2
    echo "       Re-run online, or pass --template /path/to/known-good/dietpi.txt" >&2
    exit 1
  fi
fi
verify_keys_against_template "$template" "${REQUIRED_KEYS[@]}"
[ -n "$tmp_tmpl" ] && rm -f "$tmp_tmpl"

if [ "$mode" = check ]; then
  echo "CHECK OK: secrets valid, pubkey present, keys verified"
  exit 0
fi

# write mode: stage finalized files, then copy to the FAT boot partition.
[ -d "$boot" ] || { echo "boot path not a directory: $boot" >&2; exit 1; }
[ -f "$boot/config.txt" ] || { echo "refusing: $boot has no config.txt — not a Raspberry Pi FAT boot partition" >&2; exit 1; }
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT

cp "$DIETPI_DIR/dietpi.txt" "$stage/dietpi.txt"
inject_kv "$stage/dietpi.txt" AUTO_SETUP_SSH_PUBKEY "$(sed -e 's/\r$//' -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' "$pubkey" | grep -m1 .)"
inject_kv "$stage/dietpi.txt" AUTO_SETUP_GLOBAL_PASSWORD "$GLOBAL_PASSWORD"

if grep -qE '^[A-Za-z_]+=__INJECTED__' "$stage/dietpi.txt"; then
  echo "internal error: a placeholder survived injection — refusing to write" >&2; exit 1
fi
grep -qE '^AUTO_SETUP_SSH_PUBKEY=.+' "$stage/dietpi.txt" \
  || { echo "internal error: empty pubkey after injection — refusing to write" >&2; exit 1; }

{
  printf 'aWIFI_SSID[0]=%s\n'   "$(dietpi_quote "$WIFI_SSID")"
  printf 'aWIFI_KEY[0]=%s\n'    "$(dietpi_quote "$WIFI_PSK")"
  printf 'aWIFI_KEYMGR[0]=%s\n' "$(dietpi_quote "$WIFI_KEYMGR")"
} > "$stage/dietpi-wifi.txt"

cp "$DIETPI_DIR/Automation_Custom_Script.sh" "$stage/Automation_Custom_Script.sh"
chmod +x "$stage/Automation_Custom_Script.sh"

# Fast-boot artifacts: ship the whole fastboot/ dir as one tarball so the
# first-boot Automation_Custom_Script.sh can unpack + install the kept layers.
if [ -d "$DIETPI_DIR/fastboot" ]; then
  tar czf "$stage/fastboot.tgz" -C "$DIETPI_DIR" fastboot
fi

# DietPi reads these from the FAT partition root.
cp "$stage/dietpi.txt" "$stage/dietpi-wifi.txt" "$stage/Automation_Custom_Script.sh" "$boot/"
extra=""
if [ -f "$stage/fastboot.tgz" ]; then cp "$stage/fastboot.tgz" "$boot/"; extra=", fastboot.tgz"; fi
sync
echo "WROTE: dietpi.txt, dietpi-wifi.txt, Automation_Custom_Script.sh${extra} -> $boot"
echo "Eject, boot the Zero 2 W, wait ~3-4 min, then: ssh dietpi@rpi-zero.local"
