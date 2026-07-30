# portable_core fork provenance

Every file below is a FORK of the Pi firmware (`cpp/`, read-only) taken at
the origin sha256 recorded here. Rule: a `diff` of each fork vs its origin
must show ONLY lines this table justifies. `cpp/` is never edited.

| Destination | Origin (repo path) | Origin sha256 at fork time | Diff rationale |
|---|---|---|---|
| `protocol/kv_protocol.h` | `cpp/protocol/kv_protocol.h` | `bebb475d15e7d78a6d361c72969d3415056b7108a8cbabcf179d39882c159685` | Verbatim (byte-identical). |
| `protocol/kv_protocol.cpp` | `cpp/protocol/kv_protocol.cpp` | `6f6bf6a4fbaa22b41263c0aef0864d7c2174ec21b8f20e525b02bcb554d6e39b` | Verbatim (byte-identical). |
| `engine/mode_state.h` | `cpp/engine/mode_state.h` | `6b61463de32fd77c98572a8cd6af8f267393bf38b5b90efa2524759339097df6` | Verbatim (byte-identical). |
| `engine/mode_state.cpp` | `cpp/engine/mode_state.cpp` | `b8a8e2ab0ad69c428a68cc4ee8be631c79e8f9e7c02a2f122f316f89b69ba8a3` | Verbatim (byte-identical). |
| `util/ring_buffer.h` | `cpp/ipc/ring_buffer.h` | `48b4e3cbd85d43397e508126b860c87bdb355a2d14dcf961defa33172a727343` | Verbatim (byte-identical); relocated `ipc/` → `util/` (the IPC server stays behind on the Pi). |
| `engine/serial_io.h` | `cpp/engine/serial_io.h` | `bb9a8ed889072a4dfdf5e9e995092157956b189689a0313c338e7b110cacb97f` | Seam fork. Deleted: pigpio `gpioPulse_t` shim (orig 25–32), `SerialReader::open/close` + pigpio `serial_read_open/invert/close` (orig 46–55), pin/baud plumbing in ctors, DMA-wave pulse building + busy-wait in `SerialWriter::write_bytes` (orig 132–184). Replaced by `Port::read(span)` / `Port::write(span)` (hardware-inverted UART on target). `poll()` parse-buffer logic unchanged except `rawbuf`/`pairs` moved from stack locals to members (PLAN QEMU stack constraint; 4 KB `KvPair[32]` must not live on a task stack). `write_kv`/`write_bytes` signatures and the 50-byte cap kept. |
| `engine/emulation_cycle.h` | `cpp/engine/emulation_engine.h` | `3a8ba7295f29cb402d6dcd164e25f24bfecdcc4f46e7d6a250865ca15055d1e5` | Seam fork (renamed `EmulationEngine` → `EmulationCycle`). Deleted: `std::thread` lifecycle — dtor/`start()`/`stop()`/`is_running()`/`sleep_ms` (orig 64–91), `thread_fn`'s outer while-loop + `goto done` exits (orig 105–159), `clock_gettime` timekeeping (orig 106–124), stderr logging (orig 128). Added: `reset(now_us)` re-arm and `tick(now_us)` sending ONE burst per call with injected int64 µs time; the owning FreeRTOS task/test harness supplies the 100 ms gap (`EMU_BURST_GAP_MS`) and loop. `KV_CYCLE`, `BURSTS`, `value_for` (part=6/diag=0/loop=5550), 3-hour `EMU_TIMEOUT_SEC` logic and mode checks unchanged. Behavior nuance: the state snapshot is taken per burst instead of once per 5-burst cycle (fresher values; no safety semantics change). Added: `timeout_fired_` flag + `consume_safety_timeout()` — on the ESP32 the authoritative motion state lives in `SafetyController`, not `ModeStateMachine`, so the owning task must be told the 3-hour timeout fired to zero the controller too (`safety_timeout_zero_motion`); on the Pi the single authoritative state makes this unnecessary. |

Host-test forks (in `host/tests/`, same rule):

| Destination | Origin | Origin sha256 | Diff rationale |
|---|---|---|---|
| `host/tests/test_kv_protocol.cpp` | `cpp/tests/test_kv_protocol.cpp` | `e81f1d46de231ad4b992f6b5a1b8b860e5f502dbdfe9f595275ec337afb7ad5f` | Verbatim; asserts unchanged. |
| `host/tests/test_mode_state.cpp` | `cpp/tests/test_mode_state.cpp` | `1a9af9f1bd9695db99db21821268a7c5dc82df6304585023794590c12b86c319` | Verbatim; asserts unchanged. |
| `host/tests/test_ring_buffer.cpp` | `cpp/tests/test_ring_buffer.cpp` | `71f44eface78bbeb582974420c6ed6243676ccf0255389bdef2a549ecfb8f582` | One include path: `ipc/ring_buffer.h` → `util/ring_buffer.h`; asserts unchanged. |
| `host/tests/test_emulation.cpp` | `cpp/tests/test_emulation.cpp` | `f9721e18fdc2d615b0ef2ae1e016b765fea12a8c7e0d6f49f0ad3e23fa26f57e` | Port seam adapted: `MockGpioPort`+threads+real sleeps → `FakePort`+deterministic `tick(now_us)`; every original assertion's intent preserved (14-key order, hex encodings, stop-on-mode-change, stop-after-watchdog); added a fake-clock 3-hour-timeout case (untestable in the original's real-time harness). |

New files (no `cpp/` origin): `safety/safety_constants.h`,
`safety/safety_controller.{h,cpp}` (C++ port of `../safety_model.py`
`Controller` — the Python model is the normative origin), `hal/hal.h`,
`engine/key_cache.h` (hmph/inc last-value tracking for console-takeover
detection, extracted from the serial engine task so its exchange/lifetime
semantics are host-testable; the returned previous-value view refers to a
caller-owned buffer, never internal or local storage),
`safety/feedback_window.h` (task-layer sub-ms relay-feedback sampling
window: the 10 ms qualification deadline is unsatisfiable at the serial
task's 5 ms cadence, so ENTRY/EXIT_WAIT_FEEDBACK get a dedicated
`FEEDBACK_POLL_US` poll loop; header-only and HAL-free so the host suite
drives it at the real task cadence), and
`engine/emulate_task_policy.h` (per-iteration arm/force-proxy/mirror/send
decisions of the emulate cycle task, including the PLAN entry-step-6
first-burst-zero gate: owner motion accepted during ENTRY_WAIT_* is not
mirrored into the cycle engine until the first post-entry zero burst has
actually been transmitted).

Known deviations from `safety_model.py` (all fail-safe):

- Fixed capacities replace unbounded Python collections: 8 active
  connections, 16 tracked generation keys, 256-event ring. Overflow REJECTS
  the new connection (`connection_rejected:capacity`) — refusing a
  connection can never energize the relay.
- Handles are `int32_t` (PLAN D5 phase-1 stand-ins); WSS object-identity
  keying collapses to integer-handle identity until the real WSS tier
  lands (M5).
- Invalid identities (negative generation) are rejected at `connect()`
  with an event instead of raising, since the build is `-fno-exceptions`.
- `safety_timeout_zero_motion(now)` is a fork extension with no
  `safety_model.py` counterpart: the model does not carry the 3-hour
  emulate inactivity timeout (that lives in `EmulationCycle`, Pi parity
  with `cpp/emulation_engine.h`). When the cycle engine fires the
  timeout it zeroes `ModeStateMachine`; the task then calls this to zero
  the authoritative controller motion as well, so status can never
  report stale motion and the controller->mode mirror cannot resurrect
  it. Motion-only, monotonic toward safe — mode/lease/relay untouched.
