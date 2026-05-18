#!/bin/dash
# dash: guaranteed on DietPi/Bookworm, no bashisms used — do not switch to bash.
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

# --- Fast-boot fold-back: kept layers L0 (Path A slot + sshd drop-in) + L1
# (validated cached-lease fast path) + periodic reachability recovery watchdog,
# plus bluez (the appliance needs Bluetooth; the base DietPi image lacks it).
# Idempotent (guarded by the applied marker). L2 (BSSID pin) was measured and
# REJECTED, so it is intentionally NOT installed/enabled here. POSIX/dash only.
if [ ! -f /boot/fastboot/.fastboot.applied ] && [ -n "$FW" ] && [ -f "$FW/fastboot.tgz" ]; then
  ok=1
  # F3: safe extraction — refuse absolute / ".." members, extract to a temp
  # dir with hardening flags, then copy only an allowlist of expected files.
  tx=$(mktemp -d)
  if tar tzf "$FW/fastboot.tgz" 2>/dev/null | grep -qE '^/|(^|/)\.\.(/|$)'; then
    logger -t fastboot "fold-back: refusing unsafe fastboot.tgz (absolute/.. paths)"; ok=0
  else
    tar xzf "$FW/fastboot.tgz" -C "$tx" --no-same-owner --no-same-permissions --no-overwrite-dir 2>/dev/null || ok=0
  fi
  src="$tx/fastboot"
  mkdir -p /boot/fastboot
  if [ "$ok" = 1 ] && [ -d "$src" ]; then
    for f in wifi-fastpath.sh fastboot-recover.sh treadmill-critical.target \
             fastboot-probe.service 10-ssh-fastboot.conf fastboot-net.service \
             fastboot-recover.service fastboot-recover.timer; do
      [ -f "$src/$f" ] && cp "$src/$f" /boot/fastboot/ || ok=0
    done
    chmod +x /boot/fastboot/*.sh 2>/dev/null || true
  else
    ok=0
  fi
  rm -rf "$tx"
  # bluez is OPTIONAL — log a failure (F5) but do NOT gate the applied marker.
  if ! command -v bluetoothctl >/dev/null 2>&1; then
    apt-get update -qq 2>/dev/null || true
    if DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends bluez 2>/dev/null; then
      systemctl enable bluetooth 2>/dev/null || true
    else
      logger -t fastboot "bluez install failed (appliance Bluetooth unavailable until reinstalled)"
    fi
  else
    systemctl enable bluetooth 2>/dev/null || true
  fi
  # F4: required steps gate the marker — half-installed never marks applied.
  if [ "$ok" = 1 ]; then
    cp /boot/fastboot/treadmill-critical.target /boot/fastboot/fastboot-probe.service /etc/systemd/system/ || ok=0
    mkdir -p /etc/systemd/system/ssh.service.d
    cp /boot/fastboot/10-ssh-fastboot.conf /etc/systemd/system/ssh.service.d/ || ok=0
    cp /boot/fastboot/fastboot-net.service /etc/systemd/system/ || ok=0
    cp /boot/fastboot/fastboot-recover.service /boot/fastboot/fastboot-recover.timer /etc/systemd/system/ || ok=0
    cp /etc/network/interfaces /boot/fastboot/interfaces.orig 2>/dev/null || true
    sed -i 's/^iface wlan0 inet dhcp/iface wlan0 inet manual/' /etc/network/interfaces || ok=0
    systemctl daemon-reload 2>/dev/null || true
    systemctl enable fastboot-probe.service treadmill-critical.target fastboot-net.service fastboot-recover.timer 2>/dev/null || ok=0
    systemctl add-wants treadmill-critical.target fastboot-probe.service 2>/dev/null || true
    systemctl add-wants multi-user.target treadmill-critical.target 2>/dev/null || true
  fi
  if [ "$ok" = 1 ]; then
    touch /boot/fastboot/.fastboot.applied
    echo "Automation_Custom_Script: fast-boot kept layers (L0+L1+watchdog) + bluez applied"
  else
    logger -t fastboot "fold-back incomplete — NOT marking applied; will retry next boot"
    echo "Automation_Custom_Script: fast-boot fold-back incomplete (will retry)" >&2
  fi
fi

# --- Full software family install (manifest-driven, idempotent) -------------
# Reuses the audited safe-extract posture from the fast-boot fold-back:
# refuse absolute / ".." members, extract to a temp dir with hardening
# flags, then install strictly via the shared manifest. dash/POSIX only.
if [ ! -f /boot/fastboot/.family.applied ] && [ -n "$FW" ] && [ -f "$FW/family.tgz" ]; then
  fok=1
  ftx=$(mktemp -d)
  if tar tzf "$FW/family.tgz" 2>/dev/null | grep -qE '^/|(^|/)\.\.(/|$)'; then
    logger -t fastboot "family: refusing unsafe family.tgz (absolute/.. paths)"; fok=0
  else
    tar xzf "$FW/family.tgz" -C "$ftx" --no-same-owner --no-same-permissions --no-overwrite-dir 2>/dev/null || fok=0
  fi
  # Defense-in-depth for the EXECUTE path (this block runs setup.sh, unlike
  # the fold-back which only copies a fixed allowlist): the listed-name guard
  # rejects absolute/.. names; additionally refuse ANY symlink member so a
  # clean-named symlink cannot redirect the cp/exec to outside $ftx. The real
  # payload (binaries, *.py, static, *.service, deploy/*) has no symlinks.
  if [ "$fok" = 1 ] && find "$ftx" -type l 2>/dev/null | grep -q .; then
    logger -t fastboot "family: refusing family.tgz with symlink members"; fok=0
  fi
  if [ "$fok" = 1 ] && [ -f "$ftx/deploy/setup.sh" ] && [ -f "$ftx/deploy/manifest.txt" ]; then
    # setup.sh is the single install path (manifest-driven) shared with the
    # live deployer; run it from the unpacked tree as the DietPi user.
    SETUP_USER=$(getent passwd 1000 | cut -d: -f1)
    [ -n "$SETUP_USER" ] || SETUP_USER=dietpi
    chmod +x "$ftx/deploy/setup.sh"
    mkdir -p "/home/$SETUP_USER/treadmill"
    cp -r "$ftx/build/." "/home/$SETUP_USER/treadmill/" 2>/dev/null || fok=0
    cp "$ftx/deploy/setup.sh" "$ftx/deploy/lib-artifacts.sh" \
       "$ftx/deploy/manifest.txt" "/home/$SETUP_USER/treadmill/" 2>/dev/null || fok=0
    chown -R "$SETUP_USER:$SETUP_USER" "/home/$SETUP_USER/treadmill" 2>/dev/null || true
    if [ "$fok" = 1 ]; then
      su - "$SETUP_USER" -c "cd ~/treadmill && bash setup.sh" 2>/dev/null || fok=0
    fi
  else
    fok=0
  fi
  rm -rf "$ftx"
  if [ "$fok" = 1 ]; then
    mkdir -p /boot/fastboot
    touch /boot/fastboot/.family.applied
    echo "Automation_Custom_Script: treadmill software family installed"
  else
    logger -t fastboot "family install incomplete — NOT marking applied; will retry next boot"
    echo "Automation_Custom_Script: family install incomplete (will retry)" >&2
  fi
fi

exit 0
