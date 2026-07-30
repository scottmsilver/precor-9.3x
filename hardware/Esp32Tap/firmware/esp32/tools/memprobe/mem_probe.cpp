/*
 * mem_probe.cpp — per-request memory measurement for the Esp32Tap
 * native server tier. HOST-ONLY MEASUREMENT TOOL, not firmware.
 *
 * For every in-scope endpoint, with the stores filled to their caps by
 * worst-case LEGITIMATE payloads (derived from python/server.py +
 * kotlin/), it reports
 *    cumulative bytes allocated   (what a bump ARENA must be sized for)
 *    peak simultaneously-live     (what a real free-capable allocator
 *                                  must be sized for)
 * and, for the heaviest requests, binary-searches the smallest REAL
 * ESP-IDF multi_heap region that can actually satisfy the allocation
 * sequence — i.e. measures the fragmentation premium instead of
 * guessing a fudge factor.
 *
 * Build 32-bit (ILP32) so pointer/struct sizes match xtensa-esp32s3.
 */

#include <sys/wait.h>
#include <unistd.h>

#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

#include "probe_alloc.h"

#include "fake_server_env.h"

using esp32tap::test::TestServer;

namespace {

// ---------------------------------------------------------------- payloads

// Worst-case LEGITIMATE program the app can send: MAX_INTERVALS=64
// intervals, each with a name at the NAME_CAP-1 = 47 char ceiling
// (anything longer is truncated on the device, so 47 is the largest
// value that survives a round trip), program name at the same ceiling.
std::string big_program_json(int n_intervals, int name_len,
                             const std::string& pname) {
    std::string nm(static_cast<size_t>(name_len), 'W');
    std::string s = "{\"name\":\"" + pname + "\",\"intervals\":[";
    for (int i = 0; i < n_intervals; i++) {
        if (i > 0) s += ",";
        s += "{\"name\":\"" + nm + "\",\"duration\":600,\"speed\":" +
             std::to_string(4.0 + (i % 8) * 0.1).substr(0, 4) +
             ",\"incline\":" + std::to_string(1.0 + (i % 14)).substr(0, 4) +
             "}";
    }
    s += "]}";
    return s;
}

std::string small_program_json(int idx) {
    // A realistic app-generated program: 6 intervals, human names.
    std::string s = "{\"name\":\"Tempo Builder #" + std::to_string(idx) +
                    "\",\"intervals\":[";
    const char* names[] = {"Warm up", "Build",  "Tempo",
                           "Float",   "Tempo 2", "Cool down"};
    for (int i = 0; i < 6; i++) {
        if (i > 0) s += ",";
        s += std::string("{\"name\":\"") + names[i] +
             "\",\"duration\":600,\"speed\":" + std::to_string(3 + i) +
             ".5,\"incline\":" + std::to_string(i) + ".0}";
    }
    s += "]}";
    return s;
}

// ------------------------------------------------------------------ report

struct Row {
    std::string name;
    size_t cumulative = 0;
    size_t peak = 0;
    size_t residual = 0;
    size_t nalloc = 0, nfree = 0, nrealloc = 0;
    size_t peak_blocks = 0;
    size_t max_block = 0;
    size_t resp = 0;
};

std::vector<Row> g_rows;

void emit(const char* name, const probe::Result& r, size_t resp) {
    Row row;
    row.name = name;
    row.cumulative = r.cumulative;
    row.peak = r.peak_live;
    row.residual = r.residual;
    row.nalloc = r.n_alloc;
    row.nfree = r.n_free;
    row.nrealloc = r.n_realloc;
    row.peak_blocks = r.peak_blocks;
    row.max_block = r.max_block;
    row.resp = resp;
    g_rows.push_back(row);
}

void print_rows(const char* title) {
    std::printf("\n== %s ==\n", title);
    std::printf(
        "%-42s %9s %9s %9s %7s %7s %7s %8s %8s %8s\n", "request", "cum_B",
        "peak_B", "resid_B", "allocs", "frees", "reallc", "pk_blks", "maxblk",
        "resp_B");
    for (const auto& r : g_rows) {
        std::printf("%-42s %9zu %9zu %9zd %7zu %7zu %7zu %8zu %8zu %8zu\n",
                    r.name.c_str(), r.cumulative, r.peak,
                    static_cast<ssize_t>(r.residual), r.nalloc, r.nfree,
                    r.nrealloc, r.peak_blocks, r.max_block, r.resp);
    }
    g_rows.clear();
}

// ------------------------------------------------------------------- fills

enum class Fill { FEW_BIG, MANY_SMALL };

// Fill history/workouts/runs/profiles to the point where the byte cap
// binds, using only requests the app itself makes.
void fill_stores(TestServer& ts, Fill shape) {
    // profiles: MAX_PROFILES = 8
    for (int i = 0; i < 8; i++) {
        std::string b = "{\"name\":\"Profile Name " + std::to_string(i) +
                        "\",\"color\":\"#aabbcc\",\"weight_lbs\":180,"
                        "\"vest_lbs\":10}";
        ts.req("POST", "/api/profiles", b);
    }
    // history + workouts
    const int n = (shape == Fill::FEW_BIG) ? 8 : 24;
    for (int i = 0; i < n; i++) {
        std::string prog = (shape == Fill::FEW_BIG)
                               ? big_program_json(64, 47,
                                                  "Program " + std::to_string(i))
                               : small_program_json(i);
        // history add goes through quick-start (server.py parity path)
        std::string qs = "{\"program\":" + prog + "}";
        ts.req("POST", "/api/program/quick-start", qs);
        ts.core.post_program_stop();
        std::string save = "{\"program\":" + prog + ",\"source\":\"generated\"}";
        ts.req("POST", "/api/workouts", save);
        ts.time.advance_s(120);
    }
    // runs: drive sessions so run records accumulate
    for (int i = 0; i < 40; i++) {
        ts.core.post_program_start();
        for (int k = 0; k < 3; k++) {
            ts.time.advance_s(30);
            ts.core.session_tick();
        }
        ts.core.post_program_stop();
        ts.time.advance_s(60);
    }
}

std::string first_id(TestServer& ts, const char* path) {
    auto r = ts.req("GET", path);
    auto p = r.body.find("\"id\":\"");
    if (p == std::string::npos) return "";
    p += 6;
    auto e = r.body.find('"', p);
    return r.body.substr(p, e - p);
}

// --------------------------------------------------------------- scenarios

struct Scenario {
    const char* name;
    const char* method;
    std::string path;
    std::string body;
};

std::vector<Scenario> make_scenarios(TestServer& ts) {
    std::string big = big_program_json(64, 47, "Worst Case Program Name Here");
    std::string hid = first_id(ts, "/api/programs/history");
    std::string wid = first_id(ts, "/api/workouts");
    std::string pid = first_id(ts, "/api/profiles");
    return {
        {"GET /", "GET", "/", ""},
        {"GET /api/status", "GET", "/api/status", ""},
        {"GET /api/program (64 iv loaded)", "GET", "/api/program", ""},
        {"POST /api/speed", "POST", "/api/speed", "{\"value\":5.5}"},
        {"POST /api/incline", "POST", "/api/incline", "{\"value\":7.5}"},
        {"GET /api/programs/history", "GET", "/api/programs/history", ""},
        {"GET /api/workouts", "GET", "/api/workouts", ""},
        {"GET /api/profiles", "GET", "/api/profiles", ""},
        {"GET /api/user", "GET", "/api/user", ""},
        {"GET /api/profile/active", "GET", "/api/profile/active", ""},
        {"POST /api/workouts (64-iv program)", "POST", "/api/workouts",
         "{\"program\":" + big + ",\"source\":\"generated\"}"},
        {"POST /api/program/quick-start (64 iv)", "POST",
         "/api/program/quick-start", "{\"program\":" + big + "}"},
        {"POST /api/programs/history/{id}/load", "POST",
         "/api/programs/history/" + hid + "/load", ""},
        {"POST /api/programs/history/{id}/resume", "POST",
         "/api/programs/history/" + hid + "/resume", ""},
        {"POST /api/workouts/{id}/load", "POST",
         "/api/workouts/" + wid + "/load", ""},
        {"PUT /api/workouts/{id} (rename)", "PUT", "/api/workouts/" + wid,
         "{\"name\":\"Renamed Workout With A Fairly Long Name\"}"},
        {"PUT /api/profiles/{id}", "PUT", "/api/profiles/" + pid,
         "{\"name\":\"Renamed Profile\",\"weight_lbs\":200}"},
        {"POST /api/profile/select", "POST", "/api/profile/select",
         "{\"profile_id\":\"" + pid + "\"}"},
        {"POST /api/program/stop", "POST", "/api/program/stop", ""},
        {"POST /api/gpx/upload (drained)", "POST", "/api/gpx/upload", ""},
        {"POST /api/profiles/{id}/avatar (drained)", "POST",
         "/api/profiles/" + pid + "/avatar", ""},
        {"404 unknown path", "GET", "/api/nope", ""},
        {"400 malformed JSON (8 KB)", "POST", "/api/speed",
         std::string(8 * 1024 - 1, 'x')},
    };
}

// ------------------------------------------------- multi_heap region search

alignas(16) unsigned char g_region[512 * 1024];

// Run ONE request against a multi_heap region of `bytes` in a forked
// child. Child exits 0 iff the request completed with no allocation
// failure. A too-small region reproduces the device's failure mode
// exactly (operator new -> abort).
bool region_fits(TestServer& ts, const Scenario& sc, size_t bytes) {
    std::fflush(stdout);
    pid_t pid = fork();
    if (pid == 0) {
        probe::clear_oom();
        probe::use_multi_heap(g_region, bytes);
        auto r = ts.req(sc.method, sc.path, sc.body);
        bool ok = !probe::oom_seen() && !r.body.empty();
        probe::use_plain();
        _exit(ok ? 0 : 1);
    }
    int status = 0;
    waitpid(pid, &status, 0);
    return WIFEXITED(status) && WEXITSTATUS(status) == 0;
}

size_t min_region(TestServer& ts, const Scenario& sc, size_t hi_start) {
    size_t hi = hi_start;
    while (hi < sizeof(g_region) && !region_fits(ts, sc, hi)) hi *= 2;
    if (hi >= sizeof(g_region)) return 0;
    size_t lo = 1024;
    while (lo + 64 < hi) {
        size_t mid = (lo + hi) / 2;
        if (region_fits(ts, sc, mid)) {
            hi = mid;
        } else {
            lo = mid;
        }
    }
    return hi;
}

// ------------------------------------------------------------ DOM overhead

void dom_overhead() {
    std::printf("\n== rapidjson DOM cost per serialized byte ==\n");
    std::printf("%-40s %9s %9s %9s %7s\n", "payload", "json_B", "dom_peak_B",
                "dom_resid", "ratio");
    struct P {
        const char* name;
        std::string json;
    };
    std::vector<P> ps = {
        {"64-interval program (47-char names)",
         big_program_json(64, 47, "Worst Case")},
        {"6-interval program (human names)", small_program_json(1)},
        {"status frame", "{\"type\":\"status\",\"speed\":5.5,\"incline\":2.0,"
                         "\"mode\":\"emulate\",\"connected\":true}"},
    };
    for (auto& p : ps) {
        auto w = probe::win_open();
        {
            rapidjson::Document d;
            d.Parse(p.json.c_str(), p.json.size());
            auto r0 = probe::win_close(w);
            std::printf("%-40s %9zu %9zu %9zu %7.2f\n", p.name, p.json.size(),
                        r0.peak_live, r0.residual,
                        static_cast<double>(r0.residual) /
                            static_cast<double>(p.json.size()));
        }
    }
}

}  // namespace

int main(int argc, char** argv) {
    const char* mode = argc > 1 ? argv[1] : "count";
    std::setvbuf(stdout, nullptr, _IOLBF, 0);

    std::printf("# sizeof: void*=%zu size_t=%zu std::string=%zu "
                "rapidjson::Value=%zu rapidjson::Document=%zu\n",
                sizeof(void*), sizeof(size_t), sizeof(std::string),
                sizeof(rapidjson::Value), sizeof(rapidjson::Document));

    for (Fill shape : {Fill::FEW_BIG, Fill::MANY_SMALL}) {
        const char* sname =
            shape == Fill::FEW_BIG ? "stores filled: FEW BIG (64-iv programs)"
                                   : "stores filled: MANY SMALL (6-iv programs)";
        static TestServer* ts = nullptr;
        ts = new TestServer();
        size_t before_fill = probe::live_bytes();
        fill_stores(*ts, shape);
        size_t after_fill = probe::live_bytes();

        std::printf("\n#### %s\n", sname);
        std::printf("# resident store bytes after fill: %zu\n",
                    after_fill - before_fill);
        std::printf("# serialized store sizes: history=%zu workouts=%zu "
                    "runs=%zu profiles=%zu\n",
                    ts->history.serialize().size(),
                    ts->workouts.serialize().size(), ts->runs.serialize().size(),
                    ts->profiles.serialize().size());
        std::printf("# entry counts: history=%d workouts=%d runs=%d "
                    "profiles=%d\n",
                    ts->history.size(), ts->workouts.size(), ts->runs.size(),
                    ts->profiles.size());

        auto scs = make_scenarios(*ts);
        for (const auto& sc : scs) {
            // Warm once (first call can allocate one-shot statics), then
            // measure the steady-state request.
            ts->req(sc.method, sc.path, sc.body);
            auto w = probe::win_open();
            auto resp = ts->req(sc.method, sc.path, sc.body);
            auto r = probe::win_close(w);
            emit(sc.name, r, resp.body.size());
        }
        print_rows(sname);

        // WS frame builders (the other producer of per-tick garbage)
        {
            struct WsCase {
                const char* name;
            };
            auto w1 = probe::win_open();
            auto s1 = ts->core.status_json();
            auto r1 = probe::win_close(w1);
            emit("ws: status_json()", r1, s1.size());

            auto w2 = probe::win_open();
            auto s2 = ts->core.program_json();
            auto r2 = probe::win_close(w2);
            emit("ws: program_json() (64 iv)", r2, s2.size());

            auto w3 = probe::win_open();
            auto s3 = ts->core.session_json();
            auto r3 = probe::win_close(w3);
            emit("ws: session_json()", r3, s3.size());

            auto w4 = probe::win_open();
            std::vector<std::string> frames;
            ts->core.connect_frames(frames);
            size_t tot = 0;
            for (auto& f : frames) tot += f.size();
            auto r4 = probe::win_close(w4);
            emit("ws: connect_frames() (3 frames)", r4, tot);

            auto w5 = probe::win_open();
            int n = ts->core.kv_tick();
            auto r5 = probe::win_close(w5);
            emit("ws: kv_tick()", r5, static_cast<size_t>(n));

            auto w6 = probe::win_open();
            ts->core.session_tick();
            auto r6 = probe::win_close(w6);
            emit("1 Hz: session_tick() (+30 s ckpt)", r6, 0);
        }
        print_rows("periodic / WS producers");

        // "no resident DOM" variant: the list endpoints re-read their
        // stores from flash INSIDE the request (property 5 of the
        // mandate), so the peak includes the store parse.
        {
            auto w = probe::win_open();
            ts->history.init(ts->fs, ts->persist, "program_history.json");
            ts->workouts.init(ts->fs, ts->persist, "saved_workouts.json");
            ts->runs.init(ts->fs, ts->persist, "run_history.json");
            auto resp = ts->core.get_history();
            auto r = probe::win_close(w);
            emit("flash-read + GET /api/programs/history", r, resp.body.size());
        }
        {
            auto w = probe::win_open();
            ts->history.init(ts->fs, ts->persist, "program_history.json");
            ts->workouts.init(ts->fs, ts->persist, "saved_workouts.json");
            ts->runs.init(ts->fs, ts->persist, "run_history.json");
            auto resp = ts->core.get_workouts();
            auto r = probe::win_close(w);
            emit("flash-read + GET /api/workouts", r, resp.body.size());
        }
        print_rows("no-resident-DOM variant (store parsed inside request)");

        if (std::strcmp(mode, "mh") == 0) {
            std::printf("\n== minimum REAL multi_heap region (TLSF) ==\n");
            std::printf("%-42s %9s %9s %8s\n", "request", "peak_B", "min_rgn_B",
                        "premium");
            for (const auto& sc : scs) {
                // measure peak first
                ts->req(sc.method, sc.path, sc.body);
                auto w = probe::win_open();
                ts->req(sc.method, sc.path, sc.body);
                auto r = probe::win_close(w);
                if (r.peak_live < 512) continue;
                size_t mr = min_region(*ts, sc, 4096);
                std::printf("%-42s %9zu %9zu %7.2fx\n", sc.name, r.peak_live,
                            mr,
                            mr == 0 ? 0.0
                                    : static_cast<double>(mr) /
                                          static_cast<double>(r.peak_live));
            }
        }
        delete ts;
    }

    dom_overhead();
    return 0;
}
