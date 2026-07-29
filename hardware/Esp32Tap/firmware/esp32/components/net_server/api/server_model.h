/*
 * server_model.h — the seam between the pure API tier and the device.
 *
 * StatusSnapshot is the executor-published view of the safety
 * controller + motor tap (single-writer rule: only the executor task
 * reads the controller; handlers see this POD copy).
 *
 * ServerModel is what ServerCore calls to touch hardware / transport:
 *  - device impl: safety-controller bridge under controller_mu +
 *    ws pump queue (interval_executor_task.cpp / net glue);
 *  - host impl: a fake recording calls (host/tests).
 * Every motion path funnels through hw_set_speed / hw_set_incline —
 * the same SafetyController::command_motion clamps as the QEMU shim.
 */

#pragma once

#include <array>
#include <cstdint>
#include <string>
#include <string_view>

#include "time_source.h"
#include "ws_outbox.h"

namespace esp32tap::api {

// Streaming sink for the KV caches of every bus source (motor tap,
// console tap, and the frames the device itself synthesizes while
// emulating). Streamed rather than returned in StatusSnapshot: three
// 16-slot arrays would be ~1.2 KB copied onto the stack on every
// status() call, on a part where stack is as scarce as heap.
class KvSink {
public:
    virtual ~KvSink() = default;
    // `source` is one of "motor" / "console" / "emulate" (server.py's
    // kv event `source` field, which the app's Debug KV log columns on).
    virtual void kv(std::string_view source, std::string_view key,
                    std::string_view val) = 0;
};

struct StatusSnapshot {
    bool proxy = true;
    bool emulate = false;
    int emu_speed_tenths = 0;
    int emu_incline_half = 0;
    int bus_speed_tenths = -1;   // -1 == unknown (maps to JSON null)
    int bus_incline_half = -1;   // -1 == unknown
    bool treadmill_connected = true;

    // Raw motor KV cache (hex strings) for the "motor" status dict.
    static constexpr int MAX_KV = 16;
    struct Kv {
        std::array<char, 8> key{};
        std::array<char, 16> val{};
    };
    int kv_count = 0;
    std::array<Kv, MAX_KV> kv{};
};

class ServerModel {
public:
    virtual ~ServerModel() = default;

    virtual StatusSnapshot status() = 0;

    // Motion port. mph > 0 / pct > 0 also request Emulate entry (the C
    // binary's auto-emulate mirror); values are clamped again by the
    // SafetyController (0-120 tenths / 0-30 half-pct). Return false
    // when the hardware bridge is unreachable (-> 503, python's
    // "treadmill_io disconnected").
    virtual bool hw_set_speed(double mph) = 0;
    virtual bool hw_set_incline(double pct) = 0;
    virtual bool set_emulate(bool enabled) = 0;
    virtual bool set_proxy(bool enabled) = 0;

    virtual exec::TimeSource& time_source() = 0;

    // Every bus KV cache, streamed by source. Default: nothing (a model
    // with no bus attached).
    virtual void kv_snapshot(KvSink& sink) { (void)sink; }

    // Queue a serialized JSON text frame to every connected WS client.
    // `kind` classifies the frame for the transport outbox: whole-state
    // snapshots (status/session/program) coalesce so a newer one can
    // never be dropped in favour of a stale one, incremental frames
    // (kv) are the ones evicted under pressure.
    virtual void ws_broadcast(std::string&& json,
                              WsKind kind = WsKind::OTHER) = 0;
};

}  // namespace esp32tap::api
