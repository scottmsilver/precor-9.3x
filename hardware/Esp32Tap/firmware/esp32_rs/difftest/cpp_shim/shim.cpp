/*
 * shim.cpp — extern "C" seam onto the COMMITTED, UNMODIFIED C++ safety core.
 *
 * This file exists only so the Rust differential harness can drive the exact
 * same `kv_protocol.cpp`, `mode_state.cpp` and `safety_controller.cpp`
 * translation units the C++ host tests and the C++ firmware image use. It
 * adds no logic: every function is a thin forward.
 *
 * Built with the SAME flags as host/Makefile (-std=c++20 -fno-exceptions
 * -fno-rtti -O2) so the behaviour under test is byte-for-byte the shipped one.
 */

#include <cstdint>
#include <cstring>
#include <span>
#include <string>
#include <string_view>

#include "engine/mode_state.h"
#include "protocol/kv_protocol.h"
#include "safety/safety_controller.h"

using namespace esp32tap::safety;

namespace {

void copy_out(std::string_view src, char* dst, int cap) {
    int n = static_cast<int>(src.size());
    if (n > cap - 1) n = cap - 1;
    if (n > 0) std::memcpy(dst, src.data(), static_cast<size_t>(n));
    dst[n] = '\0';
}

}  // namespace

extern "C" {

// ── kv_protocol ─────────────────────────────────────────────────────

// Flattened KvPair output: keys[i*64..], values[i*64..], both NUL-terminated.
int cpp_kv_parse(const uint8_t* buf, int len, int max_pairs, char* keys_out,
                 char* values_out, int* consumed_out) {
    if (max_pairs > 32) max_pairs = 32;
    KvPair pairs[32];
    int consumed = 0;
    int n = kv_parse(std::span<const uint8_t>(buf, static_cast<size_t>(len)),
                     pairs, max_pairs, &consumed);
    for (int i = 0; i < n; i++) {
        copy_out(pairs[i].key_view(), keys_out + i * KV_FIELD_SIZE,
                 KV_FIELD_SIZE);
        copy_out(pairs[i].value_view(), values_out + i * KV_FIELD_SIZE,
                 KV_FIELD_SIZE);
    }
    *consumed_out = consumed;
    return n;
}

int cpp_kv_build(const char* key, const char* value, uint8_t* out, int cap) {
    std::string s = kv_build(key, value);
    int n = static_cast<int>(s.size());
    if (n > cap) n = cap;
    std::memcpy(out, s.data(), static_cast<size_t>(n));
    return n;
}

int cpp_encode_speed_hex(int tenths, char* out, int cap) {
    std::string s = encode_speed_hex(tenths);
    copy_out(s, out, cap);
    return static_cast<int>(s.size());
}

int cpp_decode_speed_hex(const char* hex) { return decode_speed_hex(hex); }

int cpp_encode_incline_hex(int half_pct, char* out, int cap) {
    std::string s = encode_incline_hex(half_pct);
    copy_out(s, out, cap);
    return static_cast<int>(s.size());
}

int cpp_decode_incline_hex(const char* hex) { return decode_incline_hex(hex); }

// ── mode_state ──────────────────────────────────────────────────────

void* cpp_mode_new() {
    auto* m = new ModeStateMachine();
    m->set_emulate_callback([](bool) {});
    return m;
}
void cpp_mode_free(void* h) { delete static_cast<ModeStateMachine*>(h); }

// Transition results are packed: bit0 changed, bit1 started, bit2 stopped.
static int pack(const TransitionResult& r) {
    return (r.changed ? 1 : 0) | (r.emulate_started ? 2 : 0) |
           (r.emulate_stopped ? 4 : 0);
}

int cpp_mode_request_proxy(void* h, int enabled) {
    return pack(static_cast<ModeStateMachine*>(h)->request_proxy(enabled != 0));
}
int cpp_mode_request_emulate(void* h, int enabled) {
    return pack(
        static_cast<ModeStateMachine*>(h)->request_emulate(enabled != 0));
}
int cpp_mode_set_speed(void* h, int tenths) {
    return pack(static_cast<ModeStateMachine*>(h)->set_speed(tenths));
}
int cpp_mode_set_speed_mph(void* h, double mph) {
    return pack(static_cast<ModeStateMachine*>(h)->set_speed_mph(mph));
}
int cpp_mode_set_incline(void* h, int half_pct) {
    return pack(static_cast<ModeStateMachine*>(h)->set_incline(half_pct));
}
int cpp_mode_auto_proxy(void* h, const char* key, const char* old_val,
                        const char* new_val) {
    return pack(static_cast<ModeStateMachine*>(h)->auto_proxy_on_console_change(
        key, old_val, new_val));
}
void cpp_mode_safety_timeout_reset(void* h) {
    static_cast<ModeStateMachine*>(h)->safety_timeout_reset();
}
void cpp_mode_watchdog_reset(void* h) {
    static_cast<ModeStateMachine*>(h)->watchdog_reset_to_proxy();
}
void cpp_mode_add_console_bytes(void* h, uint32_t n) {
    static_cast<ModeStateMachine*>(h)->add_console_bytes(n);
}
void cpp_mode_add_motor_bytes(void* h, uint32_t n) {
    static_cast<ModeStateMachine*>(h)->add_motor_bytes(n);
}

// out: [mode, speed_tenths, speed_raw, incline, proxy, emulate,
//       console_bytes, motor_bytes]
void cpp_mode_snapshot(void* h, int64_t* out) {
    auto* m = static_cast<ModeStateMachine*>(h);
    StateSnapshot s = m->snapshot();
    out[0] = static_cast<int64_t>(s.mode);
    out[1] = s.speed_tenths;
    out[2] = s.speed_raw;
    out[3] = s.incline;
    out[4] = s.proxy_enabled ? 1 : 0;
    out[5] = s.emulate_enabled ? 1 : 0;
    out[6] = m->console_bytes();
    out[7] = m->motor_bytes();
}

// ── safety_controller ───────────────────────────────────────────────

void* cpp_ctl_new() { return new SafetyController(); }
void cpp_ctl_free(void* h) { delete static_cast<SafetyController*>(h); }

static ConnectionIdentity ident(int transport, int32_t handle,
                                int64_t generation) {
    return ConnectionIdentity{static_cast<Transport>(transport), handle,
                              generation};
}

int cpp_ctl_connect(void* h, int t, int32_t handle, int64_t gen) {
    return static_cast<SafetyController*>(h)->connect(ident(t, handle, gen))
               ? 1
               : 0;
}
int cpp_ctl_acquire(void* h, int t, int32_t handle, int64_t gen, int64_t now) {
    return static_cast<SafetyController*>(h)->acquire(ident(t, handle, gen),
                                                      now)
               ? 1
               : 0;
}
int cpp_ctl_heartbeat(void* h, int t, int32_t handle, int64_t gen,
                      int64_t now) {
    return static_cast<SafetyController*>(h)->heartbeat(ident(t, handle, gen),
                                                        now)
               ? 1
               : 0;
}
int cpp_ctl_command_motion(void* h, int t, int32_t handle, int64_t gen,
                           int speed, int incline, int64_t now) {
    return static_cast<SafetyController*>(h)->command_motion(
               ident(t, handle, gen), speed, incline, now)
               ? 1
               : 0;
}
int cpp_ctl_disconnect(void* h, int t, int32_t handle, int64_t gen,
                       int64_t now) {
    return static_cast<SafetyController*>(h)->disconnect(ident(t, handle, gen),
                                                         now)
               ? 1
               : 0;
}
int cpp_ctl_disconnect_transport(void* h, int t, int64_t now) {
    return static_cast<SafetyController*>(h)->disconnect_transport(
               static_cast<Transport>(t), now)
               ? 1
               : 0;
}
int cpp_ctl_observe_console_bytes(void* h, const uint8_t* data, int len,
                                  int64_t now) {
    return static_cast<SafetyController*>(h)->observe_console_bytes(
        std::span<const uint8_t>(data, static_cast<size_t>(len)), now);
}
int cpp_ctl_request_emulate(void* h, int t, int32_t handle, int64_t gen,
                            int64_t now, int uart_idle_low) {
    return static_cast<SafetyController*>(h)->request_emulate(
               ident(t, handle, gen), now, uart_idle_low != 0)
               ? 1
               : 0;
}
int cpp_ctl_observe_interframe_gap(void* h, int64_t now) {
    return static_cast<SafetyController*>(h)->observe_interframe_gap(now) ? 1
                                                                          : 0;
}
int cpp_ctl_observe_relay_feedback(void* h, int nc_high, int no_high,
                                   int64_t now) {
    return static_cast<int>(
        static_cast<SafetyController*>(h)->observe_relay_feedback(
            nc_high != 0, no_high != 0, now));
}
int cpp_ctl_request_normal_exit(void* h, int t, int32_t handle, int64_t gen,
                                int64_t now) {
    return static_cast<SafetyController*>(h)->request_normal_exit(
               ident(t, handle, gen), now)
               ? 1
               : 0;
}
void cpp_ctl_set_tread_ok(void* h, int value, int64_t now) {
    static_cast<SafetyController*>(h)->set_tread_ok(value != 0, now);
}
void cpp_ctl_set_vbus_present_n(void* h, int level_high) {
    static_cast<SafetyController*>(h)->set_vbus_present_n(level_high != 0);
}
void cpp_ctl_tick(void* h, int64_t now) {
    static_cast<SafetyController*>(h)->tick(now);
}
void cpp_ctl_safety_timeout_zero_motion(void* h, int64_t now) {
    static_cast<SafetyController*>(h)->safety_timeout_zero_motion(now);
}
void cpp_ctl_emergency_stop(void* h, const char* reason, int64_t now) {
    static_cast<SafetyController*>(h)->emergency_stop(reason, now);
}
void cpp_ctl_watchdog_stall(void* h, int64_t now) {
    static_cast<SafetyController*>(h)->watchdog_stall(now);
}
void cpp_ctl_reset(void* h, const char* reason, int64_t now) {
    static_cast<SafetyController*>(h)->reset(now, reason);
}

// Full observable tuple, in one call:
//  0 mode, 1 speed, 2 incline, 3 tread_ok, 4 feedback, 5 fault_latched,
//  6 relay_cmd, 7 tx_enable, 8 usb_pullup, 9 has_last_frame,
// 10 last_frame_at, 11 has_owner, 12 owner_transport, 13 owner_handle,
// 14 owner_generation, 15 has_lease_expiry, 16 lease_expires_at,
// 17 event_count
void cpp_ctl_state(void* h, int64_t* out) {
    auto* c = static_cast<SafetyController*>(h);
    out[0] = static_cast<int64_t>(c->mode());
    out[1] = c->speed_tenths();
    out[2] = c->incline_half_percent();
    out[3] = c->tread_ok() ? 1 : 0;
    out[4] = static_cast<int64_t>(c->feedback());
    out[5] = c->fault_latched() ? 1 : 0;
    out[6] = c->relay_cmd() ? 1 : 0;
    out[7] = c->tx_enable() ? 1 : 0;
    out[8] = c->usb_pullup_enabled() ? 1 : 0;
    auto lf = c->last_complete_console_frame_at();
    out[9] = lf.has_value() ? 1 : 0;
    out[10] = lf.value_or(0);
    auto o = c->owner();
    out[11] = o.has_value() ? 1 : 0;
    out[12] = o.has_value() ? static_cast<int64_t>(o->transport) : -1;
    out[13] = o.has_value() ? o->handle : -1;
    out[14] = o.has_value() ? o->generation : -1;
    auto le = c->lease_expires_at();
    out[15] = le.has_value() ? 1 : 0;
    out[16] = le.value_or(0);
    out[17] = static_cast<int64_t>(c->event_count());
}

int cpp_ctl_event_at(void* h, uint64_t index, char* out, int cap) {
    auto sv = static_cast<SafetyController*>(h)->event_at(index);
    copy_out(sv, out, cap);
    return static_cast<int>(sv.size());
}

}  // extern "C"
