# Esp32Tap DevKit Bare-Board Bring-Up Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce, back up, flash, and verify a provenance-bound ESP32-S3-DevKitC-1 N8R8 diagnostic image that cannot drive treadmill outputs, then verify the approved sidecar harness.

**Architecture:** Rebase this feature onto the exact hardened firmware/provenance state at `554e4c5`, then extend its immutable artifact pipeline with a third `devkit-bringup` kind. Put protocol logic in a dependency-free host-tested crate and ESP-IDF calls in a separate firmware crate with a narrow unsafe boundary; use a host-tested Pi bench tool for backup, flash, UART capture, and switch sampling.

**Tech Stack:** Rust 1.90 + ESP-IDF 5.5.4 (`esp-idf-sys`), Python 3/pytest, Docker, `esptool` 5.3.1, pyserial, SSH, SHA-256 manifests.

**Specification:** `docs/superpowers/specs/2026-07-31-esp32tap-devkit-bringup-design.md`

---

## File structure

New portable and firmware units:

- `hardware/Esp32Tap/firmware/esp32_rs/bringup_core/` — dependency-free bounded command parser and canonical report formatter with host tests.
- `hardware/Esp32Tap/firmware/esp32_rs/devkit_bringup/` — separate ESP-IDF application; no production control, UART, network, or BLE modules.
- `hardware/Esp32Tap/firmware/esp32_rs/sdkconfig.defaults.devkit` — N8R8/UART-console diagnostic overlay.

New bench units:

- `hardware/Esp32Tap/firmware/esp32_rs/tools/devkit_bench.py` — Pi-side bundle verification, backup, flash, monitor, and SAMPLE client.
- `hardware/Esp32Tap/firmware/esp32_rs/tools/devkit_remote.sh` — closet-side locked verify/copy/remote-exec wrapper.
- `hardware/Esp32Tap/firmware/esp32_rs/tools/test_devkit_*.py` — source-contract, bench-tool, and remote-wrapper tests.
- `hardware/Esp32Tap/firmware/esp32_rs/docs/devkit-sidecar-netlist.md` — durable netlist, BOM, and eight-state truth table.

Existing units extended, not forked:

- `tools/artifact_inputs.py`, `tools/artifact_provenance.py`, `tools/build.sh`, and `tools/build_image.sh` gain a third artifact kind.
- Their existing tests gain third-kind cases.
- `tools/check_unsafe_budget.py` gains an independent DevKit unsafe allowlist/budget.
- `.gitignore` and `README.md` gain generated-artifact/backup rules and operator instructions.

## Fixed bench identity

```text
Pi SSH:          ssilver@192.168.1.155 with HostKeyAlias=rpi
Pi Tailscale:   100.117.206.120 (fallback)
esptool:        /home/ssilver/.local/bin/esptool (v5.3.1 verified)
serial device:  /dev/serial/by-id/usb-Silicon_Labs_CP2102N_USB_to_UART_Bridge_Controller_4cd513f253bff0119bc5c57948e9de0f-if00-port0
chip MAC:       94:a9:90:db:b0:e4
flash size:     0x800000
```

### Task 1: Integrate the current hardened firmware/provenance base

**Files:**
- Verify: `hardware/Esp32Tap/firmware/esp32_rs/partitions_esp32tap.csv`
- Verify: `.gitignore`
- Verify: `hardware/Esp32Tap/firmware/esp32_rs/tools/artifact_provenance.py`

- [ ] **Step 1: Rebase the approved design commits onto the exact prerequisite commit**

```bash
git rebase --onto 554e4c5 e50b31a
```

Expected: this branch contains the approved design above `554e4c5`; no spec conflict.

- [ ] **Step 2: Prove the partition source is tracked and not ignored**

```bash
git ls-files --error-unmatch hardware/Esp32Tap/firmware/esp32_rs/partitions_esp32tap.csv
if git check-ignore hardware/Esp32Tap/firmware/esp32_rs/partitions_esp32tap.csv; then exit 1; fi
```

Do not close `precor-9_3x-344` until Task 7 also proves a clean build.

- [ ] **Step 3: Run prerequisite gates**

```bash
cd hardware/Esp32Tap/firmware/esp32_rs
python3 -m pytest -q tools/test_artifact_inputs.py tools/test_artifact_provenance.py tools/test_build_image.py tools/test_provenance_entrypoints.py
python3 tools/check_pins.py
python3 tools/check_wdt_chain.py
```

Expected: all pass.

- [ ] **Step 4: Push the rebased branch safely**

```bash
git push --force-with-lease origin feat/esp32tap-devkit-bringup
```

### Task 2: Add the portable bounded diagnostic protocol

**Files:**
- Create: `hardware/Esp32Tap/firmware/esp32_rs/bringup_core/Cargo.toml`
- Create: `hardware/Esp32Tap/firmware/esp32_rs/bringup_core/src/lib.rs`
- Create: `hardware/Esp32Tap/firmware/esp32_rs/bringup_core/tests/protocol.rs`

- [ ] **Step 1: Write failing exact parser tests**

```rust
#[test]
fn accepts_one_canonical_sample_command() {
    assert_eq!(parse_command(b"SAMPLE 7\n"), Ok(Command::Sample(7)));
}

#[test]
fn rejects_ambiguous_or_oversized_commands() {
    assert_eq!(parse_command(b"SAMPLE +7\n"), Err(ParseError::BadSequence));
    assert_eq!(parse_command(b"SAMPLE 7 trailing\n"), Err(ParseError::BadShape));
    assert_eq!(parse_command(&[b'A'; MAX_COMMAND_BYTES + 1]), Err(ParseError::TooLong));
}
```

- [ ] **Step 2: Run RED**

```bash
cargo test --manifest-path hardware/Esp32Tap/firmware/esp32_rs/bringup_core/Cargo.toml
```

Expected: crate/API absent.

- [ ] **Step 3: Implement the no-allocation API**

```rust
#![forbid(unsafe_code)]
#![no_std]

pub const MAX_COMMAND_BYTES: usize = 32;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Command { Sample(u32) }

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PinSample {
    pub sequence: u32,
    pub gpio4: bool,
    pub gpio5: bool,
    pub gpio6: bool,
    pub gpio7: bool,
    pub gpio15_is_input: bool,
    pub gpio17_is_input: bool,
    pub gpio21_is_input: bool,
}
```

Reject signs, alternate whitespace, overflow, missing newline, NUL, and trailing data.

- [ ] **Step 4: Add failing canonical-format tests**

Require a fixed-capacity writer to emit exactly:

```text
INPUT SAMPLE seq=7 gpio4=0 gpio5=1 gpio6=1 gpio7=0 dir15=input dir17=input dir21=input
```

Also require bounded `BRINGUP ERROR code=BAD_COMMAND` output.

- [ ] **Step 5: Implement formatters and run GREEN**

```bash
cargo test --manifest-path hardware/Esp32Tap/firmware/esp32_rs/bringup_core/Cargo.toml
```

- [ ] **Step 6: Commit**

```bash
git add hardware/Esp32Tap/firmware/esp32_rs/bringup_core
git commit -m "feat(Esp32Tap): add bounded DevKit diagnostic protocol"
```

### Task 3: Add the isolated DevKit firmware and protected-pin gate

**Files:**
- Create: `hardware/Esp32Tap/firmware/esp32_rs/devkit_bringup/Cargo.toml`
- Create: `hardware/Esp32Tap/firmware/esp32_rs/devkit_bringup/build.rs`
- Create: `hardware/Esp32Tap/firmware/esp32_rs/devkit_bringup/.cargo/config.toml`
- Create: `hardware/Esp32Tap/firmware/esp32_rs/devkit_bringup/rust-toolchain.toml`
- Create: `hardware/Esp32Tap/firmware/esp32_rs/devkit_bringup/src/main.rs`
- Create: `hardware/Esp32Tap/firmware/esp32_rs/devkit_bringup/src/hardware.rs`
- Create: `hardware/Esp32Tap/firmware/esp32_rs/sdkconfig.defaults.devkit`
- Create: `hardware/Esp32Tap/firmware/esp32_rs/tools/test_devkit_source_contract.py`
- Modify: `hardware/Esp32Tap/firmware/esp32_rs/tools/check_unsafe_budget.py`

- [ ] **Step 1: Write failing source-contract tests**

Require these sets and prohibitions:

```python
PROTECTED_INPUT_ONLY = {4, 5, 6, 7, 15, 17, 21}
UNTOUCHED = {16, 18, 38}
FORBIDDEN_TEXT = {
    "gpio_set_direction", "gpio_config", "gpio_set_level",
    "gpio_pullup_en", "gpio_pullup_dis", "gpio_pulldown_en",
    "gpio_pulldown_dis", "gpio_set_pull_mode", "gpio_hold_en",
    "uart_set_pin", "uart_driver_install", "esp_wifi", "nimble",
    "safety_core", "program_core", "ble_core", "coach_core",
}
```

Allow `gpio_get_direction` and `gpio_get_level` only in `hardware.rs`. Require direct dependencies exactly `bringup_core`, `esp-idf-sys`, plus build dependency `embuild`. Require unsafe tokens only in `hardware.rs`, each with a local `// SAFETY:` justification. Inspect both source and generated sdkconfig; reject every pull/output/hold configuration API and every protected GPIO from an output mask.

The RED tests must also require every startup field from the spec and these exact terminal failure codes: `BAD_RECIPE`, `CHIP_INFO`, `MAC_READ`, `FLASH_SIZE`, `PSRAM_SIZE`, `GPIO_READ`, `PROTECTED_DIRECTION`, and `UART_WRITE`. Require direction and level records for GPIO4/5/6/7/15/16/17/18/21/38, a single terminal PASS/FAIL, a bounded line/command size, and source/config proof that neither panic nor safe halt calls `esp_restart` or enables a reboot watchdog.

- [ ] **Step 2: Run RED**

```bash
python3 -m pytest -q hardware/Esp32Tap/firmware/esp32_rs/tools/test_devkit_source_contract.py
```

- [ ] **Step 3: Create the crate and recipe-ID build gate**

`build.rs` rejects absent/non-lowercase-64-hex `ESP32TAP_RECIPE_ID`, then emits:

```rust
println!("cargo:rustc-env=ESP32TAP_RECIPE_ID={recipe}");
println!("cargo:rerun-if-env-changed=ESP32TAP_RECIPE_ID");
```

The crate root declares no production module. `main` emits the exact bounded record sequence below, checks GPIO15/17/21 direction before PASS, and then accepts only bounded SAMPLE commands:

```text
ESP32TAP DEVKIT BRINGUP — NO CONTROL OUTPUTS
BUILD recipe=<64hex> git=<40hex>
CHIP model=ESP32-S3 revision=<n> mac=94:a9:90:db:b0:e4 crystal_mhz=40 reset=<reason>
MEMORY flash_bytes=8388608 psram_total=<n> internal_free=<n> psram_free=<n>
PINS gpio4=<level>/<direction> gpio5=<level>/<direction> gpio6=<level>/<direction> gpio7=<level>/<direction> gpio15=<level>/input gpio16=<level>/<direction> gpio17=<level>/input gpio18=<level>/<direction> gpio21=<level>/input gpio38=<level>/<direction>
BRINGUP STAGE0 PASS
```

Before the first application report, wait a fixed `STARTUP_SETTLE_MS = 5_000` so the independently powered CP2102N can disappear/re-enumerate and the Pi can reopen it during a true cold boot. Source-contract tests must prove the delay occurs before the first report write and is neither omitted nor repeated.

On failure, emit one `BRINGUP FAIL code=<EXACT_CODE>` line and enter a delay-based halt. No restart call, watchdog subscription, or retry loop may emit a second report.

- [ ] **Step 4: Implement the read-only ESP-IDF boundary**

Wrap only chip/MAC/reset/flash-size/heap queries, `gpio_get_direction`, `gpio_get_level`, and bounded UART0 console I/O. No function may configure a pad or install/remap a UART driver.

- [ ] **Step 5: Add the standalone pinned N8R8 sdkconfig**

Do not layer this on the production sdkconfig, which contains radio/network and treadmill watchdog choices irrelevant to the isolated binary. Make `sdkconfig.defaults.devkit` complete for this crate: 8 MB flash, the tracked custom partition table, octal PSRAM boot/init/caps allocation, default UART console, panic-print-and-halt, and no task-WDT auto-init. Resolve exact IDF 5.5.4 key spelling from generated configuration. Reject QEMU-only, OpenETH, Wi-Fi, Bluetooth, USB-console, debug-JTAG, panic-reboot, and task-WDT-init options.

- [ ] **Step 6: Extend unsafe-budget enforcement separately**

Add an independent DevKit allowlist/line budget. Do not add this crate to the treadmill production unsafe budget.

- [ ] **Step 7: Run GREEN and commit**

```bash
python3 -m pytest -q hardware/Esp32Tap/firmware/esp32_rs/tools/test_devkit_source_contract.py
python3 hardware/Esp32Tap/firmware/esp32_rs/tools/check_unsafe_budget.py
cargo test --manifest-path hardware/Esp32Tap/firmware/esp32_rs/bringup_core/Cargo.toml
git add hardware/Esp32Tap/firmware/esp32_rs/devkit_bringup hardware/Esp32Tap/firmware/esp32_rs/sdkconfig.defaults.devkit hardware/Esp32Tap/firmware/esp32_rs/tools/check_unsafe_budget.py hardware/Esp32Tap/firmware/esp32_rs/tools/test_devkit_source_contract.py
git commit -m "feat(Esp32Tap): add output-isolated DevKit firmware"
```

### Task 4: Extend immutable artifact publication with `devkit-bringup`

**Files:**
- Modify: `hardware/Esp32Tap/firmware/esp32_rs/tools/artifact_provenance.py`
- Modify: `hardware/Esp32Tap/firmware/esp32_rs/tools/artifact_inputs.py`
- Modify: `hardware/Esp32Tap/firmware/esp32_rs/tools/build.sh`
- Modify: `hardware/Esp32Tap/firmware/esp32_rs/tools/build_image.sh`
- Modify: `hardware/Esp32Tap/firmware/esp32_rs/tools/test_artifact_provenance.py`
- Modify: `hardware/Esp32Tap/firmware/esp32_rs/tools/test_artifact_inputs.py`
- Modify: `hardware/Esp32Tap/firmware/esp32_rs/tools/test_build_image.py`
- Modify: `hardware/Esp32Tap/firmware/esp32_rs/tools/test_provenance_entrypoints.py`
- Modify: `hardware/Esp32Tap/firmware/esp32_rs/tools/test_snapshot_build.py`

- [ ] **Step 1: Add failing third-kind tests**

Require `_KIND_LAYOUT["devkit-bringup"] == ("build_devkit_bringup", "devkit")`, independent publication, exact-kind validation, renamed-production/QEMU rejection, stale-digest rejection, sealed generations, and direct-entry refusal without a valid snapshot.

- [ ] **Step 2: Run RED**

```bash
cd hardware/Esp32Tap/firmware/esp32_rs
python3 -m pytest -q tools/test_artifact_provenance.py tools/test_artifact_inputs.py tools/test_build_image.py tools/test_provenance_entrypoints.py tools/test_snapshot_build.py
```

- [ ] **Step 3: Add a complete, non-circular recipe identity**

Reuse the exact standard five bundle members, including `esp32tap.bin`, but do not mislabel the existing source-only `input_digest` as the recipe ID. Extend the manifest validator canonically and conditionally for `kind=devkit-bringup` with:

```json
{
  "git_commit": "<40hex>",
  "dirty_state": "clean",
  "profile": "release",
  "required_serial_device": "/dev/serial/by-id/usb-Silicon_Labs_CP2102N_USB_to_UART_Bridge_Controller_4cd513f253bff0119bc5c57948e9de0f-if00-port0",
  "flash_geometry": {"chip": "esp32s3", "size": 8388608, "offsets": [0, 32768, 65536]},
  "recipe_id": "<64hex>"
}
```

Before compilation, compute `recipe_id` as SHA-256 of canonical JSON containing: clean Git commit, artifact kind, profile, existing source `input_digest`, and the complete pinned `Toolchain` record (Docker image ID/recipe, IDF commit, rustc, target, linker, esptool, component lock, features). After build, parse flash geometry from generated artifacts and add it to the final manifest; geometry is not part of the pre-build recipe hash. Recompute the same recipe from the final manifest and reject any mismatch.

Extend input discovery to both new crates, DevKit config, bench tools, and gates. Add tests showing that changing commit, kind, profile, any toolchain fact, or source digest changes `recipe_id`, while changing final image bytes changes member/manifest hashes but not the pre-build recipe.

- [ ] **Step 4: Extend the pinned builder**

Add `ONLY=devkit`. Compute the complete recipe ID before compilation, pass it as `ESP32TAP_RECIPE_ID` plus the clean `ESP32TAP_GIT_COMMIT`, build `devkit_bringup/Cargo.toml` using only `sdkconfig.defaults.devkit`, output the standard bundle, parse/record flash geometry, and publish through the existing transaction.

Update `test_snapshot_build.py` for the third accepted kind, its exact error messages, snapshot input set, and publication behavior while retaining every production/QEMU assertion.

Fail on dirty/incomplete snapshot, embedded-recipe mismatch, missing 8 MB flash/octal PSRAM, QEMU/net/BLE identity, or partition overflow.

- [ ] **Step 5: Run GREEN and commit**

```bash
python3 -m pytest -q tools/test_artifact_provenance.py tools/test_artifact_inputs.py tools/test_build_image.py tools/test_provenance_entrypoints.py tools/test_snapshot_build.py
git add tools/artifact_provenance.py tools/artifact_inputs.py tools/build.sh tools/build_image.sh tools/test_artifact_provenance.py tools/test_artifact_inputs.py tools/test_build_image.py tools/test_provenance_entrypoints.py tools/test_snapshot_build.py
git commit -m "build(Esp32Tap): publish immutable DevKit bundles"
```

### Task 5: Add the Pi-side backup/flash/monitor tool

**Files:**
- Create: `hardware/Esp32Tap/firmware/esp32_rs/tools/devkit_bench.py`
- Create: `hardware/Esp32Tap/firmware/esp32_rs/tools/test_devkit_bench.py`
- Modify: `.gitignore`

- [ ] **Step 1: Write failing verification/refusal tests**

Use temporary bundles and a fake command runner. Require refusal for non-canonical/oversized manifests; wrong kind; missing, extra, symlinked, or hard-linked members; size/hash mismatch; any serial path unequal to the manifest's exact CP2102N `required_serial_device`; wrong chip/MAC/flash size; and flash before a matching 8 MB backup receipt. Test that `backup` rejects a non-physical, non-owned, or non-`0700` backup directory and that it creates only an owned, single-link regular raw backup file with exact mode `0600`, even under permissive umask. Test that receipt creation/acceptance and flash authorization reject a backup whose parent directory or raw file later fails those checks. A different valid `/dev/serial/by-id/...` path must be a RED test.

- [ ] **Step 2: Run RED**

```bash
python3 -m pytest -q hardware/Esp32Tap/firmware/esp32_rs/tools/test_devkit_bench.py
```

- [ ] **Step 3: Implement bounded subcommands**

```text
verify-bundle --bundle PATH
backup --bundle PATH --serial PATH --expected-mac 94:a9:90:db:b0:e4 --backup-dir PATH
flash-monitor --bundle PATH --serial PATH --receipt PATH --timeout 30
monitor --serial PATH --recipe-id HEX --timeout 30
cold-monitor --serial PATH --recipe-id HEX --timeout 180
sample --serial PATH --sequence N --expect 0,1,1,0
```

All subprocess calls are argv arrays using fixed `/home/ssilver/.local/bin/esptool`; no shell. The serial argument must byte-for-byte equal the manifest-bound path and resolve to the same character device immediately before every chip, backup, and flash action. Reads are bounded. `backup-dir` must be an owned physical directory (not a symlink) with exact mode `0700`. `backup` uses exclusive create and a requested `0600` mode; before hashing or accepting/writing a receipt, it re-stats the raw backup and requires an owned, single-link regular file with exact mode `0600`. Receipt JSON is canonical mode `0600` and records MAC, byte count, backup SHA-256, path, and timestamp. Backups and receipts are never overwritten.

- [ ] **Step 4: Implement backup/flash sequencing**

Backup command:

```text
esptool --chip esp32s3 --port SERIAL read-flash 0x0 0x800000 BACKUP
```

`flash-monitor` parses verified `flash_args` into bounded argv instead of trusting a shell response file. Before accepting the receipt, re-hashing its backup, or authorizing a write, re-check the owned physical `0700` backup directory, the owned single-link regular `0600` raw backup, and the `0600` receipt. Immediately before write, re-check chip/MAC and re-hash the exact backup named by the receipt.

- [ ] **Step 5: Implement bounded UART parsing**

Use pyserial at 115200 8N1. Eliminate the reset/capture race with one defined choreography: esptool writes with `--after no-reset`; the tool opens the exact port once with flow control disabled; without clearing input after reset, it invokes esptool 5.3.1's `HardReset` on that already-open pyserial object; it then captures boot bytes from the same open descriptor. `ClassicReset` is forbidden because it holds GPIO0 low and returns to the ROM bootloader. Tests use a fake serial object to assert ordering (`open -> neutral DTR/RTS -> HardReset -> read`) and prove no input-buffer reset occurs after reset.

`cold-monitor` prints `REMOVE UART USB POWER`, waits for the exact serial symlink to disappear, prints `RESTORE UART USB POWER`, waits for the same symlink and USB serial identity to reappear, opens it within the firmware's five-second settle window, and captures the one startup report. Tests drive disappearance/reappearance with a fake monotonic clock/filesystem, reject a device that never disappears, reject a changed resolved device/USB serial, and enforce bounded timeouts.

`monitor`/the capture half of `flash-monitor` requires one identity banner, recipe-ID match, every required startup field, protected direction readback, and exactly one terminal result. `sample` sends canonical `SAMPLE N\n` and accepts only the matching sequence/tuple with all protected directions input.

- [ ] **Step 6: Run GREEN and commit**

```bash
python3 -m pytest -q hardware/Esp32Tap/firmware/esp32_rs/tools/test_devkit_bench.py
git add .gitignore hardware/Esp32Tap/firmware/esp32_rs/tools/devkit_bench.py hardware/Esp32Tap/firmware/esp32_rs/tools/test_devkit_bench.py
git commit -m "feat(Esp32Tap): add guarded DevKit bench tool"
```

### Task 6: Add the closet-to-Pi locked transport

**Files:**
- Create: `hardware/Esp32Tap/firmware/esp32_rs/tools/devkit_remote.sh`
- Create: `hardware/Esp32Tap/firmware/esp32_rs/tools/test_devkit_remote.py`

- [ ] **Step 1: Write failing lock/argv tests**

Require execution under `artifact_provenance.py exec --kind devkit-bringup`, `ssh/scp -o BatchMode=yes -o HostKeyAlias=rpi`, fixed `ssilver@192.168.1.155`, a fresh remote staging directory, and the Pi venv Python. Reject caller-supplied hosts, usernames, bundle/serial paths, or shell fragments.

- [ ] **Step 2: Run RED**

```bash
python3 -m pytest -q hardware/Esp32Tap/firmware/esp32_rs/tools/test_devkit_remote.py
```

- [ ] **Step 3: Implement only `stage`, `backup`, `flash-monitor`, `monitor`, `cold-monitor`, and `sample`**

Create a random remote suffix under `/home/ssilver/esp32tap-bench/staging/`, copy the sealed bundle and bench script, run remote verification, and print exact staging/receipt paths. Never delete backups or receipts.

- [ ] **Step 4: Run GREEN and commit**

```bash
python3 -m pytest -q hardware/Esp32Tap/firmware/esp32_rs/tools/test_devkit_remote.py
bash -n hardware/Esp32Tap/firmware/esp32_rs/tools/devkit_remote.sh
git add hardware/Esp32Tap/firmware/esp32_rs/tools/devkit_remote.sh hardware/Esp32Tap/firmware/esp32_rs/tools/test_devkit_remote.py
git commit -m "feat(Esp32Tap): add locked remote DevKit transport"
```

### Task 7: Build from a clean checkout and close the source-input blocker

**Files:**
- Modify: `hardware/Esp32Tap/firmware/esp32_rs/README.md`
- Create: `hardware/Esp32Tap/firmware/esp32_rs/docs/devkit-sidecar-netlist.md`

- [ ] **Step 1: Add operator documentation**

Document exact build, stage, backup, flash, monitor, restore, and SAMPLE commands. Require an owned physical backup directory with exact mode `0700`, a single-link regular raw backup with exact mode `0600`, and a mode-`0600` receipt before flash; state that the tool validates all three despite ambient umask. Copy the approved netlist, corrected BOM (`3×10k`, `3×47k`, `3×1k`), and eight-state table. State restore is recovery-only.

- [ ] **Step 2: Commit documentation before computing/building the artifact**

```bash
git add hardware/Esp32Tap/firmware/esp32_rs/README.md hardware/Esp32Tap/firmware/esp32_rs/docs/devkit-sidecar-netlist.md
git commit -m "docs(Esp32Tap): document DevKit bench bring-up"
```

Expected: every declared source/config/doc input is committed before the recipe and clean worktree are created.

- [ ] **Step 3: Request and address code review before any provenance-bound build**

Use `superpowers:requesting-code-review`; handle concrete findings with `superpowers:receiving-code-review`, rerunning affected focused gates and committing fixes. No code review that can modify source remains after this step.

- [ ] **Step 4: Synchronize HEAD before computing the recipe**

```bash
git pull --rebase
git status --short --branch
```

Expected: clean branch. Record this exact HEAD; every remaining build in this task must use it. If HEAD changes later for any reason, restart this task from Step 4 and rebuild both clean and live bundles.

- [ ] **Step 5: Create, preflight, build, and remove a disposable clean worktree**

Run this entire block in one shell. `set -eu` stops on a failed checkout, `cd`, preflight, gate, or build before any later clean-worktree command can run; it also rejects an unset path variable. `umask 0022` applies before `mktemp` and remains in force through checkout and every clean-worktree gate/build command. The EXIT trap returns to the live repository before removing only the explicit `mktemp` path, so a failed preflight or build cannot strand the disposable worktree.

```bash
(
  set -eu
  umask 0022
  repo_root=$(git rev-parse --show-toplevel)
  tmp_dir=$(mktemp -d /tmp/esp32tap-devkit-clean.XXXXXX)
  cleanup() {
    status=$?
    trap - EXIT
    cd "$repo_root"
    if worktree_list=$(git worktree list --porcelain); then
      if test -L "$tmp_dir"; then
        printf '%s\n' "refusing symlinked cleanup path: $tmp_dir" >&2
        exit "$status"
      elif printf '%s\n' "$worktree_list" | grep -Fqx "worktree $tmp_dir"; then
        git worktree remove --force "$tmp_dir"
      elif test -e "$tmp_dir"; then
        rmdir "$tmp_dir"
      fi
    else
      printf '%s\n' 'git worktree list failed; cleaning only the explicit empty mktemp directory' >&2
      if test -L "$tmp_dir"; then
        printf '%s\n' "refusing symlinked cleanup path: $tmp_dir" >&2
        exit "$status"
      elif test -d "$tmp_dir"; then
        rmdir "$tmp_dir"
      else
        printf '%s\n' "refusing non-directory cleanup path: $tmp_dir" >&2
        exit "$status"
      fi
    fi
    exit "$status"
  }
  trap cleanup EXIT

  git worktree add --detach "$tmp_dir" HEAD
  cd "$tmp_dir/hardware/Esp32Tap/firmware/esp32_rs"

  # Physical modes must match Git's 100755 entries in a fresh checkout.
  stat -c '%a %n' tools/build_image.sh tools/qemu_smoke.sh tools/run_harness.sh tools/qemu_harness/run.sh
  python3 -m pytest -q \
    tools/test_build_image.py::test_script_is_executable_and_tracked_as_100755 \
    tools/test_provenance_entrypoints.py::test_shell_entrypoint_verifies_before_delegating

  python3 -m pytest -q tools/test_devkit_*.py
  python3 tools/check_unsafe_budget.py
  python3 tools/check_pins.py
  python3 tools/check_wdt_chain.py
  ONLY=devkit tools/build.sh
  python3 tools/artifact_provenance.py verify --repo-root "$tmp_dir" --kind devkit-bringup

  # Inspect kind, input digest, toolchain, five hashes, 8 MB header, partition
  # fit, DevKit banner, and absence of QEMU/production startup banners here.
  cd "$repo_root"
  git worktree remove "$tmp_dir"
  test ! -e "$tmp_dir" && test ! -L "$tmp_dir"
  worktree_list=$(git worktree list --porcelain)
  ! printf '%s\n' "$worktree_list" | grep -Fqx "worktree $tmp_dir"
  trap - EXIT
)
```

Expected: all four printed modes are `755`; the targeted exact-mode preflight covers `build_image.sh`, `qemu_smoke.sh`, `run_harness.sh`, and `qemu_harness/run.sh`; no retained ignored input is required; the sealed bundle is current; and the exact disposable path is neither present nor a symlink and is absent from a successfully captured `git worktree list`.

- [ ] **Step 6: Build and publish the same current commit in the live worktree**

The clean worktree's bundle is worktree-local and was intentionally removed. From the live feature worktree, rerun:

```bash
cd hardware/Esp32Tap/firmware/esp32_rs
ONLY=devkit tools/build.sh
python3 tools/artifact_provenance.py verify --repo-root "$(git rev-parse --show-toplevel)" --kind devkit-bringup
```

Expected: a sealed, current `build_devkit_bringup` bundle exists in the live worktree for Task 8.

- [ ] **Step 7: Close `precor-9_3x-344` and push before hardware mutation**

Record clean commit, clean-worktree command/result, and both clean/live image and manifest hashes in Beads; close the issue. Then run:

```bash
git status --short --branch
git push
git status --short --branch
```

Expected: push succeeds without changing HEAD; all implementation through the current sealed bundle is committed, pushed, clean, and up to date before the first flash. If push rejects because the remote moved, return to Step 4; do not pull and then reuse the old bundle.

### Task 8: Back up, flash, and verify Stage 0 on the real DevKit

**Files:**
- No repository changes expected.
- External evidence: Pi backup, receipt, staged bundle, and UART log.

- [ ] **Step 1: Record the physical-isolation precondition before mutation**

Record the operator's explicit prior confirmation in Beads: the Pi connects only to the port labeled UART; native USB is disconnected; every header pin is disconnected; no breadboard or treadmill cable is present; UART USB is the sole power source. Confirm the Pi currently enumerates exactly the manifest-bound CP2102N serial device. If that attestation is missing, contradicted, or the USB inventory changes, stop before backup/flash.

- [ ] **Step 2: Verify identity immediately before mutation**

Run locked remote `stage`, remote `verify-bundle`, and `esptool chip-id`. Require ESP32-S3, MAC `94:a9:90:db:b0:e4`, 8 MB flash, and expected N8R8 capabilities.

- [ ] **Step 3: Back up all 8 MB**

Run remote `backup`. Before hashing or receipt acceptance, require the owned physical backup directory to be exact mode `0700` and the raw backup to be an owned, single-link regular file with exact mode `0600`; require exact size `8,388,608`, independently recompute SHA-256 on the Pi, and record the mode-0600 receipt/hash in Beads. Never print or commit backup content.

- [ ] **Step 4: Flash and capture through the single race-free verb**

Run remote `flash-monitor` against the sealed staged bundle and receipt. Before it authorizes flash, require the backup directory, raw backup, and receipt to still satisfy their `0700`/owned-physical, `0600`/owned-single-link-regular, and `0600` checks. It must use `--after no-reset`, open the exact serial device, apply `HardReset` on that already-open descriptor, and capture without clearing the post-reset buffer. Capture the complete flash and UART transcript. `ClassicReset` is forbidden because it enters the ROM bootloader.

- [ ] **Step 5: Verify captured post-flash Stage 0**

Require exact banner, recipe ID, every specified chip/memory/pin field, GPIO15/17/21=input, one terminal record, and `BRINGUP STAGE0 PASS`. A later standalone `monitor` may collect ongoing output but cannot substitute for the reset-bound startup capture.

- [ ] **Step 6: Persist evidence and verify pushed state before stopping**

Add receipt/hash/transcript facts to Beads, then verify `git status` remains clean and the branch remains up to date with its remote. Report Stage 0 evidence. Do not assume the sidecar exists; ask the user to remove USB power before wiring it.

### Task 9: Verify sidecar harness and two true cold boots

**Files:**
- No source changes unless evidence exposes a defect.

- [ ] **Step 1: Install the approved sidecar with USB power removed**

Use `docs/devkit-sidecar-netlist.md`. Confirm no 5 V, native USB, GPIO16/17/18/38, or treadmill connection; inspect resistor counts and LED polarity.

- [ ] **Step 2: Capture two true cold boots**

For each, start the tested remote `cold-monitor` verb before touching power. Follow its `REMOVE UART USB POWER` and `RESTORE UART USB POWER` prompts. It must observe the exact serial device disappear, verify the same CP2102N identity on reappearance, open inside the firmware's five-second settle window, and capture the delayed startup report. Require a power-on reset reason, matching recipe ID, all protected directions input, dark LEDs, and Stage 0 PASS. RTS reset does not count.

- [ ] **Step 3: Exercise the eight-state matrix**

| DPDT | S2 TREAD_OK | S3 VBUS | GPIO4,5,6,7 |
|---|---|---|---|
| BYPASS | OPEN | OPEN | `0,1,0,1` |
| BYPASS | OPEN | CLOSED | `0,1,0,0` |
| BYPASS | CLOSED | OPEN | `0,1,1,1` |
| BYPASS | CLOSED | CLOSED | `0,1,1,0` |
| EMULATE | OPEN | OPEN | `1,0,0,1` |
| EMULATE | OPEN | CLOSED | `1,0,0,0` |
| EMULATE | CLOSED | OPEN | `1,0,1,1` |
| EMULATE | CLOSED | CLOSED | `1,0,1,0` |

Send a unique sequence for each; GPIO15/17/21 must report input and both LEDs remain dark.

- [ ] **Step 4: Observe for five minutes and record evidence**

Fail on reboot, panic, duplicate terminal report, direction change, oversized output, or LED lighting. Record commit, recipe/manifest IDs, backup receipt/hash, cold-boot log hashes, samples, and observation in `precor-9_3x-1y2`. Keep the issue in progress until Task 10 proves the verified commit and remote state did not change.

### Task 10: Final regression, provenance freeze, and closure

**Files:** all implementation/documentation files.

- [ ] **Step 1: Prove source and artifact identity are unchanged since hardware verification**

Require a clean worktree. Compare current HEAD, recipe ID, and manifest hash with the Task 8/9 evidence. If any differs, hardware evidence is stale: return to Task 7 Step 3, rebuild, and repeat Tasks 8 and 9 before continuing.

- [ ] **Step 2: Run focused tests**

```bash
cargo test --manifest-path hardware/Esp32Tap/firmware/esp32_rs/bringup_core/Cargo.toml
python3 -m pytest -q hardware/Esp32Tap/firmware/esp32_rs/tools/test_devkit_*.py
```

- [ ] **Step 3: Run inherited gates**

```bash
cd hardware/Esp32Tap/firmware/esp32_rs
python3 -m pytest -q tools/test_artifact_inputs.py tools/test_artifact_provenance.py tools/test_build_image.py tools/test_provenance_entrypoints.py tools/test_snapshot_build.py
python3 tools/check_unsafe_budget.py
python3 tools/check_case_parity.py
python3 tools/check_pins.py
python3 tools/check_wdt_chain.py
```

- [ ] **Step 4: Run the normal release sweep**

Run `tools/sweep.sh` through the inherited immutable artifact entrypoint required by the hardened branch. Do not replace safety coverage with the unresolved performance threshold. Record exact argv, status, commit, artifact identities, and elapsed time. Any failed gate that requires a source change invalidates the hardware evidence and restarts at Task 7 Step 3.

- [ ] **Step 5: Synchronize without changing the verified commit**

```bash
before=$(git rev-parse HEAD)
git pull --rebase
after=$(git rev-parse HEAD)
test "$before" = "$after"
```

If HEAD changes, do not close the issue or reuse hardware evidence; restart at Task 7 Step 3.

- [ ] **Step 6: Close the issue and push verified state**

Only now close `precor-9_3x-1y2` with all recorded evidence.

```bash
git status --short --branch
git push
git status --short --branch
```

Expected: push succeeds without changing HEAD; branch is clean and up to date with `origin/feat/esp32tap-devkit-bringup`.
