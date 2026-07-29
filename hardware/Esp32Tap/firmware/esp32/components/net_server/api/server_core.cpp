/*
 * server_core.cpp — python/server.py business-logic port. See
 * server_core.h. Behavior anchors: python/tests/test_server_integration
 * transliterations in host/tests/test_router.cpp.
 */

#include "server_core.h"

#include <charconv>
#include <cmath>
#include <cstdint>
#include <optional>
#include <utility>

#include "rapidjson/stringbuffer.h"
#include "rapidjson/writer.h"

#include "fingerprint.h"
#include "json_fmt.h"
#include "program_json.h"

namespace esp32tap::api {

namespace {

using exec::fmt_dur;
using exec::fmt_int;
using exec::fmt_py_float;
using exec::relative_time;
using exec::round_to;

std::string dump(const rapidjson::Value& v) {
    rapidjson::StringBuffer sb;
    rapidjson::Writer<rapidjson::StringBuffer> w(sb);
    v.Accept(w);
    return std::string(sb.GetString(), sb.GetSize());
}

rapidjson::Value str_val(std::string_view s,
                         rapidjson::Document::AllocatorType& a) {
    return rapidjson::Value(s.data(), static_cast<rapidjson::SizeType>(s.size()),
                            a);
}

// Simple JSON error/ok bodies.
ApiResponse err_response(int status, std::string_view msg) {
    rapidjson::Document d(rapidjson::kObjectType);
    d.AddMember("error", str_val(msg, d.GetAllocator()), d.GetAllocator());
    return {status, dump(d)};
}

ApiResponse ok_false(std::string_view msg) {
    rapidjson::Document d(rapidjson::kObjectType);
    d.AddMember("ok", false, d.GetAllocator());
    d.AddMember("error", str_val(msg, d.GetAllocator()), d.GetAllocator());
    return {200, dump(d)};
}

// Incline snap: clamp [0, 15] then round(x*2)/2.
double snap_incline(double pct) {
    double clamped = pct < 0.0 ? 0.0 : (pct > 15.0 ? 15.0 : pct);
    double doubled = clamped * 2.0;
    int64_t r = static_cast<int64_t>(doubled >= 0 ? doubled + 0.5 : doubled - 0.5);
    return static_cast<double>(r) / 2.0;
}

std::optional<double> get_number(const rapidjson::Value& v, const char* key) {
    if (!v.IsObject()) return std::nullopt;
    auto m = v.FindMember(key);
    if (m == v.MemberEnd() || !m->value.IsNumber()) return std::nullopt;
    double d = m->value.GetDouble();
    // Boundary validation: wire JSON cannot encode NaN/Inf (rapidjson
    // rejects 1e400 as NumberTooBig), but stored flash data flows
    // through here too — reject non-finite before any double->int
    // conversion downstream (UB otherwise).
    if (!std::isfinite(d)) return std::nullopt;
    return d;
}

// Clamp in the double domain BEFORE converting: an out-of-range
// double->int cast is UB, and several of these values come from stored
// flash JSON or the wire.
int to_int_clamped(double d, int lo, int hi) {
    if (!(d >= static_cast<double>(lo))) return lo;  // NaN -> lo
    if (d > static_cast<double>(hi)) return hi;
    return static_cast<int>(d);
}

std::optional<bool> get_bool(const rapidjson::Value& v, const char* key) {
    if (!v.IsObject()) return std::nullopt;
    auto m = v.FindMember(key);
    if (m == v.MemberEnd() || !m->value.IsBool()) return std::nullopt;
    return m->value.GetBool();
}

std::optional<std::string> get_string(const rapidjson::Value& v,
                                      const char* key) {
    if (!v.IsObject()) return std::nullopt;
    auto m = v.FindMember(key);
    if (m == v.MemberEnd() || !m->value.IsString()) return std::nullopt;
    return std::string(m->value.GetString(), m->value.GetStringLength());
}

// Device delta from python (which stores the full normalized interval
// text): fingerprints are stored/matched as a fixed 16-hex FNV-1a64
// token so a run record stays O(100 B) in RAM/flash instead of up to
// ~1 KB for a 64-interval program. Matching is internal-only (history
// <-> workout <-> run linkage); the app treats the value as opaque.
std::string fp_key(std::string_view fp) {
    if (fp.empty()) return "";
    uint64_t h = 1469598103934665603ULL;
    for (char ch : fp) {
        h ^= static_cast<unsigned char>(ch);
        h *= 1099511628211ULL;
    }
    std::string out(16, '0');
    for (int i = 15; i >= 0; i--) {
        out.at(static_cast<size_t>(i)) = "0123456789abcdef"[h & 0xF];
        h >>= 4;
    }
    return out;
}

// Fingerprint from a stored program JSON value. Numbers normalize the
// same way as the executor-side Program-struct fingerprint (speed and
// incline as python-float strings, duration as int) so run records and
// history entries always match on-device (see PLAN.md note).
std::string fp_from_json(const rapidjson::Value& program) {
    if (!program.IsObject()) return "";
    auto ivs = program.FindMember("intervals");
    if (ivs == program.MemberEnd() || !ivs->value.IsArray()) return "";
    std::string out;
    bool first = true;
    for (const auto& iv : ivs->value.GetArray()) {
        double speed = 0.0, incline = 0.0;
        int duration = 0;
        if (iv.IsObject()) {
            if (auto s = get_number(iv, "speed")) speed = *s;
            if (auto i = get_number(iv, "incline")) incline = *i;
            if (auto d = get_number(iv, "duration"))
                duration = to_int_clamped(*d, 0, 86400);
        }
        if (!first) out += "|";
        first = false;
        out += fmt_py_float(speed) + "," + fmt_py_float(incline) + "," +
               fmt_int(duration);
    }
    return fp_key(out);
}

// Executor-side fingerprint of the live Program (same normalization).
std::string fp_from_program(const exec::Program& p) {
    return fp_key(exec::program_fingerprint(p));
}

// Canonicalize a client/stored program JSON into the executor shape
// (name / manual / intervals x {name,duration,speed,incline}) before it
// is persisted: bounds RAM (MAX_INTERVALS cap, fixed key set) and drops
// unknown keys an attacker could pad a store file with. false == not a
// loadable program (caller keeps the original — Postel).
bool canonicalize_program(const rapidjson::Value& in,
                          rapidjson::Document& out) {
    exec::Program p{};
    if (!exec::program_from_json(in, p)) return false;
    exec::program_to_json(p, out, out.GetAllocator());
    return true;
}

// hex KV decode ("78" -> 120), python int(x, 16) parity; nullopt on
// malformed.
std::optional<int> parse_hex(std::string_view s) {
    if (s.empty() || s.size() > 8) return std::nullopt;
    int value = 0;
    auto [p, ec] = std::from_chars(s.data(), s.data() + s.size(), value, 16);
    if (ec != std::errc{} || p != s.data() + s.size()) return std::nullopt;
    return value;
}

}  // namespace

// --- JSON builders ------------------------------------------------------

std::string ServerCore::status_json() {
    StatusSnapshot st = model_.status();
    rapidjson::Document d(rapidjson::kObjectType);
    auto& a = d.GetAllocator();
    d.AddMember("type", "status", a);
    d.AddMember("proxy", st.proxy, a);
    d.AddMember("emulate", st.emulate, a);
    d.AddMember("emu_speed", st.emu_speed_tenths, a);
    d.AddMember("emu_speed_mph", st.emu_speed_tenths / 10.0, a);
    d.AddMember("emu_incline", st.emu_incline_half / 2.0, a);

    // Live bus speed: bus_speed (tenths) if known, else KV fallback.
    rapidjson::Value speed;  // null
    if (st.bus_speed_tenths >= 0) {
        speed.SetDouble(st.bus_speed_tenths / 10.0);
    }
    rapidjson::Value incline;  // null
    if (st.bus_incline_half >= 0) {
        incline.SetDouble(st.bus_incline_half / 2.0);
    }
    rapidjson::Value motor(rapidjson::kObjectType);
    for (int i = 0; i < st.kv_count; i++) {
        const auto& kv = st.kv.at(static_cast<size_t>(i));
        std::string_view key(kv.key.data());
        std::string_view val(kv.val.data());
        if (speed.IsNull() && key == "hmph") {
            if (auto h = parse_hex(val)) speed.SetDouble(*h / 100.0);
        }
        if (incline.IsNull() && key == "inc") {
            if (auto h = parse_hex(val)) incline.SetDouble(*h / 2.0);
        }
        motor.AddMember(str_val(key, a), str_val(val, a), a);
    }
    d.AddMember("speed", speed, a);
    d.AddMember("incline", incline, a);
    d.AddMember("motor", motor, a);
    d.AddMember("treadmill_connected", st.treadmill_connected, a);
    d.AddMember("heart_rate", 0, a);
    d.AddMember("hrm_connected", false, a);
    d.AddMember("hrm_device", "", a);
    return dump(d);
}

std::string ServerCore::program_json() {
    rapidjson::Document d(rapidjson::kObjectType);
    auto& a = d.GetAllocator();
    d.AddMember("type", "program", a);
    if (prog_.has_program()) {
        rapidjson::Value pv;
        exec::program_to_json(prog_.program(), pv, a);
        d.AddMember("program", pv, a);
    } else {
        d.AddMember("program", rapidjson::Value(), a);  // null
    }
    d.AddMember("running", prog_.running(), a);
    d.AddMember("paused", prog_.paused(), a);
    d.AddMember("completed", prog_.completed(), a);
    d.AddMember("current_interval", prog_.current_interval(), a);
    d.AddMember("interval_elapsed", prog_.interval_elapsed(), a);
    d.AddMember("total_elapsed", prog_.total_elapsed(), a);
    d.AddMember("total_duration", prog_.total_duration(), a);
    if (!prog_.pending_encouragement().empty()) {
        d.AddMember("encouragement",
                    str_val(prog_.pending_encouragement(), a), a);
    }
    return dump(d);
}

std::string ServerCore::session_json() {
    rapidjson::Document d(rapidjson::kObjectType);
    auto& a = d.GetAllocator();
    d.AddMember("type", "session", a);
    d.AddMember("active", sess_.active(), a);
    d.AddMember("elapsed", sess_.elapsed(), a);
    d.AddMember("distance", sess_.distance(), a);
    d.AddMember("vert_feet", sess_.vert_feet(), a);
    d.AddMember("calories", round_to(sess_.calories(), 1), a);
    d.AddMember("wall_started_at", str_val(sess_.wall_started_at(), a), a);
    if (sess_.has_end_reason()) {
        d.AddMember("end_reason", str_val(sess_.end_reason(), a), a);
    } else {
        d.AddMember("end_reason", rapidjson::Value(), a);  // null
    }
    return dump(d);
}

// --- broadcast plumbing -------------------------------------------------

void ServerCore::broadcast_status() {
    model_.ws_broadcast(status_json(), WsKind::STATUS);
}
void ServerCore::broadcast_session() {
    model_.ws_broadcast(session_json(), WsKind::SESSION);
}

void ServerCore::connect_frames(std::vector<std::string>& out) {
    out.push_back(status_json());
    if (sess_.active()) out.push_back(session_json());
    if (prog_.has_program()) out.push_back(program_json());
}

// --- ProgramEvents ------------------------------------------------------

void ServerCore::on_change(double speed, double incline) {
    double clamped_inc = snap_incline(incline);
    model_.hw_set_speed(speed);
    model_.hw_set_incline(clamped_inc);
    broadcast_status();
}

void ServerCore::on_update() {
    model_.ws_broadcast(program_json(), WsKind::PROGRAM);
    if (prog_.completed() && !prog_.running()) {
        if (prog_.has_program()) {
            history_.update_position(prog_.program().name.view(),
                                     prog_.current_interval(),
                                     prog_.total_elapsed(), true);
        }
        model_.hw_set_speed(0);
        model_.hw_set_incline(0);
        if (sess_.active()) {
            save_run_record("program_complete");
            sess_.end("program_complete");
            broadcast_session();
        }
        broadcast_status();
    }
}

// --- run records --------------------------------------------------------

void ServerCore::build_run_record(std::string_view reason,
                                  rapidjson::Document& out) {
    out.SetObject();
    auto& a = out.GetAllocator();
    std::string id = active_run_id_.empty()
                         ? storage::JsonArrayStore::make_id(ts_.now_us())
                         : active_run_id_;
    out.AddMember("id", str_val(id, a), a);
    out.AddMember("started_at", str_val(sess_.wall_started_at(), a), a);
    if (reason != "in_progress") {
        out.AddMember("ended_at", str_val(ts_.now_iso(), a), a);
    } else {
        out.AddMember("ended_at", rapidjson::Value(), a);  // null
    }
    out.AddMember("elapsed", round_to(sess_.elapsed(), 1), a);
    out.AddMember("distance", round_to(sess_.distance(), 3), a);
    out.AddMember("vert_feet", round_to(sess_.vert_feet(), 1), a);
    out.AddMember("calories", round_to(sess_.calories(), 1), a);
    out.AddMember("end_reason", str_val(reason, a), a);
    if (prog_.has_program()) {
        out.AddMember("program_name", str_val(prog_.program().name.view(), a),
                      a);
        out.AddMember("program_fingerprint",
                      str_val(fp_from_program(prog_.program()), a), a);
    } else {
        out.AddMember("program_name", rapidjson::Value(), a);
        out.AddMember("program_fingerprint", rapidjson::Value(), a);
    }
    out.AddMember("program_completed", prog_.completed(), a);
    out.AddMember("is_manual", prog_.is_manual(), a);
}

void ServerCore::save_run_record(std::string_view reason) {
    if (!sess_.active() || sess_.elapsed() < 5) {
        active_run_id_.clear();
        return;
    }
    if (!active_run_id_.empty()) {
        runs_.finalize(active_run_id_, ts_.now_iso(),
                       round_to(sess_.elapsed(), 1),
                       round_to(sess_.distance(), 3),
                       round_to(sess_.vert_feet(), 1),
                       round_to(sess_.calories(), 1), reason,
                       prog_.completed());
    } else {
        rapidjson::Document rec;
        build_run_record(reason, rec);
        rapidjson::Value copy(rec, runs_.doc().GetAllocator());
        runs_.insert(std::move(copy));
    }
    active_run_id_.clear();
}

void ServerCore::run_checkpoint() {
    if (active_run_id_.empty() && sess_.elapsed() >= 5) {
        rapidjson::Document rec;
        build_run_record("in_progress", rec);
        active_run_id_ =
            std::string(rec["id"].GetString(), rec["id"].GetStringLength());
        auto& a = runs_.doc().GetAllocator();
        rapidjson::Value copy(rec, a);
        runs_.insert(std::move(copy));
    } else if (!active_run_id_.empty()) {
        runs_.update_metrics(active_run_id_, round_to(sess_.elapsed(), 1),
                             round_to(sess_.distance(), 3),
                             round_to(sess_.vert_feet(), 1),
                             round_to(sess_.calories(), 1));
    }
}

int ServerCore::boot_recover_runs() { return runs_.boot_recover(ts_.now_iso()); }

void ServerCore::session_tick() {
    if (!sess_.active()) return;
    StatusSnapshot st = model_.status();
    sess_.tick(st.emu_speed_tenths / 10.0, st.emu_incline_half / 2.0,
               user_weight_kg());
    broadcast_session();
    run_save_counter_++;
    if (run_save_counter_ >= 30) {  // server.py _RUN_SAVE_INTERVAL
        run_save_counter_ = 0;
        run_checkpoint();
    }
}

void ServerCore::handle_auto_proxy(bool console_takeover) {
    // server.py gates only the pause/encouragement work on an active
    // session (_handle_auto_proxy), but ALWAYS ends _apply() with a
    // status broadcast — so a session-less EMULATING->PROXY transition
    // still reaches the app. Keep the broadcast outside the guard.
    if (sess_.active()) {
        if (prog_.running() && !prog_.paused()) {
            prog_.pause_silently();
        }
        sess_.pause();
        prog_.set_pending_encouragement(console_takeover
                                            ? "Console took over — paused"
                                            : "Belt stopped — heartbeat lost");
        model_.ws_broadcast(program_json(), WsKind::PROGRAM);
        prog_.drain_encouragement();
    }
    // server.py parity: the same C++ status event that signaled the
    // emulate->proxy edge also broadcasts build_status(), so the app's
    // emulate/speed state flips immediately on console takeover.
    broadcast_status();
}

int ServerCore::kv_tick() {
    // Change detector + per-tick cap over EVERY bus source. server.py
    // re-enqueues each {"type":"kv",...} event from treadmill_io with
    // its `source` ("motor", "console", "emulate"), and the app's Debug
    // KV log columns on exactly that field — a motor-only stream makes
    // the whole outbound side of the bus invisible, including the
    // frames the device itself synthesizes while emulating.
    struct Collector : public KvSink {
        ServerCore* self;
        int sent = 0;
        void kv(std::string_view source, std::string_view key,
                std::string_view val) override {
            if (key.empty()) return;
            // Cap the per-tick fan-out, but do NOT mark the keys we
            // skipped as seen: they stay "changed" and go out on a
            // later tick. (Marking them seen after truncating would
            // drop those updates until the value happened to change
            // again — the 14-key cycle would lose its slow keys.)
            if (sent >= ServerCore::MAX_KV_FRAMES_PER_TICK) return;
            if (!self->kv_changed(source, key, val)) return;

            rapidjson::Document d(rapidjson::kObjectType);
            auto& a = d.GetAllocator();
            d.AddMember("type", "kv", a);
            d.AddMember("source", str_val(source, a), a);
            d.AddMember("key", str_val(key, a), a);
            d.AddMember("value", str_val(val, a), a);
            d.AddMember("ts", static_cast<double>(self->ts_.now_us()) / 1e6,
                        a);
            self->model_.ws_broadcast(dump(d), WsKind::KV);
            sent++;
            // Record only once the frame is out.
            self->kv_mark_seen(source, key, val);
        }
    };
    Collector c;
    c.self = this;
    model_.kv_snapshot(c);
    return c.sent;
}

// True when (source, key) has no recorded value or a different one.
bool ServerCore::kv_changed(std::string_view source, std::string_view key,
                            std::string_view val) const {
    for (int i = 0; i < kv_seen_count_; i++) {
        const auto& e = kv_seen_.at(static_cast<size_t>(i));
        if (std::string_view(e.source.data()) == source &&
            std::string_view(e.key.data()) == key) {
            return std::string_view(e.val.data()) != val;
        }
    }
    return true;
}

void ServerCore::kv_mark_seen(std::string_view source, std::string_view key,
                              std::string_view val) {
    for (int i = 0; i < kv_seen_count_; i++) {
        auto& e = kv_seen_.at(static_cast<size_t>(i));
        if (std::string_view(e.source.data()) == source &&
            std::string_view(e.key.data()) == key) {
            e.val.fill('\0');
            val.copy(e.val.data(), e.val.size() - 1);
            return;
        }
    }
    if (kv_seen_count_ >= MAX_KV_TRACKED) return;
    auto& e = kv_seen_.at(static_cast<size_t>(kv_seen_count_++));
    e.source.fill('\0');
    e.key.fill('\0');
    e.val.fill('\0');
    source.copy(e.source.data(), e.source.size() - 1);
    key.copy(e.key.data(), e.key.size() - 1);
    val.copy(e.val.data(), e.val.size() - 1);
}

void ServerCore::handle_client_loss() {
    if (!sess_.active() || !prog_.running() || prog_.paused()) return;
    StatusSnapshot st = model_.status();
    prog_.toggle_pause();  // -> paused (mirror of post_program_pause)
    sess_.pause();
    paused_speed_tenths_ = st.emu_speed_tenths;
    model_.hw_set_speed(0);
    prog_.set_pending_encouragement("Connection lost — paused");
    model_.ws_broadcast(program_json(), WsKind::PROGRAM);
    prog_.drain_encouragement();
    broadcast_status();
}

// --- history helpers ----------------------------------------------------

void ServerCore::add_history(const rapidjson::Value& program,
                             std::string_view prompt) {
    rapidjson::Document canon;
    if (canonicalize_program(program, canon)) {
        history_.add(canon, prompt, ts_.now_iso(), ts_.now_us());
    } else {
        history_.add(program, prompt, ts_.now_iso(), ts_.now_us());
    }
}

std::string ServerCore::last_run_text(const rapidjson::Value* run) {
    if (run == nullptr || !run->IsObject()) return "";
    std::string when;
    if (auto ended = get_string(*run, "ended_at")) {
        when = relative_time(*ended, ts_.now_iso());
    }
    double dist = 0.0;
    if (auto d = get_number(*run, "distance")) dist = *d;
    std::string dist_text =
        dist >= 0.01 ? exec::fmt_fixed(dist, 2) + " mi" : "";
    double elapsed = 0.0;
    if (auto e = get_number(*run, "elapsed")) elapsed = *e;
    std::string dur = fmt_dur(elapsed);
    std::string joined;
    for (const std::string& part : {when, dur, dist_text}) {
        if (part.empty()) continue;
        if (!joined.empty()) joined += " · ";
        joined += part;
    }
    return joined.empty() ? "" : "Last run: " + joined;
}

// --- endpoints ----------------------------------------------------------

ApiResponse ServerCore::get_banner() {
    return {200,
            "{\"service\":\"precor-treadmill\",\"api\":\"/api\",\"ws\":\"/ws\"}"};
}

ApiResponse ServerCore::get_status() { return {200, status_json()}; }

ApiResponse ServerCore::apply_speed(double mph) {
    StatusSnapshot st = model_.status();
    if (!st.treadmill_connected) {
        return err_response(503, "treadmill_io disconnected");
    }
    if (mph > 0) {
        sess_.ensure_manual(mph, st.emu_incline_half / 2.0, 60);
    } else if (mph == 0 && sess_.active()) {
        if (prog_.running()) prog_.stop();
        save_run_record("user_stop");
        sess_.end("user_stop");
        broadcast_session();
    }
    if (prog_.is_manual() && prog_.running() && mph > 0) {
        prog_.split_for_manual(mph, st.emu_incline_half / 2.0);
    }
    if (!model_.hw_set_speed(mph)) {
        // Motion authority refused (lease held elsewhere / bridge down):
        // surface it — never 200 a command that did nothing. The device
        // model escalates refused ZERO-speed commands to emergency_stop
        // itself, so the stop path cannot land here.
        return err_response(503, "treadmill_io disconnected");
    }
    broadcast_status();
    return {200, status_json()};
}

ApiResponse ServerCore::post_speed(const rapidjson::Document& body) {
    auto value = get_number(body, "value");
    if (!value) return err_response(422, "value (mph) required");
    return apply_speed(*value);
}

ApiResponse ServerCore::apply_incline(double pct) {
    StatusSnapshot st = model_.status();
    if (!st.treadmill_connected) {
        return err_response(503, "treadmill_io disconnected");
    }
    double clamped = snap_incline(pct);
    if (prog_.is_manual() && prog_.running()) {
        prog_.split_for_manual(st.emu_speed_tenths / 10.0, clamped);
    }
    if (!model_.hw_set_incline(clamped)) {
        return err_response(503, "treadmill_io disconnected");
    }
    broadcast_status();
    return {200, status_json()};
}

ApiResponse ServerCore::post_incline(const rapidjson::Document& body) {
    auto value = get_number(body, "value");
    if (!value) return err_response(422, "value (percent) required");
    return apply_incline(*value);
}

ApiResponse ServerCore::post_emulate(const rapidjson::Document& body) {
    auto enabled = get_bool(body, "enabled");
    if (!enabled) return err_response(422, "enabled required");
    if (!model_.set_emulate(*enabled)) {
        return err_response(503, "treadmill_io disconnected");
    }
    broadcast_status();
    return {200, status_json()};
}

ApiResponse ServerCore::post_proxy(const rapidjson::Document& body) {
    auto enabled = get_bool(body, "enabled");
    if (!enabled) return err_response(422, "enabled required");
    if (!model_.set_proxy(*enabled)) {
        return err_response(503, "treadmill_io disconnected");
    }
    broadcast_status();
    return {200, status_json()};
}

ApiResponse ServerCore::get_program() { return {200, program_json()}; }

ApiResponse ServerCore::post_program_start() {
    if (!prog_.has_program()) return ok_false("No program loaded");
    sess_.start_program();
    return {200, program_json()};
}

ApiResponse ServerCore::post_program_quick_start(
    const rapidjson::Document& body) {
    double speed = 3.0, incline = 0.0, dm_raw = 60.0;
    if (auto s = get_number(body, "speed")) speed = *s;
    if (auto i = get_number(body, "incline")) incline = *i;
    if (auto dm = get_number(body, "duration_minutes")) dm_raw = *dm;
    // pydantic Field(ge/le) parity: reject, don't clamp. Range checks
    // run in the double domain BEFORE any int conversion (UB guard).
    if (speed < 0.5 || speed > 12.0) {
        return err_response(422, "speed must be between 0.5 and 12.0");
    }
    if (incline < 0 || incline > 15) {
        return err_response(422, "incline must be between 0 and 15");
    }
    if (dm_raw < 1 || dm_raw > 300) {
        return err_response(422,
                            "duration_minutes must be between 1 and 300");
    }
    int duration_minutes = static_cast<int>(dm_raw);
    sess_.ensure_manual(speed, incline, duration_minutes);
    // {"ok": true, **prog.to_dict()}
    rapidjson::Document d;
    d.Parse(program_json().c_str());
    rapidjson::Document out(rapidjson::kObjectType);
    auto& a = out.GetAllocator();
    out.AddMember("ok", true, a);
    for (auto& m : d.GetObject()) {
        rapidjson::Value k(m.name, a);
        rapidjson::Value v(m.value, a);
        out.AddMember(k, v, a);
    }
    return {200, dump(out)};
}

void ServerCore::apply_stop() {
    if (prog_.running() && prog_.has_program()) {
        history_.update_position(prog_.program().name.view(),
                                 prog_.current_interval(),
                                 prog_.total_elapsed(), false);
    }
    if (prog_.running()) prog_.stop();
    if (sess_.active()) {
        save_run_record("user_stop");
        sess_.end("user_stop");
        broadcast_session();
    }
    model_.hw_set_speed(0);
    model_.hw_set_incline(0);
    broadcast_status();
}

ApiResponse ServerCore::post_program_stop() {
    apply_stop();
    return {200, program_json()};
}

ApiResponse ServerCore::post_reset() {
    sess_.reset();
    model_.hw_set_speed(0);
    model_.hw_set_incline(0);
    broadcast_session();
    broadcast_status();
    return {200, "{\"ok\":true}"};
}

ApiResponse ServerCore::post_program_pause() {
    StatusSnapshot st = model_.status();
    prog_.toggle_pause();
    if (prog_.paused()) {
        sess_.pause();
        paused_speed_tenths_ = st.emu_speed_tenths;
        model_.hw_set_speed(0);
        broadcast_status();
    } else {
        // Resume: ProgramState's toggle_pause on_change already
        // re-applied the interval targets.
        sess_.resume();
        broadcast_status();
    }
    return {200, program_json()};
}

ApiResponse ServerCore::post_program_skip() {
    prog_.skip();
    return {200, program_json()};
}

ApiResponse ServerCore::post_program_prev() {
    prog_.prev();
    return {200, program_json()};
}

ApiResponse ServerCore::post_program_extend(const rapidjson::Document& body) {
    auto seconds = get_number(body, "seconds");
    if (!seconds) return err_response(422, "seconds required");
    if (*seconds < -3600 || *seconds > 3600) {  // double-domain check first
        return err_response(422, "seconds must be between -3600 and 3600");
    }
    int sec = static_cast<int>(*seconds);
    if (!prog_.running()) return ok_false("No program running");
    prog_.extend_current(sec);
    return {200, program_json()};
}

ApiResponse ServerCore::post_program_adjust_duration(
    const rapidjson::Document& body) {
    auto delta = get_number(body, "delta_seconds");
    if (!delta) return err_response(422, "delta_seconds required");
    if (*delta < -3600 || *delta > 3600) {  // double-domain check first
        return err_response(422,
                            "delta_seconds must be between -3600 and 3600");
    }
    int sec = static_cast<int>(*delta);
    if (!prog_.is_manual() || !prog_.running()) {
        return ok_false("No manual program running");
    }
    prog_.adjust_duration(sec);
    return {200, program_json()};
}

ApiResponse ServerCore::get_history() {
    rapidjson::Document out(rapidjson::kArrayType);
    auto& a = out.GetAllocator();
    size_t used = 2;  // "[]"

    // saved-workout fingerprints
    std::vector<std::pair<std::string, std::string>> saved_fps;  // fp -> id
    for (auto& w : workouts_.doc().GetArray()) {
        if (!w.IsObject()) continue;
        auto p = w.FindMember("program");
        auto id = w.FindMember("id");
        if (p == w.MemberEnd() || id == w.MemberEnd() ||
            !id->value.IsString()) {
            continue;
        }
        saved_fps.emplace_back(
            fp_from_json(p->value),
            std::string(id->value.GetString(), id->value.GetStringLength()));
    }

    for (auto& e : history_.doc().GetArray()) {
        if (!e.IsObject()) continue;
        rapidjson::Value copy(e, a);
        std::string fp;
        auto p = e.FindMember("program");
        if (p != e.MemberEnd()) fp = fp_from_json(p->value);

        bool saved = false;
        std::string saved_id;
        for (const auto& [wfp, wid] : saved_fps) {
            if (!fp.empty() && wfp == fp) {
                saved = true;
                saved_id = wid;
                break;
            }
        }
        copy.AddMember("saved", saved, a);
        if (saved) {
            copy.AddMember("saved_workout_id", str_val(saved_id, a), a);
        } else {
            copy.AddMember("saved_workout_id", rapidjson::Value(), a);
        }
        const rapidjson::Value* run = runs_.last_run_for_fingerprint(fp);
        if (run != nullptr) {
            rapidjson::Value run_copy(*run, a);
            copy.AddMember("last_run", run_copy, a);
        } else {
            copy.AddMember("last_run", rapidjson::Value(), a);
        }
        copy.AddMember("last_run_text", str_val(last_run_text(run), a), a);
        // Response-byte bound (see MAX_LIST_RESPONSE_BYTES): entries are
        // newest-first, so truncating the tail drops the oldest.
        used += dump(copy).size() + 1;
        if (used > MAX_LIST_RESPONSE_BYTES && out.Size() > 0) break;
        out.PushBack(copy, a);
    }
    return {200, dump(out)};
}

ApiResponse ServerCore::post_history_load(std::string_view id) {
    rapidjson::Value* e = history_.find_by_id(id);
    if (e == nullptr) return ok_false("Not found");
    auto p = e->FindMember("program");
    if (p == e->MemberEnd()) return ok_false("Not found");
    exec::Program prog{};
    if (!exec::program_from_json(p->value, prog)) {
        return ok_false("Corrupt program");
    }
    prog_.load(prog);
    rapidjson::Document d(rapidjson::kObjectType);
    auto& a = d.GetAllocator();
    d.AddMember("ok", true, a);
    rapidjson::Value prog_copy(p->value, a);
    d.AddMember("program", prog_copy, a);
    return {200, dump(d)};
}

ApiResponse ServerCore::post_history_resume(std::string_view id) {
    rapidjson::Value* e = history_.find_by_id(id);
    if (e == nullptr) return ok_false("Not found");
    auto completed = get_bool(*e, "completed");
    if (completed && *completed) {
        return ok_false("Program already completed — use load to start over");
    }
    auto p = e->FindMember("program");
    if (p == e->MemberEnd()) return ok_false("Not found");
    exec::Program prog{};
    if (!exec::program_from_json(p->value, prog)) {
        return ok_false("Corrupt program");
    }
    prog_.load(prog);
    int resume_iv = 0, resume_elapsed = 0;
    if (auto v = get_number(*e, "last_interval")) {
        resume_iv = to_int_clamped(*v, 0, 1 << 20);
    }
    if (auto v = get_number(*e, "last_elapsed")) {
        resume_elapsed = to_int_clamped(*v, 0, 1 << 30);
    }
    sess_.start_program(resume_iv, resume_elapsed);
    rapidjson::Document pd;
    pd.Parse(program_json().c_str());
    rapidjson::Document out(rapidjson::kObjectType);
    auto& a = out.GetAllocator();
    out.AddMember("ok", true, a);
    for (auto& m : pd.GetObject()) {
        rapidjson::Value k(m.name, a);
        rapidjson::Value v(m.value, a);
        out.AddMember(k, v, a);
    }
    return {200, dump(out)};
}

ApiResponse ServerCore::get_workouts() {
    rapidjson::Document out(rapidjson::kArrayType);
    auto& a = out.GetAllocator();
    size_t used = 2;  // "[]"
    for (rapidjson::Value* w : workouts_.ordered()) {
        rapidjson::Value copy(*w, a);
        std::string fp;
        auto p = w->FindMember("program");
        if (p != w->MemberEnd()) fp = fp_from_json(p->value);
        const rapidjson::Value* run = runs_.last_run_for_fingerprint(fp);
        if (run != nullptr) {
            rapidjson::Value run_copy(*run, a);
            copy.AddMember("last_run", run_copy, a);
        } else {
            copy.AddMember("last_run", rapidjson::Value(), a);
        }
        std::string run_text = last_run_text(run);
        copy.AddMember("last_run_text", str_val(run_text, a), a);

        // usage_text (server.py _usage_text parity, "last_used" key)
        int times = 0;
        if (auto t = get_number(*w, "times_used")) {
            times = to_int_clamped(*t, 0, 1 << 30);
        }
        std::string usage;
        if (!run_text.empty()) {
            usage = run_text;
            if (times > 1) {
                usage += " · " + fmt_int(times) + " runs total";
            }
        } else if (times > 0) {
            std::string last;
            if (auto lu = get_string(*w, "last_used")) {
                last = relative_time(*lu, ts_.now_iso());
            }
            usage = "Used " + fmt_int(times) +
                    (times != 1 ? " times" : " time") +
                    (last.empty() ? "" : " · last " + last);
        } else {
            usage = "Never used";
        }
        copy.AddMember("usage_text", str_val(usage, a), a);
        // Response-byte bound (see MAX_LIST_RESPONSE_BYTES).
        used += dump(copy).size() + 1;
        if (used > MAX_LIST_RESPONSE_BYTES && out.Size() > 0) break;
        out.PushBack(copy, a);
    }
    return {200, dump(out)};
}

ApiResponse ServerCore::post_workouts(const rapidjson::Document& body) {
    if (!body.IsObject()) return ok_false("Provide history_id or program");
    auto history_id = get_string(body, "history_id");
    const rapidjson::Value* program = nullptr;
    std::string source = "generated";
    std::string prompt;

    if (history_id && !history_id->empty()) {
        rapidjson::Value* e = history_.find_by_id(*history_id);
        if (e == nullptr) return ok_false("History entry not found");
        auto p = e->FindMember("program");
        if (p == e->MemberEnd()) return ok_false("History entry not found");
        program = &p->value;
        if (auto pr = get_string(*e, "prompt")) prompt = *pr;
        // Source inference (server.py parity)
        if (prompt.rfind("GPX:", 0) == 0) {
            source = "gpx";
        } else {
            auto manual = p->value.FindMember("manual");
            bool is_manual =
                p->value.IsObject() && manual != p->value.MemberEnd() &&
                ((manual->value.IsBool() && manual->value.GetBool()) ||
                 (!manual->value.IsBool() && !manual->value.IsNull()));
            source = is_manual ? "manual" : "generated";
        }
    } else if (body.HasMember("program")) {
        std::string err = exec::validate_program_json(body["program"]);
        if (!err.empty()) return ok_false(err);
        program = &body["program"];
        if (auto s = get_string(body, "source")) source = *s;
        if (source != "generated" && source != "gpx" && source != "manual") {
            return err_response(422,
                                "source must be generated, gpx or manual");
        }
        if (auto pr = get_string(body, "prompt")) {
            if (pr->size() > MAX_PROMPT_CHARS) {
                return err_response(422, "prompt must have at most " +
                                             fmt_int(static_cast<int64_t>(
                                                 MAX_PROMPT_CHARS)) +
                                             " characters");
            }
            prompt = *pr;
        }
    } else {
        return ok_false("Provide history_id or program");
    }

    // server.py _save_workout parity: guest data is not persisted.
    if (guest_mode_) return ok_false("Create a profile to save workouts");

    // Canonicalize before persisting (bounds entry size; drops unknown
    // keys — see canonicalize_program).
    rapidjson::Document canon;
    if (canonicalize_program(*program, canon)) program = &canon;

    std::string id = workouts_.save_workout(*program, source, prompt,
                                            ts_.now_iso(), ts_.now_us());
    if (id.empty()) {
        // Device RAM cap (python has no cap — PLAN.md note).
        return ok_false("Workout limit reached — delete one first");
    }
    rapidjson::Value* w = workouts_.find_by_id(id);
    if (w == nullptr) return ok_false("Not found");
    rapidjson::Document d(rapidjson::kObjectType);
    auto& a = d.GetAllocator();
    d.AddMember("ok", true, a);
    rapidjson::Value copy(*w, a);
    d.AddMember("workout", copy, a);
    return {200, dump(d)};
}

ApiResponse ServerCore::put_workout(std::string_view id,
                                    const rapidjson::Document& body) {
    auto name = get_string(body, "name");
    if (!name || name->empty()) {
        return err_response(422, "name must have at least 1 character");
    }
    if (name->size() > MAX_NAME_CHARS) {
        return err_response(
            422, "name must have at most " +
                     fmt_int(static_cast<int64_t>(MAX_NAME_CHARS)) +
                     " characters");
    }
    if (workouts_.find_by_id(id) == nullptr) return ok_false("Not found");
    workouts_.rename(id, *name, ts_.now_iso());
    rapidjson::Value* w = workouts_.find_by_id(id);
    rapidjson::Document d(rapidjson::kObjectType);
    auto& a = d.GetAllocator();
    d.AddMember("ok", true, a);
    rapidjson::Value copy(*w, a);
    d.AddMember("workout", copy, a);
    return {200, dump(d)};
}

ApiResponse ServerCore::delete_workout(std::string_view id) {
    if (!workouts_.remove_by_id(id)) return ok_false("Not found");
    return {200, "{\"ok\":true}"};
}

ApiResponse ServerCore::post_workout_load(std::string_view id) {
    {
        rapidjson::Value* w = workouts_.find_by_id(id);
        if (w == nullptr) return ok_false("Not found");
        auto p = w->FindMember("program");
        if (p == w->MemberEnd()) return ok_false("Not found");
        exec::Program prog{};
        if (!exec::program_from_json(p->value, prog)) {
            return ok_false("Corrupt program");
        }
        prog_.load(prog);
    }
    // bump_usage saves — save() may compact the store document, which
    // invalidates every Value* into it. Re-find after.
    workouts_.bump_usage(id, ts_.now_iso());
    rapidjson::Value* w = workouts_.find_by_id(id);
    if (w == nullptr) return ok_false("Not found");
    auto p = w->FindMember("program");
    if (p == w->MemberEnd()) return ok_false("Not found");
    std::string prompt;
    if (auto pr = get_string(*w, "prompt")) prompt = *pr;
    rapidjson::Document d(rapidjson::kObjectType);
    auto& a = d.GetAllocator();
    d.AddMember("ok", true, a);
    rapidjson::Value prog_copy(p->value, a);
    d.AddMember("program", prog_copy, a);
    // add_history mutates the history store only; w/p (workout store)
    // stay valid, but the response was built above regardless.
    add_history(p->value, prompt);
    return {200, dump(d)};
}

// --- profiles (server.py Profile Management API port) -------------------

std::string ServerCore::active_profile_id() const {
    // server.py _active_profile_id parity: guest id in guest mode or
    // when no active profile is set.
    if (guest_mode_) return std::string(storage::GUEST_PROFILE_ID);
    const std::string& pid = profiles_.active_id();
    return pid.empty() ? std::string(storage::GUEST_PROFILE_ID) : pid;
}

void ServerCore::profile_json(std::string_view id, rapidjson::Value& out,
                              rapidjson::Document::AllocatorType& a) {
    out.SetNull();
    if (id == storage::GUEST_PROFILE_ID) {
        // Synthesized guest row (python db fixed guest profile).
        std::string now = ts_.now_iso();
        out.SetObject();
        out.AddMember("id", str_val(storage::GUEST_PROFILE_ID, a), a);
        out.AddMember("name", "Guest", a);
        out.AddMember("color", "#888888", a);
        out.AddMember("initials", "G", a);
        out.AddMember("has_avatar", false, a);
        out.AddMember("weight_lbs", guest_weight_lbs_, a);
        out.AddMember("vest_lbs", guest_vest_lbs_, a);
        out.AddMember("created_at", str_val(now, a), a);
        out.AddMember("updated_at", str_val(now, a), a);
        return;
    }
    rapidjson::Value* e = profiles_.find_by_id(id);
    if (e != nullptr) out.CopyFrom(*e, a);
}

double ServerCore::user_weight_kg() {
    // server.py _user_weight_kg parity, including the `or` quirk: a
    // 0-lb stored weight falls back to the 154 default.
    int weight = storage::DEFAULT_WEIGHT_LBS;
    int vest = 0;
    std::string pid = active_profile_id();
    if (pid == storage::GUEST_PROFILE_ID) {
        weight = guest_weight_lbs_;
        vest = guest_vest_lbs_;
    } else if (rapidjson::Value* p = profiles_.find_by_id(pid)) {
        if (auto w = get_number(*p, "weight_lbs")) {
            weight = to_int_clamped(*w, 0, 1000);  // stored data: clamp
        }
        if (auto v = get_number(*p, "vest_lbs")) {
            vest = to_int_clamped(*v, 0, 1000);
        }
    }
    if (weight == 0) weight = storage::DEFAULT_WEIGHT_LBS;
    return (weight + vest) * 0.453592;
}

ApiResponse ServerCore::get_profiles() {
    rapidjson::Document out(rapidjson::kArrayType);
    auto& a = out.GetAllocator();
    for (auto& e : profiles_.doc().GetArray()) {
        rapidjson::Value copy(e, a);
        out.PushBack(copy, a);
    }
    return {200, dump(out)};
}

ApiResponse ServerCore::post_profiles(const rapidjson::Document& body) {
    auto name = get_string(body, "name");
    if (!name || name->empty() || name->size() > 50) {
        return err_response(422, "name must be 1-50 characters");
    }
    std::string color = "#4A90D9";  // python CreateProfileRequest default
    if (auto c = get_string(body, "color")) color = *c;
    if (color.size() > 32) return err_response(422, "invalid color");
    int weight = storage::DEFAULT_WEIGHT_LBS;
    int vest = 0;
    if (auto w = get_number(body, "weight_lbs")) {
        if (*w < 0 || *w > 500) {
            return err_response(422, "weight_lbs must be between 0 and 500");
        }
        weight = static_cast<int>(*w);  // range-checked above
    }
    if (auto v = get_number(body, "vest_lbs")) {
        if (*v < 0 || *v > 100) {
            return err_response(422, "vest_lbs must be between 0 and 100");
        }
        vest = static_cast<int>(*v);  // range-checked above
    }
    std::string id = profiles_.create(*name, color, weight, vest,
                                      ts_.now_iso(), ts_.now_us());
    if (id.empty()) {
        // Device RAM cap (python is unbounded — PLAN.md note).
        return err_response(422, "profile limit reached");
    }
    rapidjson::Document d(rapidjson::kObjectType);
    auto& a = d.GetAllocator();
    d.AddMember("ok", true, a);
    rapidjson::Value p;
    profile_json(id, p, a);
    d.AddMember("profile", p, a);
    return {200, dump(d)};
}

ApiResponse ServerCore::put_profile(std::string_view id,
                                    const rapidjson::Document& body) {
    if (profiles_.find_by_id(id) == nullptr) {
        return {404, "{\"ok\":false,\"error\":\"Not found\"}"};
    }
    std::optional<std::string> name = get_string(body, "name");
    if (name && (name->empty() || name->size() > 50)) {
        return err_response(422, "name must be 1-50 characters");
    }
    std::optional<std::string> color = get_string(body, "color");
    if (color && color->size() > 32) return err_response(422, "invalid color");
    int weight = -1, vest = -1;
    if (auto w = get_number(body, "weight_lbs")) {
        if (*w < 0 || *w > 500) {
            return err_response(422, "weight_lbs must be between 0 and 500");
        }
        weight = static_cast<int>(*w);
    }
    if (auto v = get_number(body, "vest_lbs")) {
        if (*v < 0 || *v > 100) {
            return err_response(422, "vest_lbs must be between 0 and 100");
        }
        vest = static_cast<int>(*v);
    }
    profiles_.update(id, name ? &*name : nullptr, color ? &*color : nullptr,
                     weight, vest, ts_.now_iso());
    if (!guest_mode_ && profiles_.active_id() == id) {
        broadcast_status();  // _broadcast_profile_changed parity
    }
    rapidjson::Document d(rapidjson::kObjectType);
    auto& a = d.GetAllocator();
    d.AddMember("ok", true, a);
    rapidjson::Value p;
    profile_json(id, p, a);
    d.AddMember("profile", p, a);
    return {200, dump(d)};
}

ApiResponse ServerCore::delete_profile(std::string_view id) {
    if (id == storage::GUEST_PROFILE_ID) {
        return {400, "{\"ok\":false,\"error\":\"Cannot delete guest\"}"};
    }
    if (id == active_profile_id()) {
        return {409,
                "{\"ok\":false,\"error\":\"Cannot delete active profile\"}"};
    }
    if (!profiles_.remove(id)) {
        return {404, "{\"ok\":false,\"error\":\"Not found\"}"};
    }
    return {200, "{\"ok\":true}"};
}

ApiResponse ServerCore::post_profile_select(const rapidjson::Document& body) {
    auto id = get_string(body, "id");
    if (!id) return err_response(422, "id required");
    if (sess_.active()) {
        return {409,
                "{\"ok\":false,\"error\":\"Cannot switch profiles during "
                "active session\"}"};
    }
    if (*id != storage::GUEST_PROFILE_ID &&
        profiles_.find_by_id(*id) == nullptr) {
        return {404, "{\"ok\":false,\"error\":\"Profile not found\"}"};
    }
    guest_mode_ = false;
    profiles_.set_active(*id);
    broadcast_status();  // _broadcast_profile_changed parity
    rapidjson::Document d(rapidjson::kObjectType);
    auto& a = d.GetAllocator();
    d.AddMember("ok", true, a);
    rapidjson::Value p;
    profile_json(*id, p, a);
    d.AddMember("profile", p, a);
    return {200, dump(d)};
}

ApiResponse ServerCore::get_profile_active() {
    rapidjson::Document d(rapidjson::kObjectType);
    auto& a = d.GetAllocator();
    rapidjson::Value p;
    profile_json(active_profile_id(), p, a);
    d.AddMember("profile", p, a);
    d.AddMember("guest_mode", guest_mode_, a);
    return {200, dump(d)};
}

ApiResponse ServerCore::post_profile_guest() {
    if (sess_.active()) {
        return {409,
                "{\"ok\":false,\"error\":\"Cannot switch during active "
                "session\"}"};
    }
    guest_mode_ = true;
    broadcast_status();  // _broadcast_profile_changed parity
    return {200, "{\"ok\":true,\"guest_mode\":true}"};
}

ApiResponse ServerCore::post_profile_guest_convert() {
    const std::string& pid = profiles_.active_id();
    if (pid.empty() || pid == storage::GUEST_PROFILE_ID) {
        return {400,
                "{\"ok\":false,\"error\":\"No active non-guest profile\"}"};
    }
    // Device delta: stores are one shared pool (no per-profile
    // isolation), so there is no guest data to transfer — flipping the
    // mode IS the conversion (PLAN.md note).
    guest_mode_ = false;
    rapidjson::Document d(rapidjson::kObjectType);
    auto& a = d.GetAllocator();
    d.AddMember("ok", true, a);
    d.AddMember("profile_id", str_val(pid, a), a);
    return {200, dump(d)};
}

ApiResponse ServerCore::get_avatar(std::string_view id) {
    (void)id;
    // No avatar storage on-device; python's shape for "none stored".
    return {404, "{\"ok\":false,\"error\":\"No avatar\"}"};
}

ApiResponse ServerCore::post_avatar(std::string_view id) {
    if (id != storage::GUEST_PROFILE_ID &&
        profiles_.find_by_id(id) == nullptr) {
        return {404, "{\"ok\":false,\"error\":\"Not found\"}"};
    }
    return {501,
            "{\"ok\":false,\"error\":\"Avatars not supported on this "
            "device\"}"};
}

ApiResponse ServerCore::delete_avatar(std::string_view id) {
    if (id != storage::GUEST_PROFILE_ID &&
        profiles_.find_by_id(id) == nullptr) {
        return {404, "{\"ok\":false,\"error\":\"Not found\"}"};
    }
    return {200, "{\"ok\":true}"};  // nothing stored — clearing is a no-op
}

ApiResponse ServerCore::get_user() {
    // server.py /api/user: active profile as a user dict.
    std::string pid = active_profile_id();
    int weight = storage::DEFAULT_WEIGHT_LBS;
    int vest = 0;
    if (pid == storage::GUEST_PROFILE_ID) {
        weight = guest_weight_lbs_;
        vest = guest_vest_lbs_;
    } else if (rapidjson::Value* p = profiles_.find_by_id(pid)) {
        if (auto w = get_number(*p, "weight_lbs")) {
            weight = to_int_clamped(*w, 0, 1000);  // stored data: clamp
        }
        if (auto v = get_number(*p, "vest_lbs")) {
            vest = to_int_clamped(*v, 0, 1000);
        }
    } else {
        pid = "1";  // python fallback shape when the profile is gone
    }
    rapidjson::Document d(rapidjson::kObjectType);
    auto& a = d.GetAllocator();
    d.AddMember("id", str_val(pid, a), a);
    d.AddMember("weight_lbs", weight, a);
    d.AddMember("vest_lbs", vest, a);
    return {200, dump(d)};
}

ApiResponse ServerCore::put_user(const rapidjson::Document& body) {
    int weight = -1, vest = -1;
    if (auto w = get_number(body, "weight_lbs")) {
        if (*w < 0 || *w > 500) {
            return err_response(422, "weight_lbs must be between 0 and 500");
        }
        weight = static_cast<int>(*w);
    }
    if (auto v = get_number(body, "vest_lbs")) {
        if (*v < 0 || *v > 100) {
            return err_response(422, "vest_lbs must be between 0 and 100");
        }
        vest = static_cast<int>(*v);
    }
    std::string pid = active_profile_id();
    if (pid == storage::GUEST_PROFILE_ID) {
        if (weight >= 0) guest_weight_lbs_ = weight;
        if (vest >= 0) guest_vest_lbs_ = vest;
    } else if (weight >= 0 || vest >= 0) {
        profiles_.update(pid, nullptr, nullptr, weight, vest, ts_.now_iso());
    }
    return get_user();
}

}  // namespace esp32tap::api
