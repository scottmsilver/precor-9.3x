/*
 * router.cpp — (method, path, body) -> ApiResponse dispatch for the
 * native server tier. Pure (host-testable); runs on the executor
 * thread. Input hardening: 8 KB body cap (a saved-workout program body
 * can exceed the PLAN's 1 KB WSS-command cap — documented delta),
 * malformed JSON -> 400, unknown paths -> 404, out-of-scope endpoints
 * -> 503 stubs (Gemini/HRM tiers are separate workflows).
 */

#include "server_core.h"

#include <array>
#include <string_view>

#include "rapidjson/document.h"

namespace esp32tap::api {

namespace {

constexpr size_t MAX_BODY_BYTES = 8 * 1024;

// Endpoint families that exist on the Pi server but are deliberately
// not part of the standalone device (graceful 503 per Postel: the
// Kotlin models tolerate error bodies on all of these).
constexpr std::array<std::string_view, 10> kOutOfScopePrefixes = {
    "/api/chat",       "/api/voice",   "/api/tts",
    "/api/program/generate",           "/api/background",
    "/api/hrm",        "/api/tool",    "/api/config",
    "/api/log",        "/api/device-log",
};

bool starts_with(std::string_view s, std::string_view prefix) {
    return s.size() >= prefix.size() && s.substr(0, prefix.size()) == prefix;
}

// "/api/workouts/{id}" or "/api/workouts/{id}/load" segment extraction.
std::string_view path_segment(std::string_view path, std::string_view prefix) {
    std::string_view rest = path.substr(prefix.size());
    auto slash = rest.find('/');
    return slash == std::string_view::npos ? rest : rest.substr(0, slash);
}

}  // namespace

ApiResponse handle_request(ServerCore& core, std::string_view method,
                           std::string_view path, std::string_view body) {
    const bool get = method == "GET";
    const bool post = method == "POST";

    // NON-JSON ENDPOINTS FIRST — before BOTH the JSON body cap and the
    // JSON pre-parse below, either of which would make them
    // unreachable. Their bodies are multipart/binary by definition (a
    // real GPX route is tens of KB to megabytes and never parses as
    // JSON), so the app would get "body too large"/"invalid JSON body"
    // for every single upload instead of the intended 501. The
    // transport drains such bodies without storing them and dispatches
    // here with an empty body (transport_httpd.cpp is_upload_path).
    if (path == "/api/gpx/upload" && post) {
        return {501,
                "{\"ok\":false,\"error\":\"GPX upload not supported on this "
                "device\"}"};
    }
    if (starts_with(path, "/api/profiles/")) {
        std::string_view pid = path_segment(path, "/api/profiles/");
        if (!pid.empty() &&
            path == "/api/profiles/" + std::string(pid) + "/avatar") {
            if (get) return core.get_avatar(pid);
            if (post) return core.post_avatar(pid);
            if (method == "DELETE") return core.delete_avatar(pid);
        }
    }

    // JSON body cap (the transport applies the same one before we get
    // here; this keeps the router self-contained for host tests).
    if (body.size() > MAX_BODY_BYTES) {
        return {400, "{\"error\":\"body too large\"}"};
    }

    rapidjson::Document doc;
    if (!body.empty()) {
        doc.Parse(body.data(), body.size());
        if (doc.HasParseError()) {
            return {400, "{\"error\":\"invalid JSON body\"}"};
        }
    }

    if (path == "/" && get) return core.get_banner();
    if (path == "/api/status" && get) return core.get_status();
    if (path == "/api/speed" && post) return core.post_speed(doc);
    if (path == "/api/incline" && post) return core.post_incline(doc);
    if (path == "/api/emulate" && post) return core.post_emulate(doc);
    if (path == "/api/proxy" && post) return core.post_proxy(doc);
    if (path == "/api/program" && get) return core.get_program();
    if (path == "/api/program/start" && post) return core.post_program_start();
    if (path == "/api/program/quick-start" && post) {
        return core.post_program_quick_start(doc);
    }
    if (path == "/api/program/stop" && post) return core.post_program_stop();
    if (path == "/api/reset" && post) return core.post_reset();
    if (path == "/api/program/pause" && post) return core.post_program_pause();
    if (path == "/api/program/skip" && post) return core.post_program_skip();
    if (path == "/api/program/prev" && post) return core.post_program_prev();
    if (path == "/api/program/extend" && post) {
        return core.post_program_extend(doc);
    }
    if (path == "/api/program/adjust-duration" && post) {
        return core.post_program_adjust_duration(doc);
    }
    if (path == "/api/programs/history" && get) return core.get_history();
    if (starts_with(path, "/api/programs/history/") && post) {
        std::string_view id = path_segment(path, "/api/programs/history/");
        if (path == "/api/programs/history/" + std::string(id) + "/load") {
            return core.post_history_load(id);
        }
        if (path == "/api/programs/history/" + std::string(id) + "/resume") {
            return core.post_history_resume(id);
        }
    }
    if (path == "/api/workouts") {
        if (get) return core.get_workouts();
        if (post) return core.post_workouts(doc);
    }
    if (starts_with(path, "/api/workouts/")) {
        std::string_view id = path_segment(path, "/api/workouts/");
        if (!id.empty()) {
            std::string base = "/api/workouts/" + std::string(id);
            if (path == base && method == "PUT") {
                return core.put_workout(id, doc);
            }
            if (path == base && method == "DELETE") {
                return core.delete_workout(id);
            }
            if (path == base + "/load" && post) {
                return core.post_workout_load(id);
            }
        }
    }
    // Profiles (native — the app's start destination gates on
    // GET /api/profile/active; see server.py Profile Management API).
    if (path == "/api/profiles") {
        if (get) return core.get_profiles();
        if (post) return core.post_profiles(doc);
    }
    if (starts_with(path, "/api/profiles/")) {
        std::string_view id = path_segment(path, "/api/profiles/");
        if (!id.empty()) {
            std::string base = "/api/profiles/" + std::string(id);
            if (path == base && method == "PUT") {
                return core.put_profile(id, doc);
            }
            if (path == base && method == "DELETE") {
                return core.delete_profile(id);
            }
            // (the /avatar sub-route is handled before the JSON
            // pre-parse above — its bodies are never JSON)
        }
    }
    if (path == "/api/profile/select" && post) {
        return core.post_profile_select(doc);
    }
    if (path == "/api/profile/active" && get) return core.get_profile_active();
    if (path == "/api/profile/guest" && post) return core.post_profile_guest();
    if (path == "/api/profile/guest/convert" && post) {
        return core.post_profile_guest_convert();
    }
    if (path == "/api/user") {
        if (get) return core.get_user();
        if (method == "PUT") return core.put_user(doc);
    }
    for (std::string_view prefix : kOutOfScopePrefixes) {
        if (starts_with(path, prefix)) {
            return {503, "{\"error\":\"not supported on this device\"}"};
        }
    }
    return {404, "{\"error\":\"Not found\"}"};
}

}  // namespace esp32tap::api
