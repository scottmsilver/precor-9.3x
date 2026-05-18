# Design: Pi Zero 2 W — Fast Boot to SSH (Phase 1)

**Date:** 2026-05-16
**Status:** Approved (design); implementation plan pending
**Scope:** Phase 1 only — a headless DietPi 64-bit install on a Pi Zero 2 W that
boots unattended to a key-authenticated SSH login. Treadmill stack, aggressive
boot tuning, and the unified-image work are explicitly deferred.

## Context

The project currently deploys to a Pi 4 reachable as host `rpi` over Tailscale
(`make deploy` → rsync `build/` → build C++ on Pi → `setup.sh`). A **second,
separate** Pi Zero 2 W is being brought up. Long-term goal: one arm64 image that
runs on both boards (both are ARMv8/aarch64; the project already cross-compiles
the Rust daemons to `aarch64-unknown-linux-gnu`), aggressively tuned to a
~4–6 s boot, with the safety-critical `treadmill_io` daemon coming up first and
network-independent.

This spec covers **only the first step toward that**: get the Zero 2 W onto a
clean, lean DietPi image that boots to SSH, with the low-risk boot wins applied
and a recorded baseline. Everything else is a later phase.

## Decisions (locked during brainstorming)

| Decision | Choice | Rationale |
|---|---|---|
| Board | Pi Zero 2 W (separate from the Pi 4 `rpi`) | quad-core ARMv8, 512 MB; identical 40-pin GPIO; same arch as the Pi 4 |
| Distro | DietPi 64-bit (aarch64) | leanest fast-boot base; scripted headless first boot; it is the long-term unified-image target, so no rework |
| SSH reach | LAN WiFi only — `rpi-zero.local` / IP | simplest; Tailscale deferred |
| Phase-1 scope | minimal image + low-risk boot wins | defer read-only overlay / no-initramfs / kernel trimming to a later phase with the treadmill stack |
| Provisioning | Approach A: file-driven DietPi automation committed to the repo | only option that is unattended + reproducible; its files are the direct inputs to the later custom-image build (zero throwaway) |

## Architecture

A new self-contained directory in the repo; no existing code touched.

```
provisioning/dietpi/
  README.md                    # download URL + checksum, steps, failure modes
  dietpi.txt                   # unattended install config (committed; pubkey + password are __INJECTED__ placeholders)
  dietpi-wifi.txt.example      # WiFi array-format template (committed)
  dietpi-wifi.txt              # finalized WiFi creds (gitignored, produced by prepare-sd.sh)
  secrets.env.example          # WiFi SSID/PSK + global password template (committed)
  secrets.env                  # real secrets (gitignored)
  Automation_Custom_Script.sh  # first-boot hook for the few wins dietpi.txt can't express
  prepare-sd.sh                # templates secrets + copies files to the SD boot partition
  baseline-boot.txt            # committed artifact: systemd-analyze output (post-bring-up)
```

Each unit has one purpose:

- **`dietpi.txt`** — declarative DietPi unattended-install config. Input: none.
  Consumed once by DietPi first-boot.
- **`dietpi-wifi.txt`** — WiFi credentials only. Secret. Generated from the
  `.example` template by `prepare-sd.sh`.
- **`Automation_Custom_Script.sh`** — idempotent first-boot shell hook for the
  handful of boot wins not expressible as `dietpi.txt` keys.
- **`prepare-sd.sh`** — the only thing the operator runs locally. Input: a
  mounted SD boot-partition path. Side effect: writes finalized config files
  onto it. `--check` mode (no SD needed) verifies every `dietpi.txt` key
  against the current DietPi template (catches upstream renames), confirms
  secrets/pubkey presence, runs the SSID/PSK quote-escape round-trip test, and
  asserts the WiFi preconditions (2.4 GHz / WPA2 / broadcast).

## Provisioning Flow

One-time, unattended (~3–4 min including DietPi's first-boot reboot):

1. Flash the stock **DietPi 64-bit ARMv8** image to the SD card (`dd`/Etcher;
   no Raspberry Pi Imager customization — DietPi does it via the boot-partition
   files).
2. Run `provisioning/dietpi/prepare-sd.sh <mounted-boot-path>`. It injects WiFi
   SSID/PSK (from the local gitignored `dietpi-wifi.txt`) and the SSH **public**
   key (from `~/.ssh/*.pub`), then copies `dietpi.txt`, the finalized
   `dietpi-wifi.txt`, and `Automation_Custom_Script.sh` onto the FAT boot
   partition.
3. Insert the SD into the Zero 2 W, power on.
4. DietPi runs unattended: associates WiFi, sets hostname `rpi-zero`, installs
   OpenSSH, runs the custom script, reboots.
5. **Done state:** `ssh dietpi@rpi-zero.local` succeeds with the baked key on a
   warm boot, **and** password SSH is refused (key-only is enforced by
   `SOFTWARE_DISABLE_SSH_PASSWORD_LOGINS=1`, not merely unused).

Subsequent boots are the fast "boots to SSH" path that the later aggressive
phase will optimize and measure.

## Configuration Detail

DietPi is already lean by default (no desktop, no `apt-daily` timers, no
`man-db`, no plymouth). Most "cheap wins" are therefore config flags, not a
tuning script.

### `dietpi.txt` — load-bearing entries

> Key names verified against the current DietPi (Bookworm) `dietpi.txt`
> template during codex review (2026-05-16). `prepare-sd.sh --check` re-verifies
> every key against the template fetched at provisioning time, so a future
> DietPi rename fails loudly instead of silently no-op'ing on a headless box.

- `AUTO_SETUP_AUTOMATED=1` — fully unattended; the sole no-interaction
  guarantee on current DietPi. (`AUTO_SETUP_ACCEPT_LICENSE` was also set here
  previously, but DietPi removed the license/EULA key entirely — reconciled
  2026-05-17 against the live DietPi template; `AUTO_SETUP_AUTOMATED=1` alone
  covers the unattended/no-interaction requirement.)
- `AUTO_SETUP_NET_HOSTNAME=rpi-zero` — distinct from the Pi 4's `rpi`
- `AUTO_SETUP_NET_WIFI_ENABLED=1`
- `AUTO_SETUP_NET_WIFI_COUNTRY_CODE=US` — required for correct regulatory
  domain. Wrong/missing mainly disables channels not legal in the domain (and
  an AP's advertised country can override it); not always a hard "no WiFi", but
  treat it as mandatory.
- `AUTO_SETUP_HEADLESS=1` — DietPi's own HDMI/framebuffer-disable lever; the
  correct primary headless knob (replaces hand-editing `gpu_mem`, which is now
  at most a belt-and-suspenders follow-up, not the mechanism)
- `AUTO_SETUP_SSH_SERVER_INDEX=-2` — OpenSSH (not Dropbear). This is *the* way
  the SSH server is selected; `AUTO_SETUP_INSTALL_SOFTWARE_ID` is not involved
  in SSH selection. The later deploy phase uses rsync/ssh heavily.
- `AUTO_SETUP_SSH_PUBKEY=__INJECTED__` — `prepare-sd.sh` substitutes the real
  pubkey. DietPi installs it into `authorized_keys` for **both** `root` and the
  non-root `dietpi` user. Phase-1 acceptance uses the `dietpi` user, for
  consistency with the existing deploy model (Pi 4 services run as a non-root
  `@USER@`); `root` login also works as a fallback.
- `SOFTWARE_DISABLE_SSH_PASSWORD_LOGINS=1` — **required for the "key-only"
  claim to be true.** `AUTO_SETUP_SSH_PUBKEY` alone only *adds* a key;
  password auth stays enabled without this. Without it the box is not key-only.
- `AUTO_SETUP_GLOBAL_PASSWORD=__INJECTED__` — `prepare-sd.sh` substitutes a
  generated non-default password (from the gitignored secrets file). DietPi's
  default password is the well-known `dietpi`; under `AUTO_SETUP_AUTOMATED=1`
  this **must** be changed even though password SSH is disabled, because it is
  also the `root`/`dietpi` *local* and `sudo` password.
- `AUTO_SETUP_INSTALL_SOFTWARE_ID=` — empty: base only (SSH comes from the
  server-index key above, not from here)
- `AUTO_SETUP_CUSTOM_SCRIPT_EXEC=0` — explicitly run the local
  `/boot/Automation_Custom_Script.sh` (template default is `0`, but a
  hand-written minimal `dietpi.txt` that omits it could skip the script;
  pinning it makes the first-boot hook deterministic)
- `AUTO_SETUP_BOOT_WAIT_FOR_NETWORK=0` — largest single cheap win: do not block
  boot on network. **(Codex review caught the earlier draft using the
  non-existent `CONFIG_BOOT_WAIT_FOR_NETWORK`, which would have silently left
  the default network wait enabled — the exact opposite of the intended win.)**
- `CONFIG_CHECK_DIETPI_UPDATES=0`, `CONFIG_CHECK_APT_UPDATES=0` — no boot-time
  update checks (also prevents an offline hang)
- `SURVEY_OPTED_IN=0`
- `AUTO_SETUP_SWAPFILE_SIZE=0` — no swap (`0`=disabled, `1`=auto, `2+`=MiB).
  Safe in phase 1 (only SSH runs). **Deferred decision, not silent:** 512 MB is
  tight; when the treadmill stack lands, revisit with zram rather than a
  disabled swap.

### `Automation_Custom_Script.sh` — only what `dietpi.txt` can't express

DietPi runs `/boot/Automation_Custom_Script.sh` **at the end of first-run
setup, after networking and the DietPi install** — it cannot rescue a failed
first-boot WiFi/SSH provisioning, and its edits only take effect from the
*next* boot. So it carries only non-critical polish:

- Append `quiet loglevel=3` to the kernel cmdline if absent
- Mask any boot-time unit that survives DietPi defaults but is unneeded
  (verified empirically on first bring-up; expected to be a short list)

Headless display disable is now `AUTO_SETUP_HEADLESS=1` in `dietpi.txt`, not a
`gpu_mem` edit here.

**Boot-partition path is not assumed.** The script must resolve the firmware
config/cmdline location at runtime (DietPi RPi images historically use `/boot`;
Raspberry Pi OS Bookworm moved to `/boot/firmware`). It detects which exists
rather than hard-coding either; `prepare-sd.sh` makes the same detection when
copying onto the externally-mounted FAT partition.

Idempotent — re-running must not double-append or break the config.

### Secrets

- `dietpi-wifi.txt` is gitignored; `dietpi-wifi.txt.example` is the committed
  template. It uses DietPi's exact array format:
  `aWIFI_SSID[0]='…'`, `aWIFI_KEY[0]='…'`, `aWIFI_KEYMGR[0]='WPA-PSK'`.
- A local gitignored `secrets.env` (template: `secrets.env.example`) holds the
  WiFi SSID/PSK **and** the generated non-default global password.
- `prepare-sd.sh` reads those plus the SSH pubkey from `~/.ssh/*.pub`, and
  writes the finalized `dietpi.txt` (pubkey + `AUTO_SETUP_GLOBAL_PASSWORD`
  substituted) and `dietpi-wifi.txt` onto the SD. The PSK, password, and key
  never enter git.
- **Quoting is a correctness item, not a detail.** SSID/PSK values containing
  `'`, `$`, `` ` ``, `\`, or spaces must be emitted single-quoted with DietPi's
  documented escaping; naive string substitution silently corrupts credentials
  and bricks a headless first boot. `prepare-sd.sh --check` includes a
  round-trip quote/escape test.
- `.gitignore` gains `provisioning/dietpi/dietpi-wifi.txt` and
  `provisioning/dietpi/secrets.env`.

## Error Handling & Robustness

Headless means no screen to debug, so failure modes are designed for:

- **Phase-1 WiFi requirement: a broadcast 2.4 GHz WPA2-PSK SSID.** This is a
  hard prerequisite, not just a warning. The Zero 2 W radio is 2.4 GHz only.
  DietPi's first-boot WiFi template supports `WPA-PSK`/`WPA-EAP`/`WEP`/`NONE` —
  there is **no reliable first-boot WPA3/SAE path**, so a WPA3-only network
  will fail; require WPA2-Personal (or WPA2/WPA3 mixed with WPA2 actually
  enabled). **Hidden SSIDs** have known headless first-boot failures even with
  `scan_ssid=1`; Phase 1 requires a broadcast SSID. "Band-steered" is too vague
  — a shared 2.4/5 SSID can work, but PMF/steering/channel quirks make it a bad
  acceptance dependency, so pin first boot to a dedicated 2.4 GHz WPA2 SSID.
  `prepare-sd.sh` prints these as hard preconditions; README lists them as
  failure cause #1.
- **Country code.** Pinned to `US` in `dietpi.txt` for the correct regulatory
  domain (mainly governs which channels are legal; an AP's advertised country
  can still override).
- **mDNS fallback.** If `rpi-zero.local` does not resolve, README documents
  finding the DHCP lease via the router or an `arp`/`ping` sweep.
- **Recovery path.** If the box never appears within ~5 min: re-mount the SD on
  a computer, check `dietpi-wifi.txt`, optionally enable serial console.
  Documented, not automated (automation is later-phase territory).

## Testing & Validation

- **Acceptance (pass condition):** from the dev machine, on a *warm* boot (not
  the one-time first-boot setup): (a) `ssh dietpi@rpi-zero.local` succeeds with
  the baked key; (b) password SSH is actively refused (verifying key-only is
  enforced); (c) the default `dietpi` password no longer works for local/sudo.
- **Baseline capture:** on the booted Pi, record `systemd-analyze`,
  `systemd-analyze blame`, `systemd-analyze critical-chain`; commit to
  `provisioning/dietpi/baseline-boot.txt`. Phase 1 does **not** tune to a
  target — it establishes the clean baseline the later aggressive phase is
  measured against.
- **Automatable now:** `prepare-sd.sh --check` lints `dietpi.txt` keys and
  verifies secrets + pubkey presence before flashing. SD provisioning itself
  cannot be unit-tested; the documented manual acceptance run plus the
  committed baseline artifact are the proof.

## Out of Scope (Deferred to Later Phases)

Tailscale join · treadmill services and service-ordering (`treadmill_io` first,
network-independent) · read-only / overlay root · `/data` writable partition ·
no-initramfs · kernel-module trimming · the unified custom `.img` build ·
`server.py` JSON-path changes · anything touching the existing Pi 4.

None of phase 1's artifacts are throwaway — `dietpi.txt`,
`Automation_Custom_Script.sh`, and `prepare-sd.sh` are the direct inputs to the
later unified custom-image build.
