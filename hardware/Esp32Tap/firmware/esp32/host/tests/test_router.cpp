/*
 * test_router.cpp — API contract tests over the pure router +
 * ServerCore with a fake ServerModel: transliterated from
 * python/tests/test_server_integration.py plus golden assertions for
 * every JSON key the Kotlin models mark deserialization-mandatory
 * (report: StatusMessage 6 keys, ProgramMessage 7 keys, SessionMessage
 * keys, Interval 4-tuple).
 */

#define DOCTEST_CONFIG_IMPLEMENT_WITH_MAIN
#define DOCTEST_CONFIG_NO_EXCEPTIONS_BUT_WITH_ALL_ASSERTS
#include <doctest.h>

#include <algorithm>
#include <string>
#include <vector>

#include "rapidjson/document.h"

#include "fakes/fake_server_env.h"

using namespace esp32tap;
using esp32tap::test::TestServer;

namespace {

rapidjson::Document parse(const std::string& body) {
    rapidjson::Document d;
    d.Parse(body.c_str());
    REQUIRE_FALSE(d.HasParseError());
    return d;
}

void check_status_mandatory_keys(const rapidjson::Document& d) {
    // Kotlin StatusMessage: no defaults for these 6 — missing key ==
    // deserialization crash in the app.
    for (const char* key : {"proxy", "emulate", "emu_speed", "emu_speed_mph",
                            "emu_incline", "treadmill_connected"}) {
        INFO("missing status key: " << key);
        REQUIRE(d.HasMember(key));
    }
    // Always-present extras (nullable in Kotlin but sent by python)
    for (const char* key : {"type", "speed", "incline", "motor", "heart_rate",
                            "hrm_connected", "hrm_device"}) {
        INFO("missing status key: " << key);
        REQUIRE(d.HasMember(key));
    }
    CHECK(std::string(d["type"].GetString()) == "status");
}

void check_program_mandatory_keys(const rapidjson::Document& d) {
    for (const char* key :
         {"type", "program", "running", "paused", "completed",
          "current_interval", "interval_elapsed", "total_elapsed",
          "total_duration"}) {
        INFO("missing program key: " << key);
        REQUIRE(d.HasMember(key));
    }
    CHECK(std::string(d["type"].GetString()) == "program");
    if (d["program"].IsObject()) {
        REQUIRE(d["program"].HasMember("intervals"));
        for (const auto& iv : d["program"]["intervals"].GetArray()) {
            // Kotlin Interval: all 4 keys mandatory
            for (const char* key : {"name", "duration", "speed", "incline"}) {
                INFO("missing interval key: " << key);
                REQUIRE(iv.HasMember(key));
            }
        }
    }
}

void check_session_mandatory_keys(const rapidjson::Document& d) {
    for (const char* key : {"type", "active", "elapsed", "distance",
                            "vert_feet", "wall_started_at", "calories",
                            "end_reason"}) {
        INFO("missing session key: " << key);
        REQUIRE(d.HasMember(key));
    }
    CHECK(std::string(d["type"].GetString()) == "session");
}

}  // namespace

TEST_CASE("GET / returns the exact JSON banner") {
    TestServer s;
    auto r = s.req("GET", "/");
    CHECK(r.status == 200);
    auto d = parse(r.body);
    CHECK(std::string(d["service"].GetString()) == "precor-treadmill");
    CHECK(std::string(d["api"].GetString()) == "/api");
    CHECK(std::string(d["ws"].GetString()) == "/ws");
}

TEST_CASE("GET /api/status: mandatory keys, null speed, KV fallback") {
    TestServer s;
    auto r = s.req("GET", "/api/status");
    CHECK(r.status == 200);
    auto d = parse(r.body);
    check_status_mandatory_keys(d);
    CHECK(d["speed"].IsNull());  // bus unknown, no KV
    CHECK(d["incline"].IsNull());
    CHECK(d["heart_rate"].GetInt() == 0);
    CHECK_FALSE(d["hrm_connected"].GetBool());
    CHECK(std::string(d["hrm_device"].GetString()).empty());
    CHECK(d["proxy"].GetBool());
    CHECK_FALSE(d["emulate"].GetBool());

    // KV fallback: hmph hex 78 = 120 -> 1.2 mph; inc hex A = 10 -> 5.0%
    s.model.motor_kv = {{"hmph", "78"}, {"inc", "A"}};
    d = parse(s.req("GET", "/api/status").body);
    CHECK(d["speed"].GetDouble() == doctest::Approx(1.2));
    CHECK(d["incline"].GetDouble() == doctest::Approx(5.0));
    CHECK(std::string(d["motor"]["hmph"].GetString()) == "78");

    // bus_speed -1 means unknown; >= 0 wins over KV
    s.model.bus_speed_tenths = 25;
    s.model.bus_incline_half = 3;
    d = parse(s.req("GET", "/api/status").body);
    CHECK(d["speed"].GetDouble() == doctest::Approx(2.5));
    CHECK(d["incline"].GetDouble() == doctest::Approx(1.5));
}

TEST_CASE("POST /api/speed clamps to 120 tenths and auto-creates manual") {
    TestServer s;
    auto d = parse(s.req("POST", "/api/speed", "{\"value\": 99}").body);
    check_status_mandatory_keys(d);
    CHECK(d["emu_speed"].GetInt() == 120);
    CHECK(d["emu_speed_mph"].GetDouble() == doctest::Approx(12.0));
    CHECK(d["emulate"].GetBool());
    CHECK_FALSE(d["proxy"].GetBool());
    // hw got the raw mph (client clamps at the controller)
    CHECK(s.model.hw_speeds.front() == doctest::Approx(99.0));

    // Auto-created manual program running
    auto p = parse(s.req("GET", "/api/program").body);
    check_program_mandatory_keys(p);
    CHECK(p["running"].GetBool());
    REQUIRE(p["program"].IsObject());
    CHECK(std::string(p["program"]["name"].GetString()) == "60-Min Manual");
    CHECK(p["program"]["manual"].GetBool());
    CHECK(p["total_duration"].GetInt() == 3600);
}

TEST_CASE("POST /api/speed 0 ends the session with user_stop") {
    TestServer s;
    s.req("POST", "/api/speed", "{\"value\": 3.0}");
    s.time.advance_s(10);
    s.core.program_tick();
    s.core.session_tick();

    s.model.frames.clear();
    auto d = parse(s.req("POST", "/api/speed", "{\"value\": 0}").body);
    check_status_mandatory_keys(d);
    auto p = parse(s.req("GET", "/api/program").body);
    CHECK_FALSE(p["running"].GetBool());
    // A session frame with end_reason user_stop was broadcast
    bool saw_end = false;
    for (const auto& f : s.model.frames) {
        rapidjson::Document fd;
        fd.Parse(f.c_str());
        if (!fd.HasParseError() && fd.HasMember("type") &&
            std::string(fd["type"].GetString()) == "session" &&
            fd["end_reason"].IsString() &&
            std::string(fd["end_reason"].GetString()) == "user_stop") {
            saw_end = true;
            check_session_mandatory_keys(fd);
        }
    }
    CHECK(saw_end);
}

TEST_CASE("POST /api/incline snaps to 0.5 before the hw call") {
    TestServer s;
    // 5 -> 5.0 (emu_incline half = 10)
    auto d = parse(s.req("POST", "/api/incline", "{\"value\": 5}").body);
    CHECK(d["emu_incline"].GetDouble() == doctest::Approx(5.0));
    CHECK(s.model.hw_inclines.back() == doctest::Approx(5.0));
    CHECK(s.model.emu_incline_half == 10);
    // 5.3 snaps to 5.5 (hw sees the SNAPPED value)
    d = parse(s.req("POST", "/api/incline", "{\"value\": 5.3}").body);
    CHECK(d["emu_incline"].GetDouble() == doctest::Approx(5.5));
    CHECK(s.model.hw_inclines.back() == doctest::Approx(5.5));
    CHECK(s.model.emu_incline_half == 11);
    // 0.5 stays 0.5 -> half 1
    d = parse(s.req("POST", "/api/incline", "{\"value\": 0.5}").body);
    CHECK(s.model.emu_incline_half == 1);
    // clamp at 15
    d = parse(s.req("POST", "/api/incline", "{\"value\": 40}").body);
    CHECK(d["emu_incline"].GetDouble() == doctest::Approx(15.0));
    CHECK(d["emulate"].GetBool());
}

TEST_CASE("speed/incline while manual program runs split intervals") {
    TestServer s;
    s.req("POST", "/api/speed", "{\"value\": 3.0}");
    s.time.advance_s(30);
    s.core.program_tick();
    auto d = parse(s.req("POST", "/api/incline", "{\"value\": 5.0}").body);
    check_status_mandatory_keys(d);
    auto p = parse(s.req("GET", "/api/program").body);
    REQUIRE(p["program"]["intervals"].Size() == 2);
    CHECK(std::string(
              p["program"]["intervals"][1]["name"].GetString()) == "Seg 2");
    CHECK(p["program"]["intervals"][1]["incline"].GetDouble() ==
          doctest::Approx(5.0));
    CHECK(p["current_interval"].GetInt() == 1);
}

TEST_CASE("503 with python error body when hardware unreachable") {
    TestServer s;
    s.model.treadmill_connected = false;
    auto r = s.req("POST", "/api/speed", "{\"value\": 3.0}");
    CHECK(r.status == 503);
    auto d = parse(r.body);
    CHECK(std::string(d["error"].GetString()) == "treadmill_io disconnected");
    r = s.req("POST", "/api/incline", "{\"value\": 3.0}");
    CHECK(r.status == 503);
}

TEST_CASE("emulate/proxy toggles") {
    TestServer s;
    auto d =
        parse(s.req("POST", "/api/emulate", "{\"enabled\": true}").body);
    check_status_mandatory_keys(d);
    CHECK(d["emulate"].GetBool());
    CHECK_FALSE(d["proxy"].GetBool());
    d = parse(s.req("POST", "/api/proxy", "{\"enabled\": true}").body);
    CHECK(d["proxy"].GetBool());
    CHECK_FALSE(d["emulate"].GetBool());
    // disable-only semantics
    d = parse(s.req("POST", "/api/proxy", "{\"enabled\": false}").body);
    CHECK_FALSE(d["proxy"].GetBool());
    CHECK_FALSE(d["emulate"].GetBool());
}

TEST_CASE("program start without program; quick-start; pause; extend") {
    TestServer s;
    auto r = s.req("POST", "/api/program/start");
    auto d = parse(r.body);
    CHECK_FALSE(d["ok"].GetBool());
    CHECK(std::string(d["error"].GetString()) == "No program loaded");

    // quick-start defaults
    d = parse(s.req("POST", "/api/program/quick-start", "{}").body);
    CHECK(d["ok"].GetBool());
    check_program_mandatory_keys(d);
    CHECK(d["running"].GetBool());
    CHECK(d["total_duration"].GetInt() == 3600);
    // quick-start range validation (pydantic parity: reject)
    auto bad =
        s.req("POST", "/api/program/quick-start", "{\"speed\": 20}");
    CHECK(bad.status == 422);

    // pause stashes and zeroes speed
    size_t n = s.model.hw_speeds.size();
    d = parse(s.req("POST", "/api/program/pause").body);
    CHECK(d["paused"].GetBool());
    REQUIRE(s.model.hw_speeds.size() > n);
    CHECK(s.model.hw_speeds.back() == doctest::Approx(0.0));
    // resume re-applies interval speed via on_change
    d = parse(s.req("POST", "/api/program/pause").body);
    CHECK_FALSE(d["paused"].GetBool());
    CHECK(s.model.hw_speeds.back() == doctest::Approx(3.0));

    // extend floors at 10 s
    d = parse(s.req("POST", "/api/program/extend",
                    "{\"seconds\": -3600}")
                  .body);
    CHECK(d["program"]["intervals"][0]["duration"].GetInt() == 10);
    // out-of-range 422
    CHECK(s.req("POST", "/api/program/extend", "{\"seconds\": 9999}")
              .status == 422);
    // adjust-duration is manual-only (quick-start IS manual, so ok)
    d = parse(s.req("POST", "/api/program/adjust-duration",
                    "{\"delta_seconds\": 60}")
                  .body);
    CHECK(d["program"]["intervals"][0]["duration"].GetInt() == 70);

    // stop: program stops, zero motion commanded
    d = parse(s.req("POST", "/api/program/stop").body);
    check_program_mandatory_keys(d);
    CHECK_FALSE(d["running"].GetBool());
    CHECK(s.model.hw_speeds.back() == doctest::Approx(0.0));
    CHECK(s.model.emu_speed_tenths == 0);

    // extend with no running program
    d = parse(
        s.req("POST", "/api/program/extend", "{\"seconds\": 30}").body);
    CHECK_FALSE(d["ok"].GetBool());
    CHECK(std::string(d["error"].GetString()) == "No program running");

    // reset
    d = parse(s.req("POST", "/api/reset").body);
    CHECK(d["ok"].GetBool());
    d = parse(s.req("GET", "/api/program").body);
    CHECK(d["program"].IsNull());
}

TEST_CASE("skip / prev recompute elapsed") {
    TestServer s;
    // two-interval generated program via workout save + load
    const char* prog_json =
        "{\"program\": {\"name\": \"Two\", \"intervals\": ["
        "{\"name\":\"A\",\"duration\":60,\"speed\":2.0,\"incline\":0},"
        "{\"name\":\"B\",\"duration\":120,\"speed\":5.0,\"incline\":1.0}"
        "]}}";
    auto d = parse(s.req("POST", "/api/workouts", prog_json).body);
    REQUIRE(d["ok"].GetBool());
    std::string wid = d["workout"]["id"].GetString();
    d = parse(s.req("POST", "/api/workouts/" + std::string(wid) + "/load")
                  .body);
    REQUIRE(d["ok"].GetBool());
    d = parse(s.req("POST", "/api/program/start").body);
    CHECK(d["running"].GetBool());
    CHECK(s.model.hw_speeds.back() == doctest::Approx(2.0));

    d = parse(s.req("POST", "/api/program/skip").body);
    CHECK(d["current_interval"].GetInt() == 1);
    CHECK(d["total_elapsed"].GetInt() == 60);
    CHECK(s.model.hw_speeds.back() == doctest::Approx(5.0));

    d = parse(s.req("POST", "/api/program/prev").body);
    CHECK(d["current_interval"].GetInt() == 0);
    CHECK(d["total_elapsed"].GetInt() == 0);
    d = parse(s.req("POST", "/api/program/prev").body);
    CHECK(d["current_interval"].GetInt() == 0);  // floor at 0
}

TEST_CASE("workouts: save/list/rename/delete/load + history linkage") {
    TestServer s;
    // invalid program -> error mentioning "intervals"
    auto d = parse(
        s.req("POST", "/api/workouts", "{\"program\": {\"name\":\"x\"}}")
            .body);
    CHECK_FALSE(d["ok"].GetBool());
    CHECK(std::string(d["error"].GetString()).find("intervals") !=
          std::string::npos);

    // invalid source -> 422
    CHECK(s.req("POST", "/api/workouts",
                "{\"program\": {\"name\":\"x\",\"intervals\":[]},"
                "\"source\":\"weird\"}")
              .status == 422);

    // neither history_id nor program
    d = parse(s.req("POST", "/api/workouts", "{}").body);
    CHECK_FALSE(d["ok"].GetBool());

    // valid save
    const char* prog_json =
        "{\"program\": {\"name\": \"W1\", \"intervals\": ["
        "{\"name\":\"A\",\"duration\":600,\"speed\":3.0,\"incline\":0.0}"
        "]}, \"source\": \"generated\", \"prompt\": \"hills\"}";
    d = parse(s.req("POST", "/api/workouts", prog_json).body);
    REQUIRE(d["ok"].GetBool());
    auto& w = d["workout"];
    for (const char* key :
         {"id", "name", "program", "source", "prompt", "times_used",
          "last_used", "created_at", "total_duration"}) {
        INFO("missing workout key: " << key);
        REQUIRE(w.HasMember(key));
    }
    CHECK(w["last_used"].IsNull());
    std::string wid = w["id"].GetString();

    // list: usage_text "Never used", enrichment keys present
    d = parse(s.req("GET", "/api/workouts").body);
    REQUIRE(d.IsArray());
    REQUIRE(d.Size() == 1);
    CHECK(std::string(d[0]["usage_text"].GetString()) == "Never used");
    CHECK(d[0]["last_run"].IsNull());
    CHECK(std::string(d[0]["last_run_text"].GetString()).empty());

    // rename: empty -> 422; valid updates program.name too
    CHECK(s.req("PUT", "/api/workouts/" + wid, "{\"name\": \"\"}").status ==
          422);
    d = parse(
        s.req("PUT", "/api/workouts/" + wid, "{\"name\": \"Better\"}").body);
    REQUIRE(d["ok"].GetBool());
    CHECK(std::string(d["workout"]["name"].GetString()) == "Better");
    CHECK(std::string(d["workout"]["program"]["name"].GetString()) ==
          "Better");
    d = parse(s.req("PUT", "/api/workouts/zzz", "{\"name\": \"n\"}").body);
    CHECK_FALSE(d["ok"].GetBool());
    CHECK(std::string(d["error"].GetString()) == "Not found");

    // load bumps usage and re-adds to history
    d = parse(s.req("POST", "/api/workouts/" + wid + "/load").body);
    REQUIRE(d["ok"].GetBool());
    d = parse(s.req("GET", "/api/workouts").body);
    CHECK(d[0]["times_used"].GetInt() == 1);
    CHECK(d[0]["last_used"].IsString());

    auto h = parse(s.req("GET", "/api/programs/history").body);
    REQUIRE(h.IsArray());
    REQUIRE(h.Size() == 1);
    CHECK(std::string(h[0]["name"].GetString()) == "Better");
    // saved linkage by fingerprint
    CHECK(h[0]["saved"].GetBool());
    CHECK(std::string(h[0]["saved_workout_id"].GetString()) == wid);
    for (const char* key :
         {"id", "name", "program", "source", "prompt", "total_duration",
          "completed", "last_interval", "last_elapsed", "created_at",
          "saved", "saved_workout_id", "last_run", "last_run_text"}) {
        INFO("missing history key: " << key);
        REQUIRE(h[0].HasMember(key));
    }

    // history load / resume
    std::string hid = h[0]["id"].GetString();
    d = parse(
        s.req("POST", "/api/programs/history/" + hid + "/load").body);
    CHECK(d["ok"].GetBool());
    REQUIRE(d.HasMember("program"));
    d = parse(s.req("POST", "/api/programs/history/zzz/load").body);
    CHECK_FALSE(d["ok"].GetBool());
    CHECK(std::string(d["error"].GetString()) == "Not found");

    // mark completed -> resume rejected with "completed" in error
    s.history.update_position("Better", 0, 600, true);
    d = parse(
        s.req("POST", "/api/programs/history/" + hid + "/resume").body);
    CHECK_FALSE(d["ok"].GetBool());
    CHECK(std::string(d["error"].GetString()).find("completed") !=
          std::string::npos);

    // delete
    d = parse(s.req("DELETE", "/api/workouts/" + wid).body);
    CHECK(d["ok"].GetBool());
    d = parse(s.req("DELETE", "/api/workouts/" + wid).body);
    CHECK_FALSE(d["ok"].GetBool());
}

TEST_CASE("history resume restarts from saved position") {
    TestServer s;
    const char* prog_json =
        "{\"program\": {\"name\": \"R\", \"intervals\": ["
        "{\"name\":\"A\",\"duration\":60,\"speed\":2.0,\"incline\":0},"
        "{\"name\":\"B\",\"duration\":120,\"speed\":5.0,\"incline\":1.0}"
        "]}}";
    auto d = parse(s.req("POST", "/api/workouts", prog_json).body);
    std::string wid = d["workout"]["id"].GetString();
    s.req("POST", "/api/workouts/" + wid + "/load");
    auto h = parse(s.req("GET", "/api/programs/history").body);
    std::string hid = h[0]["id"].GetString();
    s.history.update_position("R", 1, 90, false);

    d = parse(
        s.req("POST", "/api/programs/history/" + hid + "/resume").body);
    REQUIRE(d["ok"].GetBool());
    CHECK(d["running"].GetBool());
    CHECK(d["current_interval"].GetInt() == 1);
    CHECK(d["total_elapsed"].GetInt() == 90);
    CHECK(d["interval_elapsed"].GetInt() == 30);  // 90 - 60 cumulative
    CHECK(s.model.hw_speeds.back() == doctest::Approx(5.0));
}

TEST_CASE("run records: 30s checkpoint + finalize + boot recovery") {
    TestServer s;
    s.req("POST", "/api/speed", "{\"value\": 3.0}");
    // 35 ticks: record created at the 30-tick boundary (elapsed >= 5)
    for (int i = 0; i < 35; i++) {
        s.time.advance_s(1);
        s.core.program_tick();
        s.core.session_tick();
    }
    CHECK(s.runs.size() == 1);
    {
        const auto& rec = s.runs.doc()[0];
        CHECK(std::string(rec["end_reason"].GetString()) == "in_progress");
        CHECK(rec["ended_at"].IsNull());
        CHECK(rec["is_manual"].GetBool());
        CHECK(std::string(rec["program_name"].GetString()) ==
              "60-Min Manual");
        for (const char* key :
             {"id", "started_at", "ended_at", "elapsed", "distance",
              "vert_feet", "calories", "end_reason", "program_name",
              "program_fingerprint", "program_completed", "is_manual"}) {
            INFO("missing run key: " << key);
            REQUIRE(rec.HasMember(key));
        }
    }

    // stop finalizes with user_stop
    s.req("POST", "/api/program/stop");
    CHECK(s.runs.size() == 1);
    CHECK(std::string(s.runs.doc()[0]["end_reason"].GetString()) ==
          "user_stop");
    CHECK(s.runs.doc()[0]["ended_at"].IsString());

    // boot recovery converts stray in_progress -> disconnect
    s.req("POST", "/api/speed", "{\"value\": 3.0}");
    for (int i = 0; i < 31; i++) {
        s.time.advance_s(1);
        s.core.program_tick();
        s.core.session_tick();
    }
    CHECK(std::string(s.runs.doc()[0]["end_reason"].GetString()) ==
          "in_progress");
    CHECK(s.core.boot_recover_runs() == 1);
    CHECK(std::string(s.runs.doc()[0]["end_reason"].GetString()) ==
          "disconnect");
}

TEST_CASE("program completion ends session with program_complete") {
    TestServer s;
    auto d = parse(s.req("POST", "/api/program/quick-start",
                         "{\"duration_minutes\": 1}")
                       .body);
    REQUIRE(d["ok"].GetBool());
    for (int i = 0; i < 62; i++) {
        s.time.advance_s(1);
        s.core.program_tick();
        s.core.session_tick();
    }
    auto p = parse(s.req("GET", "/api/program").body);
    CHECK(p["completed"].GetBool());
    CHECK_FALSE(p["running"].GetBool());
    CHECK(s.model.emu_speed_tenths == 0);
    // run record finalized program_complete
    REQUIRE(s.runs.size() == 1);
    CHECK(std::string(s.runs.doc()[0]["end_reason"].GetString()) ==
          "program_complete");
    CHECK(s.runs.doc()[0]["program_completed"].GetBool());
    // history marked completed
    auto h = parse(s.req("GET", "/api/programs/history").body);
    // quick-start manual programs aren't added to history — history empty
    CHECK(h.Size() == 0);
}

TEST_CASE("ws connect frames: status, session-if-active, program-if-loaded") {
    TestServer s;
    std::vector<std::string> frames;
    s.core.connect_frames(frames);
    REQUIRE(frames.size() == 1);  // only status
    {
        auto d = parse(frames[0]);
        check_status_mandatory_keys(d);
    }
    s.req("POST", "/api/speed", "{\"value\": 3.0}");
    frames.clear();
    s.core.connect_frames(frames);
    REQUIRE(frames.size() == 3);
    auto d0 = parse(frames[0]);
    check_status_mandatory_keys(d0);
    auto d1 = parse(frames[1]);
    check_session_mandatory_keys(d1);
    auto d2 = parse(frames[2]);
    check_program_mandatory_keys(d2);
}

TEST_CASE("auto-proxy bounce pauses and injects exact python strings") {
    TestServer s;
    s.req("POST", "/api/speed", "{\"value\": 3.0}");
    s.model.frames.clear();
    s.core.handle_auto_proxy(true);
    auto p = parse(s.req("GET", "/api/program").body);
    CHECK(p["paused"].GetBool());
    bool saw = false;
    for (const auto& f : s.model.frames) {
        if (f.find("Console took over — paused") != std::string::npos) {
            saw = true;
        }
    }
    CHECK(saw);
    // server.py parity: the same event also broadcasts a status frame
    // (the app must see emulate/speed flip immediately on takeover).
    REQUIRE_FALSE(s.model.frames.empty());
    {
        auto last = parse(s.model.frames.back());
        CHECK(std::string(last["type"].GetString()) == "status");
    }

    // heartbeat-lost variant
    TestServer s2;
    s2.req("POST", "/api/speed", "{\"value\": 3.0}");
    s2.model.frames.clear();
    s2.core.handle_auto_proxy(false);
    bool saw2 = false;
    for (const auto& f : s2.model.frames) {
        if (f.find("Belt stopped — heartbeat lost") != std::string::npos) {
            saw2 = true;
        }
    }
    CHECK(saw2);
}

TEST_CASE("stubs: gpx 501, out-of-scope 503, unknown 404, bad json 400") {
    TestServer s;
    auto r = s.req("POST", "/api/gpx/upload", "");
    CHECK(r.status == 501);
    auto d = parse(r.body);
    CHECK_FALSE(d["ok"].GetBool());

    for (const char* path :
         {"/api/chat", "/api/program/generate", "/api/background/advise",
          "/api/hrm", "/api/hrm/scan", "/api/tool", "/api/config"}) {
        auto rr = s.req("POST", path, "{}");
        INFO("path: " << path);
        CHECK(rr.status == 503);
    }
    CHECK(s.req("GET", "/api/nonsense").status == 404);
    CHECK(s.req("POST", "/api/speed", "{oops").status == 400);
    CHECK(s.req("POST", "/api/speed", std::string(9000, 'x')).status == 400);
    // missing value -> 422
    CHECK(s.req("POST", "/api/speed", "{}").status == 422);
}

TEST_CASE("profiles: create/select/active/guest/update/delete guards") {
    TestServer s;

    // Fresh device: active falls back to the synthesized guest profile
    // (python _active_profile_id parity) so the app reaches the Lobby.
    auto d = parse(s.req("GET", "/api/profile/active").body);
    REQUIRE(d["profile"].IsObject());
    CHECK(std::string(d["profile"]["name"].GetString()) == "Guest");
    CHECK_FALSE(d["guest_mode"].GetBool());
    CHECK(parse(s.req("GET", "/api/profiles").body).Size() == 0);

    // Create -> {"ok":true,"profile":{...}} with the Kotlin Profile keys
    auto c = parse(
        s.req("POST", "/api/profiles",
              "{\"name\":\"scott\",\"color\":\"#123456\",\"weight_lbs\":200}")
            .body);
    CHECK(c["ok"].GetBool());
    for (const char* key : {"id", "name", "color", "initials", "has_avatar",
                            "weight_lbs", "vest_lbs", "created_at",
                            "updated_at"}) {
        INFO("missing profile key: " << key);
        REQUIRE(c["profile"].HasMember(key));
    }
    CHECK(std::string(c["profile"]["initials"].GetString()) == "S");
    std::string pid = c["profile"]["id"].GetString();

    // Validation parity (pydantic 422s)
    CHECK(s.req("POST", "/api/profiles", "{}").status == 422);
    CHECK(s.req("POST", "/api/profiles",
                "{\"name\":\"x\",\"weight_lbs\":900}")
              .status == 422);

    // Select -> active reflects it; ws status broadcast happened
    s.model.frames.clear();
    auto sel = parse(
        s.req("POST", "/api/profile/select", "{\"id\":\"" + pid + "\"}").body);
    CHECK(sel["ok"].GetBool());
    CHECK_FALSE(s.model.frames.empty());
    d = parse(s.req("GET", "/api/profile/active").body);
    CHECK(std::string(d["profile"]["id"].GetString()) == pid);
    CHECK_FALSE(d["guest_mode"].GetBool());
    CHECK(s.req("POST", "/api/profile/select", "{\"id\":\"nope\"}").status ==
          404);

    // /api/user proxies the active profile
    auto u = parse(s.req("GET", "/api/user").body);
    CHECK(std::string(u["id"].GetString()) == pid);
    CHECK(u["weight_lbs"].GetInt() == 200);
    u = parse(s.req("PUT", "/api/user", "{\"weight_lbs\":180}").body);
    CHECK(u["weight_lbs"].GetInt() == 180);

    // Update recomputes initials; guards
    auto up = parse(
        s.req("PUT", "/api/profiles/" + pid, "{\"name\":\"bob\"}").body);
    CHECK(std::string(up["profile"]["initials"].GetString()) == "B");
    CHECK(s.req("PUT", "/api/profiles/nope", "{\"name\":\"x\"}").status ==
          404);

    // Delete guards: guest 400, active 409, unknown 404
    CHECK(s.req("DELETE",
                std::string("/api/profiles/") +
                    std::string(storage::GUEST_PROFILE_ID))
              .status == 400);
    CHECK(s.req("DELETE", "/api/profiles/" + pid).status == 409);
    CHECK(s.req("DELETE", "/api/profiles/nope").status == 404);

    // Guest mode: enter, active reflects it, convert flips back
    auto g = parse(s.req("POST", "/api/profile/guest").body);
    CHECK(g["ok"].GetBool());
    CHECK(g["guest_mode"].GetBool());
    d = parse(s.req("GET", "/api/profile/active").body);
    CHECK(d["guest_mode"].GetBool());
    CHECK(std::string(d["profile"]["name"].GetString()) == "Guest");
    auto conv = parse(s.req("POST", "/api/profile/guest/convert").body);
    CHECK(conv["ok"].GetBool());
    CHECK(std::string(conv["profile_id"].GetString()) == pid);
    d = parse(s.req("GET", "/api/profile/active").body);
    CHECK_FALSE(d["guest_mode"].GetBool());

    // During an active session: select + guest are 409 (python parity)
    s.req("POST", "/api/speed", "{\"value\": 3.0}");
    CHECK(s.req("POST", "/api/profile/select", "{\"id\":\"" + pid + "\"}")
              .status == 409);
    CHECK(s.req("POST", "/api/profile/guest").status == 409);

    // Avatars: none stored / unsupported (documented device delta)
    CHECK(s.req("GET", "/api/profiles/" + pid + "/avatar").status == 404);
    CHECK(s.req("POST", "/api/profiles/" + pid + "/avatar").status == 501);
    CHECK(s.req("DELETE", "/api/profiles/" + pid + "/avatar").status == 200);
}

TEST_CASE("guest mode refuses workout saves (python _save_workout parity)") {
    TestServer s;
    s.req("POST", "/api/profile/guest");
    auto r = parse(
        s.req("POST", "/api/workouts",
              "{\"program\":{\"name\":\"P\",\"intervals\":[{\"name\":\"A\","
              "\"duration\":60,\"speed\":3.0,\"incline\":0.0}]}}")
            .body);
    CHECK_FALSE(r["ok"].GetBool());
    CHECK(std::string(r["error"].GetString()) ==
          "Create a profile to save workouts");
}

TEST_CASE("motion authority refused -> 503, never a silent 200") {
    TestServer s;
    s.model.refuse_motion = true;
    auto r = s.req("POST", "/api/speed", "{\"value\": 3.0}");
    CHECK(r.status == 503);
    auto d = parse(r.body);
    CHECK(std::string(d["error"].GetString()) == "treadmill_io disconnected");
    CHECK(s.req("POST", "/api/incline", "{\"value\": 2.0}").status == 503);
    // Zero-speed (the STOP path) always lands: the device escalates a
    // refused zero command to emergency_stop and reports success.
    CHECK(s.req("POST", "/api/speed", "{\"value\": 0}").status == 200);
}

TEST_CASE("client loss dead-man pauses a running program (belt to 0)") {
    TestServer s;
    s.req("POST", "/api/speed", "{\"value\": 3.0}");
    REQUIRE(parse(s.req("GET", "/api/program").body)["running"].GetBool());
    s.model.frames.clear();
    s.model.hw_speeds.clear();
    s.core.handle_client_loss();
    auto p = parse(s.req("GET", "/api/program").body);
    CHECK(p["running"].GetBool());
    CHECK(p["paused"].GetBool());
    REQUIRE_FALSE(s.model.hw_speeds.empty());
    CHECK(s.model.hw_speeds.back() == doctest::Approx(0.0));
    bool saw_msg = false;
    for (const auto& f : s.model.frames) {
        if (f.find("Connection lost — paused") != std::string::npos) {
            saw_msg = true;
        }
    }
    CHECK(saw_msg);
    // Trailing status frame so the app's speed display flips
    CHECK(std::string(parse(s.model.frames.back())["type"].GetString()) ==
          "status");
    // Idempotent: already paused -> no-op
    size_t n = s.model.frames.size();
    s.core.handle_client_loss();
    CHECK(s.model.frames.size() == n);
}

TEST_CASE("GPX upload reaches its 501 with a REAL (non-JSON) body") {
    // Regression: the router JSON-pre-parsed EVERY body and 400'd on a
    // parse error before dispatch, so a genuine multipart upload could
    // never reach the intended 501 — only an empty or JSON body did.
    TestServer s;
    const std::string multipart =
        "------WebKitFormBoundaryABC\r\n"
        "Content-Disposition: form-data; name=\"file\"; filename=\"r.gpx\"\r\n"
        "Content-Type: application/gpx+xml\r\n\r\n"
        "<?xml version=\"1.0\"?><gpx><trk><trkseg/></trk></gpx>\r\n"
        "------WebKitFormBoundaryABC--\r\n";
    auto r = s.req("POST", "/api/gpx/upload", multipart);
    CHECK(r.status == 501);
    CHECK_FALSE(parse(r.body)["ok"].GetBool());
    // Empty and JSON bodies still behave.
    CHECK(s.req("POST", "/api/gpx/upload", "").status == 501);
    CHECK(s.req("POST", "/api/gpx/upload", "{}").status == 501);
    // Everything else still rejects malformed JSON.
    CHECK(s.req("POST", "/api/speed", "{oops").status == 400);
}

TEST_CASE("avatar upload reaches its stub with a binary body") {
    TestServer s;
    auto created = parse(
        s.req("POST", "/api/profiles",
              "{\"name\":\"Ann\",\"color\":\"#112233\",\"weight_lbs\":150}")
            .body);
    std::string pid = created["profile"]["id"].GetString();
    std::string png("\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR", 20);
    CHECK(s.req("POST", "/api/profiles/" + pid + "/avatar", png).status == 501);
}

TEST_CASE("persisted free text is length-capped (prompt / name)") {
    // python has no caps; here every stored byte is resident RAM for
    // the life of the boot on a no-PSRAM part, so an unbounded prompt
    // or name is a remote heap-exhaustion -> reboot -> total data loss
    // path bounded only by the 8 KB body cap.
    TestServer s;
    const std::string program =
        "\"program\":{\"name\":\"P\",\"intervals\":[{\"name\":\"A\","
        "\"duration\":600,\"speed\":3.0,\"incline\":0.0}]}";

    std::string long_prompt(esp32tap::api::MAX_PROMPT_CHARS + 1, 'p');
    auto over = s.req("POST", "/api/workouts",
                      "{" + program + ",\"prompt\":\"" + long_prompt + "\"}");
    CHECK(over.status == 422);
    CHECK(parse(over.body).HasMember("error"));

    std::string ok_prompt(esp32tap::api::MAX_PROMPT_CHARS, 'p');
    auto ok = s.req("POST", "/api/workouts",
                    "{" + program + ",\"prompt\":\"" + ok_prompt + "\"}");
    REQUIRE(ok.status == 200);
    auto okd = parse(ok.body);
    REQUIRE(okd["ok"].GetBool());
    std::string wid = okd["workout"]["id"].GetString();

    std::string long_name(esp32tap::api::MAX_NAME_CHARS + 1, 'n');
    auto bad = s.req("PUT", "/api/workouts/" + wid,
                     "{\"name\":\"" + long_name + "\"}");
    CHECK(bad.status == 422);
    CHECK(parse(bad.body).HasMember("error"));
    // The over-long rename did NOT land.
    auto listed = parse(s.req("GET", "/api/workouts").body);
    REQUIRE(listed.Size() == 1);
    CHECK(std::string(listed[0]["name"].GetString()) != long_name);
    // Empty is still rejected, exactly-at-cap is accepted.
    CHECK(s.req("PUT", "/api/workouts/" + wid, "{\"name\":\"\"}").status == 422);
    std::string at_cap(esp32tap::api::MAX_NAME_CHARS, 'n');
    CHECK(s.req("PUT", "/api/workouts/" + wid, "{\"name\":\"" + at_cap + "\"}")
              .status == 200);
}

TEST_CASE("auto-proxy broadcasts status even with no active session") {
    // server.py gates only the pause/encouragement work on session
    // activity but ALWAYS ends _apply() with a status broadcast, so a
    // session-less EMULATING->PROXY transition still reaches the app.
    TestServer s;
    s.model.emulate = true;
    s.model.proxy = false;
    s.model.frames.clear();
    s.core.handle_auto_proxy(true);
    REQUIRE_FALSE(s.model.frames.empty());
    CHECK(std::string(parse(s.model.frames.back())["type"].GetString()) ==
          "status");
    // No session -> no program bounce frame was injected.
    for (const auto& f : s.model.frames) {
        CHECK(f.find("Console took over") == std::string::npos);
    }
}

TEST_CASE("kv_tick emits app-shaped kv frames for CHANGED motor keys only") {
    // server.py re-enqueues every {"type":"kv",...} event: it is the
    // app's only continuous WS traffic and feeds both the Debug KV log
    // and the incremental status.motor merge.
    TestServer s;
    s.model.motor_kv = {{"hmph", "78"}, {"inc", "A"}};
    s.model.frames.clear();
    CHECK(s.core.kv_tick() == 2);
    REQUIRE(s.model.frames.size() == 2);
    auto f0 = parse(s.model.frames[0]);
    CHECK(std::string(f0["type"].GetString()) == "kv");
    CHECK(std::string(f0["source"].GetString()) == "motor");
    CHECK(std::string(f0["key"].GetString()) == "hmph");
    CHECK(std::string(f0["value"].GetString()) == "78");
    CHECK(f0["ts"].IsNumber());

    // Unchanged -> silent (no fan-out storm on a no-PSRAM part).
    s.model.frames.clear();
    CHECK(s.core.kv_tick() == 0);
    CHECK(s.model.frames.empty());

    // Only the changed key is re-sent.
    s.model.motor_kv = {{"hmph", "96"}, {"inc", "A"}};
    CHECK(s.core.kv_tick() == 1);
    REQUIRE(s.model.frames.size() == 1);
    CHECK(std::string(parse(s.model.frames[0])["key"].GetString()) == "hmph");

    // Per-tick fan-out is bounded...
    s.model.motor_kv.clear();
    for (int i = 0; i < 12; i++) {
        s.model.motor_kv.emplace_back("k" + std::to_string(i),
                                      std::to_string(i));
    }
    s.model.frames.clear();
    const int cap = esp32tap::api::ServerCore::MAX_KV_FRAMES_PER_TICK;
    CHECK(s.core.kv_tick() == cap);

    // ...and the keys the cap skipped are NOT marked as seen: they go
    // out on later ticks instead of being lost until they next change.
    // (The 14-key motor cycle would otherwise permanently lose its
    // slow-moving keys, e.g. ver/part/type, from the Debug screen.)
    s.model.frames.clear();
    CHECK(s.core.kv_tick() == 12 - cap);
    std::vector<std::string> keys;
    for (const auto& f : s.model.frames) {
        keys.push_back(parse(f)["key"].GetString());
    }
    REQUIRE(keys.size() == static_cast<size_t>(12 - cap));
    CHECK(keys.front() == "k" + std::to_string(cap));
    // Now everything is seen: quiet again.
    CHECK(s.core.kv_tick() == 0);
}

TEST_CASE("list responses are byte-bounded") {
    TestServer s;
    // 20 saved workouts each carrying a 64-interval program would be a
    // ~100 KB response on a part with ~200 KB of free heap.
    std::string ivs;
    for (int i = 0; i < 64; i++) {
        if (i > 0) ivs += ",";
        ivs +=
            "{\"name\":\"Interval with a fairly long display name\","
            "\"duration\":300,\"speed\":5.5,\"incline\":2.5}";
    }
    for (int i = 0; i < 20; i++) {
        std::string body = "{\"program\":{\"name\":\"Big " +
                           std::to_string(i) + "\",\"intervals\":[" + ivs +
                           "]}}";
        s.req("POST", "/api/workouts", body);
    }
    auto hist = s.req("GET", "/api/programs/history");
    CHECK(hist.body.size() <= esp32tap::api::MAX_LIST_RESPONSE_BYTES);
    auto workouts = s.req("GET", "/api/workouts");
    CHECK(workouts.body.size() <= esp32tap::api::MAX_LIST_RESPONSE_BYTES);
    CHECK(parse(workouts.body).Size() >= 1);
}

TEST_CASE("kv frames cover EVERY bus source, not just the motor tap") {
    // server.py forwards {"type":"kv"} for source in motor / console /
    // emulate, and the app's Debug KV log columns on exactly that
    // field (TreadmillViewModel.handleKVUpdate). A motor-only stream
    // makes the whole OUTBOUND side of the bus invisible — including
    // the frames the device itself synthesizes while emulating.
    TestServer s;
    s.model.motor_kv = {{"hmph", "78"}};
    s.model.console_kv = {{"hmph", "C8"}};
    s.model.emulate_kv = {{"hmph", "78"}, {"inc", "A"}};
    s.model.frames.clear();
    CHECK(s.core.kv_tick() == 4);

    std::vector<std::string> sources;
    for (const auto& f : s.model.frames) {
        sources.push_back(parse(f)["source"].GetString());
    }
    CHECK(std::count(sources.begin(), sources.end(), "motor") == 1);
    CHECK(std::count(sources.begin(), sources.end(), "console") == 1);
    CHECK(std::count(sources.begin(), sources.end(), "emulate") == 2);

    // The change detector is keyed by (source, key): the SAME key with
    // the SAME value on a different source is a distinct stream and
    // must not be swallowed as "unchanged".
    s.model.frames.clear();
    CHECK(s.core.kv_tick() == 0);
    s.model.console_kv = {{"hmph", "78"}};  // now equals the motor value
    CHECK(s.core.kv_tick() == 1);
    REQUIRE(s.model.frames.size() == 1);
    CHECK(std::string(parse(s.model.frames[0])["source"].GetString()) ==
          "console");
}

TEST_CASE("broadcast frames are classified for the transport outbox") {
    // The transport coalesces whole-state snapshots so a newer one can
    // never be dropped in favour of a stale one; that only works if the
    // core labels them (ws_outbox.h).
    TestServer s;
    using esp32tap::api::WsKind;
    s.model.frames.clear();
    s.model.frame_kinds.clear();
    s.req("POST", "/api/speed", "{\"value\":3.0}");
    REQUIRE(!s.model.frame_kinds.empty());
    bool saw_status = false, saw_program = false;
    for (size_t i = 0; i < s.model.frames.size(); i++) {
        std::string type = parse(s.model.frames[i])["type"].GetString();
        if (type == "status") {
            saw_status = true;
            CHECK(s.model.frame_kinds[i] == WsKind::STATUS);
        } else if (type == "program") {
            saw_program = true;
            CHECK(s.model.frame_kinds[i] == WsKind::PROGRAM);
        } else if (type == "session") {
            CHECK(s.model.frame_kinds[i] == WsKind::SESSION);
        }
    }
    CHECK(saw_status);
    CHECK(saw_program);

    s.model.frames.clear();
    s.model.frame_kinds.clear();
    s.model.motor_kv = {{"hmph", "78"}};
    CHECK(s.core.kv_tick() == 1);
    REQUIRE(s.model.frame_kinds.size() == 1);
    CHECK(s.model.frame_kinds[0] == WsKind::KV);
}

TEST_CASE("a GPX body far over the JSON cap still answers 501") {
    // The transport drains an upload body without storing it and
    // dispatches with an EMPTY body, but the router must also not apply
    // its own JSON body cap to these paths — a real GPX route is tens
    // of KB and would otherwise become "body too large".
    TestServer s;
    std::string huge(64 * 1024, 'x');
    CHECK(s.req("POST", "/api/gpx/upload", huge).status == 501);
    // A non-upload path with the same body is still capped.
    CHECK(s.req("POST", "/api/speed", huge).status == 400);
}
