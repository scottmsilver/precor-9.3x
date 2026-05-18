#!/usr/bin/env bash
# Snapshot/revert /etc + boot FAT on the Pi. Each snapshot is a tar pulled to the host.
set -u
HOST="${FB_HOST:-192.168.1.206}"; KEY="${FB_KEY:-$HOME/.ssh/id_ed25519}"
SSHO="-i $KEY -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=8"
DIR="$HOME/fastboot-snapshots"
usage(){ echo "fbsnap.sh take <label> | restore <label> | list   (label: [A-Za-z0-9._-], no path traversal)"; }
# Snapshots bundle /etc + boot files — they are SECRET-BEARING (WiFi PSK,
# device keys). Stored 0600 in a 0700 dir; labels are strictly validated so a
# crafted label cannot traverse out of $DIR.
valid_label(){ case "$1" in ''|*[!A-Za-z0-9._-]*|.|..) return 1;; esac; return 0; }
case "${1:-}" in
  -h|--help) usage; exit 0 ;;
  take) valid_label "${2:-}" || { echo "invalid label (allowed: A-Za-z0-9._-)" >&2; usage; exit 2; }
        mkdir -p "$DIR"; chmod 700 "$DIR"
        ( umask 077; ssh $SSHO root@"$HOST" 'BD=$([ -d /boot/firmware ] && echo /boot/firmware || echo /boot); tar czf - --ignore-failed-read --warning=no-failed-read /etc "$BD/cmdline.txt" "$BD/config.txt" 2>/dev/null' > "$DIR/$2.tgz" )
        [ -s "$DIR/$2.tgz" ] && { chmod 600 "$DIR/$2.tgz"; echo "snapshot saved (0600): $DIR/$2.tgz"; } || { echo "snapshot FAILED" >&2; exit 1; } ;;
  restore) valid_label "${2:-}" || { echo "invalid label" >&2; exit 2; }
        [ -f "$DIR/$2.tgz" ] || { echo "no snapshot $2" >&2; exit 1; }
        ssh $SSHO root@"$HOST" 'tar xzf - -C /' < "$DIR/$2.tgz" \
          && ssh $SSHO root@"$HOST" 'systemctl daemon-reexec 2>/dev/null || systemctl daemon-reload; nohup sh -c "sleep 2; systemctl reboot" >/dev/null 2>&1 &' \
          && echo "restored $2 + reboot scheduled" || { echo "restore FAILED" >&2; exit 1; } ;;
  list) ls -1 "$DIR" 2>/dev/null ;;
  *) usage; exit 2 ;;
esac
