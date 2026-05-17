# Design: `build-image.sh` — Userspace DietPi Image Builder

**Date:** 2026-05-16
**Status:** Approved (design); implementation plan pending
**Scope:** A convenience script that turns the stock DietPi image into a
fully-configured, ready-to-flash `.img` using the existing `prepare-sd.sh`,
with **no root for the build** and an optional, heavily-guarded `--flash`.

## Context

The Phase-1 toolkit (`provisioning/dietpi/`) configures a Pi Zero 2 W by
writing three files (`dietpi.txt`, `dietpi-wifi.txt`,
`Automation_Custom_Script.sh`) into the FAT boot partition. `prepare-sd.sh`
already does the credential injection + mandatory DietPi key-rename
verification and is heavily reviewed/locked. What's missing is the
orchestration around it: fetch the image, verify it, get the three files into
the image's boot partition, optionally flash an SD. Doing that by
loop-mounting requires root every run; that is the wrong tool — we are editing
a FAT filesystem inside a regular file, not a device.

## Decisions (locked during brainstorming)

| Decision | Choice | Rationale |
|---|---|---|
| Mechanism | `mtools` (`mcopy`/`mdir`) against the image at a partition offset | Pure userspace FAT read/write; no loop device, no mount, no teardown of a privileged resource |
| Reuse vs reimplement | Call the unchanged `prepare-sd.sh` against a staging dir | `prepare-sd.sh` stays the single, audited source of truth for injection + key-verify |
| Flash | Build-only by default; opt-in `--flash /dev/sdX` with strong guards | Build needs no root; writing a raw disk legitimately requires root and is the only `sudo` in the tool |
| Board image | `DietPi_RPi234-ARMv8-Bookworm.img.xz` only | The Pi Zero 2 W is a Pi-3-class (RP3A0) ARMv8 board — RPi234, not RPi5 |
| Layout | New `provisioning/dietpi/build-image.sh`, sibling to `prepare-sd.sh` | One orchestration script; clear-layers (no build logic leaks into `prepare-sd.sh`) |

## Architecture

`provisioning/dietpi/build-image.sh` — orchestration only. It does not
reimplement any credential/config logic; it shells out to the existing
`prepare-sd.sh`. Pure, testable guard predicates are factored as shell
functions and unit-tested in the existing dependency-free harness style.

Interface:

```
build-image.sh [--image-dir DIR] [--secrets FILE] [--pubkey FILE]
               [--flash /dev/sdX] [--i-understand] [--force]
```

- `--image-dir` default `~/dietpi-build` (a real disk, never `/tmp` which may
  be tmpfs). The configured image lands at
  `<image-dir>/DietPi_RPi234-ARMv8-Bookworm.img`.
- `--secrets` / `--pubkey` are passed through to `prepare-sd.sh` (same
  defaults: `provisioning/dietpi/secrets.env`, `~/.ssh/id_*.pub`).
- `--flash /dev/sdX` opt-in raw-disk write (guards below).
- `--i-understand` required override to flash a **non-removable** device.
- `--force` permits re-decompressing over an existing `.img`.

## Build Flow (100% userspace, no root)

1. **Dep-check:** require `curl`, `xz`, `sha256sum`, `mtools` (`mcopy`,
   `mdir`), and a partition reader (`sfdisk` or `partx`, util-linux). If
   `mtools` is missing, print the install hint (`apt-get install mtools`) and
   exit non-zero. (`lsblk` additionally required only when `--flash`.)
2. **Fetch (cached):** if `<image-dir>/DietPi_RPi234-ARMv8-Bookworm.img.xz`
   and its `.sha256` are already present and verify, skip download; else
   `curl -fL` both from `https://dietpi.com/downloads/images/`.
3. **Verify:** `sha256sum -c` the sidecar. Hard fail on mismatch.
4. **Decompress:** `xz -dk` → `<image-dir>/DietPi_RPi234-ARMv8-Bookworm.img`.
   Refuse to clobber an existing `.img` unless `--force`.
5. **Partition offset:** read partition 1's start byte offset from the `.img`
   (regular file → no root) via `sfdisk -J` (sectors × 512) with `partx`
   fallback.
6. **Stage:** `mktemp -d`; `mcopy -i "$img@@$off" ::config.txt "$stage/"` to
   extract the stock `config.txt`.
7. **Inject:** run `provisioning/dietpi/prepare-sd.sh "$stage"` (unchanged).
   Its boot-partition guard (it refuses if `$stage` has no `config.txt`)
   passes because the stock `config.txt` was just extracted there; it then
   performs its mandatory key-rename verification and writes the three files
   into `$stage`.
8. **Write back:** `mcopy -o -i "$img@@$off" "$stage"/dietpi.txt
   "$stage"/dietpi-wifi.txt "$stage"/Automation_Custom_Script.sh ::`. DietPi
   already ships a `dietpi.txt`; `-o` overwrites it (intended).
9. **Done:** print the configured `.img` path. A single `trap` removes
   `$stage` on any exit (the only resource to clean — no privileged state).

## Guarded `--flash /dev/sdX` (the only `sudo`)

Runs only if `--flash` is given. Every check must pass or it refuses:

- `DEV` is an existing **block device**; refuse loop devices, `/dev/ram*`,
  and the disk backing `/` (resolve the root device and block it and its
  parent disk).
- Refuse a **non-removable** device unless `--i-understand` is also passed
  (separate from `--force`).
- Refuse if any partition of `DEV` is currently mounted.
- Show `lsblk -dno NAME,MODEL,SIZE,TRAN,RM "$DEV"` and require the operator to
  **type the exact device path** to confirm. No `-y`/non-interactive bypass.
- `sudo dd if="$img" of="$DEV" bs=4M conv=fsync status=progress` then `sync`.
- The script pre-flights `sudo -v` only on this path and states why.

## Error Handling

- `set -euo pipefail`; every external step checked; clear messages.
- Single `EXIT` trap: `rm -rf "$stage"` (idempotent; nothing privileged).
- No secrets are handled here — injection is delegated to the audited
  `prepare-sd.sh`; this script never reads the PSK/password.
- Image integrity is gated on `sha256sum -c` before any use.

## Testing

Per project rule (tests are real): the network/`mtools`/`dd` orchestration
needs real artifacts/devices → covered by a documented manual run. The
**safety-critical pure logic is unit-tested** in the existing dependency-free
harness style (`provisioning/dietpi/tests/`): arg parsing; dep-check;
partition-offset computation against a crafted tiny MBR fixture; and the flash
guards — `is_block_device`, `is_removable` (mocked `/sys`), `is_system_disk`
(refuses the `/`-backing disk), `confirm_matches` (typed-input match). The
guards that prevent destroying a disk are exactly what must not regress, so
they get real tests with fake `/sys` paths, temp device-node stand-ins, and
simulated stdin.

## Out of Scope

`.img` recompression; multi-board support (RPi234 only); auto-eject; progress
UI beyond `dd status=progress`; any change to `prepare-sd.sh` or other
existing toolkit files; Tailscale/treadmill/overlay (later phases).
