# Deploy the Treadmill Software Family to the Pi Zero 2 W — Design

**Date:** 2026-05-17
**Status:** Approved (brainstorming) — pending implementation plan

## Goal

One unified way to put the full treadmill software family — `treadmill_io`
(C++/pigpio), `ftms-daemon` + `hrm-daemon` (Rust), and `treadmill-server`
(Python/FastAPI incl. the Gemini AI coach and web UI) — onto the
newly-provisioned Pi Zero 2 W (`rpi-zero`, 192.168.1.206, aarch64, 512 MB),
while keeping the Pi 4 (`rpi`) usable as a spare.

## Decisions (locked during brainstorming)

| Axis | Decision |
|---|---|
| Deploy model | Hybrid: bake a stable baseline into the image, rsync for iteration |
| C++/Rust build | One aarch64 cross toolchain everywhere; retire build-on-Pi for compiled code |
| Service scope | Full family — `treadmill_io` + `ftms` + `hrm` + `treadmill-server` (AI coach + web UI) |
| Pi role | Zero 2 W primary, Pi 4 spare; deploy targets either host by name; config host-portable |
| Memory (512 MB) | Trim the baseline first; ordered fallback ladder; graceful AI-unavailable only as last resort |

## Architecture

One aarch64 cross toolchain (Docker, same pattern as the existing `cross`
Rust builds) produces `treadmill_io`, `ftms-daemon`, `hrm-daemon`, and the
Vite web build. A single declarative manifest (`deploy/manifest.txt`)
records every artifact's source, destination, mode, and owner. Two
consumers read that manifest:

- **Bake** (`provisioning/dietpi/`): folds the full family into a
  flashable `.img`. `treadmill_io` is wired into the committed
  `treadmill-critical.target` (Path A: network-independent, fires
  ~6.7 s). `server`/`ftms`/`hrm` stay on the normal multi-user chain
  (they require network/Bluetooth).
- **Iterate** (`deploy/deploy.sh`): cross-builds locally, rsyncs to
  `PI_HOST` (default `rpi-zero`; `rpi` still valid), restarts services.

Because both consumers install the same artifacts from the same manifest,
a freshly-flashed Pi and an rsync-iterated Pi are byte-identical.

The manifest is parsed as **data**, never sourced — same fail-closed
posture as the audited `load_secrets` in `provisioning/dietpi/lib.sh`
(whitelist of `kind`; reject paths containing `..` or a leading `/`
outside known roots).

### Build-on-Pi retirement (with one nuance)

C++ and Rust are cross-compiled in containers; build-on-Pi is retired
for compiled code. The Python venv remains `pip install`-ed on the Pi
(first boot for the baked image; only when `requirements` change for
rsync iteration). Cross-building a venv is brittle and the dependencies
ship aarch64 wheels (`fastapi`, `uvicorn`, `google-genai`, `gpxpy`,
`pydantic`). "No build on Pi" means the compile step, not pip.

## Components & file structure

### New

- `deploy/manifest.txt` — declarative artifact table. One row per
  artifact: `kind  src  dest  mode  owner`. `kind ∈ {bin, tree, file,
  unit}`. `@USER@` placeholder resolved per host. Example:
  ```
  bin   build/treadmill_io          /usr/local/bin/treadmill_io   0755 root
  bin   build/ftms-daemon           /usr/local/bin/ftms-daemon    0755 root
  bin   build/hrm-daemon            /usr/local/bin/hrm-daemon     0755 root
  tree  python/                     ~/treadmill/python/           0644 @USER@
  file  gpio.json                   ~/treadmill/gpio.json         0644 @USER@
  tree  static/                     ~/treadmill/static/           0644 @USER@
  unit  build/treadmill-io.service  /etc/systemd/system/          0644 root
  unit  build/treadmill-server.service /etc/systemd/system/       0644 root
  unit  build/ftms.service          /etc/systemd/system/          0644 root
  unit  build/hrm.service           /etc/systemd/system/          0644 root
  ```

- `deploy/lib-artifacts.sh` — shared functions sourced by both
  `deploy.sh` and the provisioning staging code:
  `manifest_parse`, `manifest_validate` (fail-closed), `manifest_place`
  (rsync side), `manifest_stage` (image side).

- `deploy/cross/Dockerfile.cpp` — `g++-aarch64-linux-gnu` + aarch64
  `libpigpio-dev` (multiarch) + vendored headers. Mirrors
  `rust/hrm/Dockerfile.cross`.

- `provisioning/dietpi/tests/test_manifest.sh` — dependency-free parser
  tests (matches the existing `test_*.sh` harness style; ends
  `ALL TESTS PASSED`).

- `deploy/tests/test_deploy_dryrun.sh` — golden `deploy.sh --dry-run`
  output; no host required.

- A memory-headroom load test (see Validation gate).

### Rewritten / modified

- `deploy/deploy.sh` — drop on-Pi C++ build; `make cross` → assemble per
  manifest → rsync to `PI_HOST` (default `rpi-zero`) → ordered service
  restart. Safety rules below.

- `Makefile` — `make cross` (three aarch64 binaries via containers),
  `make image` (bake the full appliance), `make deploy` retargeted; old
  per-Pi C++ build targets retired.

- `provisioning/dietpi/prepare-sd.sh` + `build-image.sh` — stage the full
  family via `manifest_stage` (today they carry only `fastboot.tgz` +
  boot config). The audited `build-image.sh` security boundary is
  unchanged; it gains a manifest consumer, not new privilege.

- `provisioning/dietpi/Automation_Custom_Script.sh` — extend the
  idempotent fold-back: install per manifest, create the venv (pip),
  enable the four units, wire `treadmill_io` →
  `treadmill-critical.target`, apply the memory trim, keep bluez.

- `deploy/treadmill-io.service.in` — add Path A wiring via a drop-in
  (`WantedBy=treadmill-critical.target`); ordering stays
  network-independent (`After=local-fs.target`).

- `python/server.py` / `python/program_engine.py` — **(removed
  2026-05-18, net-zero)** `google-genai` was made lazy, then **fully
  reverted** (files restored byte-identical to the pre-plan baseline via
  `git checkout`; `test_lazy_genai.py` deleted) after the live deploy
  measured ~354 MB free on the 463 MB Pi (family ≈109 MB): no memory
  pressure exists, so these files keep their original eager import and
  this plan makes no Python change here.

## Memory trim ladder (ordered, deterministic)

1. Minimal DietPi baseline — no desktop/extras; only what the four
   services need (`dietpi.txt` software selection trimmed; bluez kept).
2. Single uvicorn worker (`--workers 1`) — set in the unit file.
3. ~~Lazy `google-genai` import — SDK loads only on first AI use.~~
   **Removed 2026-05-18:** live measurement showed no memory pressure
   (~354 MB free); reverted to a normal eager top-level import.
4. zram thin margin — small compressed-RAM swap (~25% of RAM, no SD
   wear) as a cushion only.
5. **Last resort:** if the headroom gate still fails with 1–4 applied,
   the AI chat/voice endpoints return a clean "not available on this
   device" response. Core control + FTMS + HRM are never affected. The
   safety path never depends on AI.

## Deploy safety

`treadmill_io` owns the safety-critical logic (3-hour timeout,
zero-speed-on-emulate-start, auto proxy/emulate detection). `deploy.sh`:

- **Refuses to deploy if the belt is moving** — queries `/api/status`;
  non-zero speed aborts with a clear message unless `--force`.
- **Ordered restart:** static files/units first → `treadmill-server`,
  `ftms`, `hrm` → `treadmill_io` **last and atomically**
  (stop→swap→start) so the safety daemon's downtime is minimal and never
  overlaps an emulating belt.
- **Never partial:** rsync fully completes before any `systemctl`; a
  failed rsync aborts before touching services.
- Aborts immediately if `PI_HOST` is unreachable.

## Host portability (coexist)

- `gpio.json`: the Zero 2 W and Pi 4 share the 40-pin header — pin
  assignments are identical; one canonical `gpio.json`.
- Hostnames: `rpi` (Pi 4) vs `rpi-zero` (Zero 2 W). `PI_HOST` env
  selects the target; default `rpi-zero`. No hardcoded host URLs
  (CLAUDE.md rule; `server.py` already complies).
- TLS: `setup.sh` derives the cert name from `hostname` (already
  host-agnostic) so each Pi gets its own `tailscale cert`.

## Validation gate & testing

- **Memory headroom gate** (objective fit pass/fail): boot the Zero with
  all four services, apply synthetic load (one AI chat round-trip +
  FTMS notifying + an active run program), assert steady-state
  `MemAvailable ≥ 40 MB` and zero `oom-kill` in the journal. This gate
  alone decides whether ladder step 5 triggers. Reported honestly.
- **Unit / dependency-free:** `test_manifest.sh` (parser + fail-closed
  cases), `deploy.sh --dry-run` golden output, cross-build
  reproducibility (build twice → identical binary hashes).
- **Integration:** existing C++/Rust/Python suites run against the
  cross-built aarch64 binaries on the Pi (proves the toolchain produces
  working binaries, not merely compiling ones).
- **Operator-run smoke:** bake → flash → boots straight into a working
  treadmill. The one test class an agent cannot self-run (same status as
  the existing outstanding flash test).

## Error handling

- Cross-build failure aborts the bake/deploy; a stale binary is never
  shipped (manifest validation fails closed on missing/old artifacts).
- Memory headroom gate failure escalates the trim ladder to step 5 and
  is reported, not hidden.
- Deploy aborts cleanly (no partial state) on unreachable host, failed
  rsync, or a moving belt.

## Out of scope (YAGNI)

- Custom Buildroot/Yocto image (rejected — abandons the audited DietPi
  toolkit and fast-boot work for weeks of effort).
- Cross-building the Python venv.
- Pushing to any git remote, or committing without the owner password
  (CLAUDE.md).
