# Esp32Tap firmware (phase 1: safety-critical core)

**Status: HOLD.** `safety_model.py` is an executable host reference contract,
not production ESP-IDF firmware. No Emulate-capable binary exists yet, and no
repository test substitutes for contact-measured bench evidence. Do not submit
an order, pay, connect this board to the treadmill, or represent it as safe to
operate on the strength of the host model.

This tree is for **bench/CI only** (`firmware/PLAN.md` is normative). It is an
ESP-IDF **fork of the Pi firmware** (`cpp/` — kv_protocol, mode_state, the
emulation cycle, serial IO seams) plus a C++ port of the `safety_model.py`
`Controller`. Nothing here has run on hardware; the UART-loopback half of M1
and every M2/M3 bench gate remain open, and the treadmill-contact gate is not
even in sight.

## Layout

- `components/portable_core/` — OS-free fork of `cpp/protocol` + `cpp/engine`
  (see `PROVENANCE.md` for per-file origin hashes and diff rationale) plus
  `safety/safety_controller.*`, the line-faithful C++ port of
  `../safety_model.py`. Compiles for esp32s3 AND the host test build.
- `components/esp_hal/` — ESP-IDF HAL: inverted UARTs
  (`uart_set_line_inverse`), safety GPIO (outputs low first; `TREAD_OK_MCU`
  input-only), `esp_timer` clock, task-WDT helpers. Pin constants in
  `pins.hpp` are derived from the Rev E hardware sources and verified by
  `tools/check_pins.py` against `hardware/Esp32Tap/tools/design.py`.
- `main/` — boot sequence + three WDT-supervised tasks (serial engine,
  emulate cycle, interval-executor stub) and deferred-tier stubs
  (FTMS/HRM/WSS/mDNS — TODO(M4/M5), not compiled in).
- `host/` — host test build: forked `cpp/tests` doctest suites (golden
  parity) + new safety-envelope suites against `fakes/fake_hal.h`.

## Build & test

```bash
# Host suite (pin-map check + no-RTTI-construct guard + 7 doctest binaries)
make -C host test

# Firmware build (esp32s3) via the PINNED espressif/idf Docker image
docker run --rm \
  -v "$(pwd)":/project -w /project espressif/idf:release-v5.5 \
  idf.py set-target esp32s3 build

# Mandatory sdkconfig gate on every build (PLAN normative)
grep CONFIG_ESP_TASK_WDT_PANIC=y sdkconfig

# QEMU boot smoke gate (esp32s3 under the espressif QEMU fork shipped in
# the same pinned image): boots headless, asserts app_main completes, all
# three supervised tasks start, boot state is PROXY with relay released,
# and >=15 s of guest uptime with NO task-WDT trigger / panic / reboot.
tools/qemu_smoke.sh

# Artifact-level RTTI gate: no typeinfo symbols for firmware classes in
# the linked ELF (verifies the -fno-rtti property on the artifact itself,
# not just the flag plumbing).
tools/check_rtti_elf.sh
```

QEMU note: the esp32s3 machine leaves the safety GPIO inputs floating, so
the first relay-feedback sample decodes BOTH_CLOSED and the controller
latches a fault at boot (`boot state: ... fault=1`). That is the model's
correct fail-safe reaction to impossible feedback — mode stays PROXY with
the relay released, which is exactly what the smoke test asserts. UART1/2
silence in QEMU is likewise a normal Proxy condition (the serial engine
polls zero bytes; console freshness only gates Emulate entry).

Docker image is pinned to `espressif/idf:release-v5.5` (IDF v5.5 — the
PLAN's prescribed "ESP-IDF 5.x", not a rolling `:latest`/dev snapshot);
`tools/qemu_smoke.sh` uses the same pinned tag (override only via
`IDF_IMAGE=` for experiments, never in CI).
Toolchain flag note: with `CONFIG_COMPILER_CXX_RTTI` unset, IDF 5.x
applies `-fno-rtti` to every C++ compile AND the link via the response
file `build/toolchain/cxxflags` (referenced as `CMAKE_CXX_FLAGS=@…`), so
it does NOT appear inline in `compile_commands.json` — verified by
inspecting that file after a clean v5.5 build (every firmware compile
command references the response file). `-fno-exceptions` is
inline (from `CONFIG_COMPILER_CXX_EXCEPTIONS` unset). The host build
additionally passes `-std=c++20 -fno-exceptions -fno-rtti -Werror`
explicitly, `make -C host test` greps the firmware sources for
`typeid`/`dynamic_cast`, and `tools/check_rtti_elf.sh` asserts the
linked esp32s3 ELF contains no firmware typeinfo symbols — so an RTTI
construct can never slip in silently at source OR artifact level.

## Safety invariants carried by this code (host-verified only)

- Boot = Proxy, outputs low, relay feedback UNKNOWN until a real GPIO sample.
- Gap-safe emulate entry/exit exactly per PLAN (zero first, tx_enable before
  relay_cmd, 1 ms continuous feedback with a real sample before the 10 ms
  deadline, exact deadlines fail closed). The 10 ms qualification is
  unsatisfiable at the serial task's 5 ms cadence, so relay transfers run
  a dedicated sub-ms sampling window (`portable_core/safety/
  feedback_window.h`, `FEEDBACK_POLL_US`); the host suite drives entry
  AND exit at the real task cadence to prove they complete.
- PLAN entry step 6: the first transmitted burst after emulate entry
  encodes hmph=0/inc=0 even if the owner commanded motion during the
  entry window (`engine/emulate_task_policy.h` defers the
  controller→cycle motion mirror until the first zero burst went out;
  motion acceptance during entry stays model-faithful).
- FreeRTOS tick is 1000 Hz (`CONFIG_FREERTOS_HZ=1000`): at the IDF
  default 100 Hz, `pdMS_TO_TICKS(5)` truncates to 0 and the serial
  engine busy-spins, starving core 0 into a task-WDT panic loop. A
  `static_assert` in `main/serial_engine_task.cpp` makes that regression
  a compile error; `tools/qemu_smoke.sh` catches it at runtime.
- Clamps: speed 0–120 tenths, incline 0–30 half-pct app limit (0–198
  absolute guard in the cycle engine), 3-hour no-change timeout. The
  timeout zeroes BOTH the cycle engine (wire frames) and the
  authoritative SafetyController motion (`safety_timeout_zero_motion`,
  via `EmulationCycle::consume_safety_timeout()`), so status can never
  report stale motion and the controller→mode mirror cannot resurrect it
  regardless of mirror/tick ordering.
- 4 s manual lease, generation supersession, non-owner ignore; 1.5 s
  console-freshness with complete-valid-frame semantics.
- Task WDT (2 s, panic) subscribes serial engine + emulate cycle + interval
  executor; relay release on stall is completed by hardware pull-downs.
  Subscription is fail-loud: if `esp_task_wdt_add` fails, the task calls
  `esp_system_abort` (panic → reset → relay released) rather than running
  unsupervised.

None of this is bench evidence. See `../PLAN.md` "Milestones" and the
treadmill-contact gate checklist before believing anything stronger.
