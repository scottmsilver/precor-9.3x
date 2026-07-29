/*!
 * ble_core — the BLE **protocol** tier: FTMS (Fitness Machine Service) wire
 * encoding, Heart Rate Measurement parsing, and the unit conversion between
 * what BLE speaks (km/h, percent) and what the belt speaks (tenths of mph,
 * half-percent).
 *
 * ## What this crate is a port OF
 *
 * `rust/ftms/src/protocol.rs` and `rust/hrm/src/scanner.rs` — the WORKING Pi
 * daemons, in production against Zwift/QZ/Garmin/Apple Watch and real chest
 * straps. Their encodings are the specification here. Every vector in
 * `tests/ftms_protocol.rs` and `tests/hr_measurement.rs` is theirs, ported byte
 * for byte, **including the ones that pin lossy behaviour** (the truncating
 * mph->km/h divide that makes 12.0 mph encode as 1930 and not the 1931 the
 * Speed Range characteristic advertises; the uint24 distance field that drops
 * the top byte of a u32). If this crate ever disagrees with the daemon, THIS
 * crate is wrong: a phone that has been talking to the Pi must see the same
 * bytes from the ESP32.
 *
 * What did NOT come across, and could not: the daemons' transport is `bluer`,
 * i.e. BlueZ over D-Bus, i.e. Linux. The radio work is NimBLE and lives in the
 * firmware crate. Nothing in here knows a radio exists.
 *
 * ## Structural guarantees, the same three `safety_core` and `program_core` carry
 *
 *  * `#![no_std]` outside `cfg(test)` and `alloc` is never named, so an
 *    allocation in an encode or a parse is a COMPILE ERROR. The Pi daemon
 *    returns `Vec<u8>` from `encode_treadmill_data` and `vec![]` from
 *    `encode_control_response`; on a 512 KB part a notify path that allocates
 *    once per second, per subscriber, is a slow leak with a reboot at the end
 *    of it — and a reboot drops the relay mid-run. Every encoder here returns
 *    a fixed-size array or a `[u8; N]`-backed value with a length.
 *  * `#![forbid(unsafe_code)]` — enforced, not asserted: `unsafe` cannot be
 *    re-enabled by an inner `allow`, and `tools/check_unsafe_budget.py` fails
 *    the build if this line goes missing or an `unsafe` token appears.
 *  * The belt-facing output of a Control Point write is `safety_core`'s
 *    `SpeedTenths` / `InclineHalfPct`, the SAME newtypes
 *    `SafetyController::command_motion` takes. A BLE peer's number cannot
 *    reach the belt in the wrong unit because there is no other way to spell
 *    the value.
 *
 * ## What this crate deliberately does NOT do
 *
 * **It does not touch the belt, and it does not clamp.** [`ftms::CpEffect`] is
 * a description of what the peer asked for, in belt units. Turning that into
 * motion is `esp32tap/src/control.rs`'s job — THE ONE PATH TO THE BELT — which
 * owns the lease, the clamps and the auto-emulate policy that HTTP already
 * goes through. A clamp here would be a SECOND opinion about what is safe, and
 * two opinions that agree today are the thing `control.rs`'s own header
 * warns about. The Pi daemon clamps in `handle_control_command`
 * (`.clamp(0.0, 12.0)`) because on that side there was no shared choke point;
 * here there is, so an out-of-range Control Point write converts faithfully,
 * is REFUSED by the controller, and the refusal is reported back to the peer
 * as `RESULT_INVALID_PARAM`. See [`ftms::CpEffect`] for the full argument.
 *
 * **It does not advertise, connect, pair, or notify.** None of that is
 * verifiable without a radio, and QEMU has none. Bead precor-9_3x-l0h names
 * every item that stays unproven until a board exists — including whether
 * NimBLE's heap cost leaves room for TLS and the app tier, which is
 * unmeasured.
 */

#![cfg_attr(not(test), no_std)]
#![forbid(unsafe_code)]

pub mod ftms;
pub mod hrm;
