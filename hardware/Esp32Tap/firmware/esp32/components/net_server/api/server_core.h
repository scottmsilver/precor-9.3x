/*
 * server_core.h — the single-writer application core of the native
 * server tier: python/server.py's shared business logic (state
 * management, endpoint validation/clamping, program/session/run
 * coordination) ported to run entirely on the interval executor task.
 *
 * All REST/WS handler work is marshalled onto the executor thread by
 * the device transport (queue + response semaphore); host tests call
 * straight in. Hardware access goes exclusively through ServerModel.
 */

#pragma once

#include <array>
#include <string>
#include <string_view>
#include <vector>

#include "rapidjson/document.h"

#include "history_store.h"
#include "profile_store.h"
#include "program_state.h"
#include "run_store.h"
#include "server_model.h"
#include "workout_session.h"
#include "workout_store.h"

namespace esp32tap::api {

struct ApiResponse {
    int status = 200;
    std::string body;
};

// Explicit caps on client-supplied free text that gets PERSISTED.
// python has no caps (a Pi has GBs); here every stored byte is resident
// RAM on a no-PSRAM part for the life of the boot, so an unbounded
// prompt/name is a remote heap-exhaustion -> reboot -> total data loss
// path. Rejected with the same {"error": ...} 422 shape python's
// pydantic validation produces for the empty-name case.
inline constexpr size_t MAX_NAME_CHARS = 120;
inline constexpr size_t MAX_PROMPT_CHARS = 512;

// Ceiling on a list endpoint's response body. The stores are byte-
// capped (JsonArrayStore::max_bytes) but the list handlers ALSO deep-
// copy every entry into a fresh document and enrich it, so the response
// is a second, larger transient allocation — bound it explicitly rather
// than trusting the store cap to imply it.
inline constexpr size_t MAX_LIST_RESPONSE_BYTES = 24 * 1024;

class ServerCore : public exec::ProgramEvents {
public:
    ServerCore(ServerModel& model, exec::TimeSource& ts,
               storage::HistoryStore& history,
               storage::WorkoutStore& workouts, storage::RunStore& runs,
               storage::ProfileStore& profiles)
        : model_(model),
          ts_(ts),
          history_(history),
          workouts_(workouts),
          runs_(runs),
          profiles_(profiles),
          prog_(ts),
          sess_(ts, prog_) {
        prog_.set_events(this);
    }

    exec::ProgramState& prog() { return prog_; }
    exec::WorkoutSession& session() { return sess_; }

    // --- 1 Hz executor duties -------------------------------------------
    // Program scheduler tick (ProgramState::tick).
    void program_tick() { prog_.tick(); }
    // Session metrics tick + WS session frame + 30 s run checkpoints
    // (server.py _session_tick_loop parity).
    void session_tick();

    // WS "kv" frame producer (server.py on_message re-enqueues every
    // {"type":"kv",...} event — the app's only continuous WS traffic;
    // TreadmillViewModel.handleKVUpdate feeds the Debug KV log and
    // merges motor keys into status.motor). Device delta: python
    // forwards one frame per decoded KV byte-stream event, which on a
    // no-PSRAM part would be a fan-out storm — this emits at most
    // MAX_KV_FRAMES_PER_TICK frames per 1 Hz tick, only for keys whose
    // value CHANGED. Returns the number of frames broadcast.
    int kv_tick();
    static constexpr int MAX_KV_FRAMES_PER_TICK = 6;

    // Auto-proxy bounce (server.py _handle_auto_proxy): pause program +
    // session, inject the exact python encouragement strings, then a
    // status frame (server.py broadcasts build_status() on the same
    // C++ status event).
    void handle_auto_proxy(bool console_takeover);

    // Network dead-man (PLAN failure-matrix "WSS drop" row for the
    // standalone HTTP surface): every WS client has been gone for the
    // grace period while a program drives the belt -> pause (belt to 0).
    // Called by the executor; no-op unless a program is actively running.
    void handle_client_loss();

    // On-connect ordered WS frames: status, session-if-active,
    // program-if-loaded (server.py /ws parity).
    void connect_frames(std::vector<std::string>& out);

    // --- endpoint bodies (called by the router) -------------------------
    ApiResponse get_banner();
    ApiResponse get_status();
    ApiResponse post_speed(const rapidjson::Document& body);
    ApiResponse post_incline(const rapidjson::Document& body);
    ApiResponse post_emulate(const rapidjson::Document& body);
    ApiResponse post_proxy(const rapidjson::Document& body);
    ApiResponse get_program();
    ApiResponse post_program_start();
    ApiResponse post_program_quick_start(const rapidjson::Document& body);
    ApiResponse post_program_stop();
    ApiResponse post_reset();
    ApiResponse post_program_pause();
    ApiResponse post_program_skip();
    ApiResponse post_program_prev();
    ApiResponse post_program_extend(const rapidjson::Document& body);
    ApiResponse post_program_adjust_duration(const rapidjson::Document& body);
    ApiResponse get_history();
    ApiResponse post_history_load(std::string_view id);
    ApiResponse post_history_resume(std::string_view id);
    ApiResponse get_workouts();
    ApiResponse post_workouts(const rapidjson::Document& body);
    ApiResponse put_workout(std::string_view id,
                            const rapidjson::Document& body);
    ApiResponse delete_workout(std::string_view id);
    ApiResponse post_workout_load(std::string_view id);

    // Profiles (server.py Profile Management API parity; guest is
    // synthesized, avatars unsupported on-device).
    ApiResponse get_profiles();
    ApiResponse post_profiles(const rapidjson::Document& body);
    ApiResponse put_profile(std::string_view id,
                            const rapidjson::Document& body);
    ApiResponse delete_profile(std::string_view id);
    ApiResponse post_profile_select(const rapidjson::Document& body);
    ApiResponse get_profile_active();
    ApiResponse post_profile_guest();
    ApiResponse post_profile_guest_convert();
    ApiResponse get_avatar(std::string_view id);
    ApiResponse post_avatar(std::string_view id);
    ApiResponse delete_avatar(std::string_view id);
    ApiResponse get_user();
    ApiResponse put_user(const rapidjson::Document& body);

    // --- JSON builders (public for host golden tests) -------------------
    std::string status_json();
    std::string program_json();  // {"type":"program",...} incl. pending
                                 // encouragement (does NOT drain)
    std::string session_json();

    // Boot recovery (call once before serving): in_progress runs ->
    // "disconnect".
    int boot_recover_runs();

    // ProgramEvents (ProgramState callbacks)
    void on_change(double speed, double incline) override;
    void on_update() override;

private:
    void broadcast_status();
    void broadcast_session();
    // server.py _apply_* ports
    ApiResponse apply_speed(double mph);
    ApiResponse apply_incline(double pct);
    void apply_stop();
    void save_run_record(std::string_view reason);
    void run_checkpoint();
    void build_run_record(std::string_view reason, rapidjson::Document& out);
    void add_history(const rapidjson::Value& program, std::string_view prompt);
    // Enrichment helpers
    std::string last_run_text(const rapidjson::Value* run);
    // Profile helpers (server.py _active_profile_id/_user_weight_kg)
    std::string active_profile_id() const;
    void profile_json(std::string_view id, rapidjson::Value& out,
                      rapidjson::Document::AllocatorType& a);
    double user_weight_kg();

    ServerModel& model_;
    exec::TimeSource& ts_;
    storage::HistoryStore& history_;
    storage::WorkoutStore& workouts_;
    storage::RunStore& runs_;
    storage::ProfileStore& profiles_;
    exec::ProgramState prog_;
    exec::WorkoutSession sess_;

    std::string active_run_id_;
    int run_save_counter_ = 0;
    // Change detector for the "kv" WS frames, keyed by (source, key)
    // across all three bus sources. Fixed slots, zero allocation.
    static constexpr int MAX_KV_TRACKED = 3 * StatusSnapshot::MAX_KV;
    struct KvSeen {
        std::array<char, 8> source{};
        std::array<char, 8> key{};
        std::array<char, 16> val{};
    };
    std::array<KvSeen, MAX_KV_TRACKED> kv_seen_{};
    int kv_seen_count_ = 0;
    bool kv_changed(std::string_view source, std::string_view key,
                    std::string_view val) const;
    void kv_mark_seen(std::string_view source, std::string_view key,
                      std::string_view val);
    int paused_speed_tenths_ = 0;  // python state["_paused_speed"]
    bool guest_mode_ = false;      // python _guest_mode (RAM, not persisted)
    int guest_weight_lbs_ = storage::DEFAULT_WEIGHT_LBS;
    int guest_vest_lbs_ = 0;
};

// Router: (method, path, body) -> response. Pure dispatch + input
// validation (1 KB body cap, malformed JSON -> 400); runs on the
// executor thread via the device transport.
ApiResponse handle_request(ServerCore& core, std::string_view method,
                           std::string_view path, std::string_view body);

}  // namespace esp32tap::api
