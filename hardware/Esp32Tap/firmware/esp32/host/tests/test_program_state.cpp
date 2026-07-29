/*
 * test_program_state.cpp — ProgramState + WorkoutSession + fingerprint
 * host tests, transliterated from python/tests/test_program_engine.py
 * cases (pause/resume, skip/prev recompute, extend floor,
 * split_for_manual no-op rules, resume milestone pre-mark, ACSM
 * calories).
 */

#define DOCTEST_CONFIG_IMPLEMENT_WITH_MAIN
#define DOCTEST_CONFIG_NO_EXCEPTIONS_BUT_WITH_ALL_ASSERTS
#include <doctest.h>

#include <string>
#include <vector>

#include "fingerprint.h"
#include "program_state.h"
#include "time_source.h"
#include "workout_session.h"

using namespace esp32tap::exec;

namespace {

class TestTime : public TimeSource {
public:
    int64_t now = 5'000'000;
    int64_t now_us() override { return now; }
    std::string now_iso() override { return "2026-07-27T10:00:00"; }
    void advance_s(int64_t s) { now += s * 1'000'000; }
};

struct Events : public ProgramEvents {
    std::vector<std::pair<double, double>> changes;
    int updates = 0;
    void on_change(double speed, double incline) override {
        changes.emplace_back(speed, incline);
    }
    void on_update() override { updates++; }
};

Program two_interval_program() {
    Program p{};
    p.name.set("Test");
    p.count = 2;
    p.intervals.at(0).name.set("Warmup");
    p.intervals.at(0).duration = 60;
    p.intervals.at(0).speed = 2.0;
    p.intervals.at(0).incline = 0.0;
    p.intervals.at(1).name.set("Run");
    p.intervals.at(1).duration = 120;
    p.intervals.at(1).speed = 5.5;
    p.intervals.at(1).incline = 1.5;
    return p;
}

Program manual_program(int minutes = 60) {
    Program p{};
    p.name.set("60-Min Manual");
    p.manual = true;
    p.count = 1;
    p.intervals.at(0).name.set("Seg 1");
    p.intervals.at(0).duration = minutes * 60;
    p.intervals.at(0).speed = 3.0;
    p.intervals.at(0).incline = 0.0;
    return p;
}

}  // namespace

TEST_CASE("start applies first interval and ticks advance intervals") {
    TestTime t;
    Events ev;
    ProgramState ps(t);
    ps.set_events(&ev);
    ps.load(two_interval_program());
    ps.start();
    REQUIRE(ps.running());
    REQUIRE(ev.changes.size() == 1);
    CHECK(ev.changes.back().first == doctest::Approx(2.0));

    // 59 s in: still interval 0
    t.advance_s(59);
    ps.tick();
    CHECK(ps.current_interval() == 0);
    CHECK(ps.total_elapsed() == 59);

    // 61 s in: advanced to interval 1, speed 5.5 applied
    t.advance_s(2);
    ps.tick();
    CHECK(ps.current_interval() == 1);
    CHECK(ev.changes.back().first == doctest::Approx(5.5));
    CHECK(ev.changes.back().second == doctest::Approx(1.5));

    // Past the end: completed, motion zeroed
    t.advance_s(121);
    ps.tick();
    CHECK(ps.completed());
    CHECK_FALSE(ps.running());
    CHECK(ev.changes.back().first == doctest::Approx(0.0));
}

TEST_CASE("pause freezes elapsed; resume re-applies interval targets") {
    TestTime t;
    Events ev;
    ProgramState ps(t);
    ps.set_events(&ev);
    ps.load(two_interval_program());
    ps.start();

    t.advance_s(10);
    ps.tick();
    CHECK(ps.total_elapsed() == 10);

    ps.toggle_pause();
    REQUIRE(ps.paused());
    t.advance_s(30);
    ps.tick();  // paused tick: no elapsed advance
    CHECK(ps.total_elapsed() == 10);

    size_t n_changes = ev.changes.size();
    ps.toggle_pause();
    REQUIRE_FALSE(ps.paused());
    // Resume re-applied current interval speed (python parity)
    REQUIRE(ev.changes.size() == n_changes + 1);
    CHECK(ev.changes.back().first == doctest::Approx(2.0));
    t.advance_s(5);
    ps.tick();
    CHECK(ps.total_elapsed() == 15);
}

TEST_CASE("skip advances and prev floors at 0") {
    TestTime t;
    Events ev;
    ProgramState ps(t);
    ps.set_events(&ev);
    ps.load(two_interval_program());
    ps.start();

    ps.skip();
    CHECK(ps.current_interval() == 1);
    CHECK(ps.total_elapsed() == 60);  // cumulative at interval 1
    CHECK(ps.interval_elapsed() == 0);

    ps.prev();
    CHECK(ps.current_interval() == 0);
    CHECK(ps.total_elapsed() == 0);

    ps.prev();  // floors at 0, no wrap
    CHECK(ps.current_interval() == 0);

    // skip past the last interval finishes the program
    ps.skip();
    ps.skip();
    CHECK(ps.completed());
    CHECK_FALSE(ps.running());
}

TEST_CASE("extend floors at 10 s and requires running") {
    TestTime t;
    ProgramState ps(t);
    ps.load(two_interval_program());
    CHECK_FALSE(ps.extend_current(60));  // not running
    ps.start();
    CHECK(ps.extend_current(-3600));
    CHECK(ps.program().intervals.at(0).duration == 10);
    CHECK(ps.extend_current(50));
    CHECK(ps.program().intervals.at(0).duration == 60);
}

TEST_CASE("split_for_manual no-op rules and split behavior") {
    TestTime t;
    Events ev;
    ProgramState ps(t);
    ps.set_events(&ev);

    // Not manual -> no-op
    ps.load(two_interval_program());
    ps.start();
    CHECK_FALSE(ps.split_for_manual(4.0, 0.0));

    // Manual: same values -> no-op
    ps.load(manual_program());
    ps.start();
    t.advance_s(30);
    ps.tick();
    CHECK_FALSE(ps.split_for_manual(3.0, 0.0));

    // Different speed -> split: current trimmed, "Seg 2" inserted
    CHECK(ps.split_for_manual(4.0, 0.5));
    REQUIRE(ps.program().count == 2);
    CHECK(ps.program().intervals.at(0).duration == 30);
    CHECK(ps.program().intervals.at(1).name.view() == "Seg 2");
    CHECK(ps.program().intervals.at(1).duration == 3600 - 30);
    CHECK(ps.program().intervals.at(1).speed == doctest::Approx(4.0));
    CHECK(ps.current_interval() == 1);
    CHECK(ps.interval_elapsed() == 0);

    // Remaining < 1 s -> no-op (1 s interval: elapsed clamps to 1,
    // remaining 0)
    ProgramState ps2(t);
    Program p = manual_program(1);
    p.intervals.at(0).duration = 1;
    ps2.load(p);
    ps2.start();
    CHECK_FALSE(ps2.split_for_manual(5.0, 0.0));
}

TEST_CASE("adjust_duration manual-only, last interval, floor 10") {
    TestTime t;
    ProgramState ps(t);
    ps.load(two_interval_program());
    ps.start();
    CHECK_FALSE(ps.adjust_duration(60));  // not manual

    ps.load(manual_program());
    ps.start();
    CHECK(ps.adjust_duration(-7200));
    CHECK(ps.program().intervals.at(0).duration == 10);
    CHECK(ps.adjust_duration(600));
    CHECK(ps.program().intervals.at(0).duration == 610);
}

TEST_CASE("resume pre-marks passed milestones") {
    // No events wired: broadcast() does not drain, so the pending
    // encouragement stays observable after tick().
    TestTime t;
    ProgramState ps(t);
    Program p{};
    p.name.set("Long");
    p.count = 1;
    p.intervals.at(0).name.set("All");
    p.intervals.at(0).duration = 1000;
    p.intervals.at(0).speed = 3.0;
    p.intervals.at(0).incline = 0.0;
    ps.load(p);

    // Resume at 60% — 25% and 50% pre-marked, 75% still to fire.
    ps.start(0, 600);
    CHECK(ps.total_elapsed() == 600);

    t.advance_s(151);  // -> 751s = 75.1%
    ps.tick();
    CHECK(ps.pending_encouragement() ==
          "Three quarters done — the finish line is in sight!");
}

TEST_CASE("WorkoutSession ACSM calories, distance, vert feet") {
    TestTime t;
    ProgramState ps(t);
    WorkoutSession sess(t, ps);
    sess.start();
    CHECK(sess.wall_started_at() == "2026-07-27T10:00:00");

    // 60 ticks of 1 s at 3.0 mph flat (walking equation)
    for (int i = 0; i < 60; i++) {
        t.advance_s(1);
        sess.tick(3.0, 0.0);
    }
    CHECK(sess.elapsed() == doctest::Approx(60.0));
    CHECK(sess.distance() == doctest::Approx(0.05));
    CHECK(sess.vert_feet() == doctest::Approx(0.0));
    // VO2 = 3.5 + 0.1*80.4672 = 11.54672; kcal/min = *70/1000*5 = 4.0413
    CHECK(sess.calories() == doctest::Approx(4.0413).epsilon(0.001));

    // Running equation + incline accrues vert feet
    for (int i = 0; i < 60; i++) {
        t.advance_s(1);
        sess.tick(6.0, 5.0);
    }
    // second minute distance: 0.1 mi; vert = 0.1*0.05*5280 = 26.4 ft
    CHECK(sess.distance() == doctest::Approx(0.15));
    CHECK(sess.vert_feet() == doctest::Approx(26.4));

    // Paused: elapsed frozen
    sess.pause();
    t.advance_s(30);
    sess.tick(6.0, 5.0);
    CHECK(sess.elapsed() == doctest::Approx(120.0));
    sess.resume();
    t.advance_s(1);
    sess.tick(0.0, 0.0);
    CHECK(sess.elapsed() == doctest::Approx(121.0));

    sess.end("user_stop");
    CHECK_FALSE(sess.active());
    CHECK(sess.end_reason() == "user_stop");
}

TEST_CASE("ensure_manual creates and starts a 60-min manual program") {
    TestTime t;
    Events ev;
    ProgramState ps(t);
    ps.set_events(&ev);
    WorkoutSession sess(t, ps);
    sess.ensure_manual(3.5, 1.0, 60);
    CHECK(sess.active());
    REQUIRE(ps.running());
    CHECK(ps.is_manual());
    CHECK(ps.program().name.view() == "60-Min Manual");
    REQUIRE(ps.program().count == 1);
    CHECK(ps.program().intervals.at(0).duration == 3600);
    CHECK(ev.changes.back().first == doctest::Approx(3.5));

    // Running program -> ensure_manual is a no-op
    sess.ensure_manual(9.9, 0.0, 5);
    CHECK(ps.program().intervals.at(0).speed == doctest::Approx(3.5));
}

TEST_CASE("program fingerprint python-parity format") {
    Program p = two_interval_program();
    CHECK(esp32tap::exec::program_fingerprint(p) ==
          "2.0,0.0,60|5.5,1.5,120");
    Program empty{};
    CHECK(esp32tap::exec::program_fingerprint(empty) == "");
}

TEST_CASE("extend_current is bounded above: repeated extends cannot overflow") {
    TestTime t;
    Events ev;
    ProgramState ps(t);
    ps.set_events(&ev);
    Program p = two_interval_program();
    ps.load(p);
    ps.start();

    // Each call's delta is bounded by the endpoint (+/-3600), but the
    // STORED duration was previously unbounded: repeat it and the int
    // walks off the end of its range (signed overflow == UB) on a field
    // that gets persisted. The mutator must enforce the same ceiling
    // program_from_json applies to parsed input.
    for (int i = 0; i < 100; i++) CHECK(ps.extend_current(3600));
    CHECK(ps.program().intervals.at(0).duration == MAX_DURATION_S);
    // Still monotonic toward the floor on the way back down.
    for (int i = 0; i < 100; i++) CHECK(ps.extend_current(-3600));
    CHECK(ps.program().intervals.at(0).duration == MIN_DURATION_S);
}

TEST_CASE("adjust_duration is bounded above as well as below") {
    TestTime t;
    Events ev;
    ProgramState ps(t);
    ps.set_events(&ev);
    Program p = manual_program(60);
    ps.load(p);
    ps.start();

    for (int i = 0; i < 100; i++) CHECK(ps.adjust_duration(3600));
    CHECK(ps.program().intervals.at(0).duration == MAX_DURATION_S);
    CHECK(ps.total_duration() == MAX_DURATION_S);
    for (int i = 0; i < 100; i++) CHECK(ps.adjust_duration(-3600));
    CHECK(ps.program().intervals.at(0).duration == MIN_DURATION_S);
}
