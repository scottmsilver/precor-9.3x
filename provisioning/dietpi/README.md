# Building the OS Image for the Treadmill Pi

This is how you build the base OS image the treadmill Pi runs. Flash the
image, boot, confirm SSH — then `make deploy` (or `make image` to bake the
full software family in). The software layer never hand-installs OS
packages; the deploy step auto-installs the runtime prerequisites.

**DietPi (Debian 12, 64-bit) is the reference implementation** because it
boots fast and minimal on a 512 MB Zero 2 W, but nothing here is
DietPi-specific in principle: the approach is *reproducible, audited config
injection into a stock image, built entirely in userspace*. For other boards
or a different Pi OS, keep the method and adapt the hardware-specific values
(regulatory domain, board image, WiFi). The deploy/setup step then installs
`python3` + `libpigpio1` on whatever Debian-based OS you flashed.

The detailed research and rationale behind these steps lives in the design
spec: `docs/superpowers/specs/2026-05-16-pi-zero2w-boot-to-ssh-design.md`
(and the unified-image spec `2026-05-17-deploy-treadmill-zero2w-design.md`).

## Prerequisites (hard — a headless box can't tell you it failed)

- A **broadcast, 2.4 GHz, WPA2-PSK** WiFi network. The Zero 2 W has no 5 GHz.
  WPA3-only and hidden SSIDs fail on first boot.
- An SSH keypair on this machine (`~/.ssh/id_ed25519.pub` preferred).

## Non-US deployments

The WiFi regulatory domain is pinned to `US` in `provisioning/dietpi/dietpi.txt`
(`AUTO_SETUP_NET_WIFI_COUNTRY_CODE=US`). Outside the US, change that value to
your ISO 3166-1 country code before flashing — a wrong regulatory domain can
prevent WiFi association on a headless box.

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
   and fill in `WIFI_SSID`, `WIFI_PSK`, and a non-default `GLOBAL_PASSWORD`.
   Leave `WIFI_KEYMGR=WPA-PSK` unchanged for a standard WPA2 network.
5. Validate config (fetches the live DietPi template to check for key renames —
   network required, or pass `--template /path/to/dietpi.txt`):
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

## Option B — Build a ready-to-flash image (no root)

`build-image.sh` produces a fully configured `.img` entirely in userspace
(via `mtools`; no loop device, no `mount`, no root for the build itself):

```bash
provisioning/dietpi/build-image.sh \
  [--image-dir DIR] [--secrets FILE] [--pubkey FILE] \
  [--flash /dev/sdX] [--i-understand] [--force]
```

- Downloads the DietPi `.img.xz` (cached) and verifies it against the
  `.sha256` sidecar, **pinned to the exact image filename** (a sidecar
  pointing at another file is rejected).
- Decompresses, then injects the same audited `prepare-sd.sh` config into the
  image's FAT boot partition with `mtools`.
- Build artifacts (`.img`, `.xz`, `.sha256`, build dir) are owner-only —
  they embed the WiFi PSK, device password, and your SSH key.
- `--flash /dev/sdX` is the **only** step that uses `sudo` (just the raw
  `dd`). It canonicalizes the path, requires a whole **disk** (not a
  partition), refuses the system/root disk, loop/ram/zram, and mounted or
  non-removable devices (the latter overridable with `--i-understand`),
  requires you to type the exact device path, and re-checks the device
  immediately before writing. `--force` allows overwriting an existing `.img`.

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

## What this doc covers vs. the rest of deploy

This doc is the **OS layer only** — getting a clean, reproducible,
SSH-reachable Debian Pi onto the hardware. The **software layer** (the four
treadmill services, the venv, OS-prereq auto-install, Path A early start,
the headroom gate) is owned by the deploy pipeline:

- `make deploy` — cross-build + rsync the software onto a booted Pi
- `make image` — bake the full software family *into* a flashable `.img`
  (this `provisioning/` toolkit + the deploy manifest, so a flashed Pi and
  an rsync'd Pi are byte-identical)
- `make deploy-key` — push the per-device Gemini key (a secret; never
  rsync'd by a normal deploy)
- `make ship-check` — end-to-end acceptance verdict on a live device

See [`../../CLAUDE.md`](../../CLAUDE.md) (Deployment) and
`docs/superpowers/specs/2026-05-17-deploy-treadmill-zero2w-design.md`.

Genuinely still out of scope: read-only/overlay root, `/data` partition,
no-initramfs / kernel trimming (image-size optimizations only pursued if the
headroom gate ever fails on real hardware — it currently passes ~8× over).
