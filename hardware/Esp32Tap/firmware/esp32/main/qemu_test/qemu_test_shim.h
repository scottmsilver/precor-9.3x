/*
 * qemu_test_shim.h — ESP32TAP_QEMU_TEST-only harness surface.
 *
 * Compiled ONLY when the CMake cache var ESP32TAP_QEMU_TEST=1 selects it
 * (main/CMakeLists.txt); the default build contains none of this code and
 * none of its strings (the harness asserts that on the production image).
 * A test image logs a "QEMU-TEST build" banner at boot so it can never be
 * mistaken for production. NEVER flash a test image to hardware.
 *
 * Why this exists (proven QEMU ground truth, see tools/qemu_harness/README.md):
 *  - The pinned espressif QEMU (esp-QEMU 9.2.2, espressif/idf:release-v5.5)
 *    hard-wires uart0->serial0 and uart1->serial1; UART2 has NO chardev and
 *    cannot be wired by any -serial/-global/qom mechanism, so the motor-tap
 *    port is remapped to UART0 RX here (QemuTestMotorTap).
 *  - The esp32s3 GPIO model is a stub with zero input lines: GPIO_IN always
 *    reads 0 and cannot be driven externally, so K1 feedback / TREAD_OK /
 *    VBUS need the scripted model in QemuTestSafetyIo.
 *
 * The shim only *feeds* the production code paths (SafetyController,
 * SerialReader, EmulationCycle, feedback_window); it provides no way to
 * bypass clamps, console freshness, gap qualification, or feedback
 * qualification — production semantics are not weakened.
 *
 * Harness command surface (lines injected on UART0 RX, framed "\nQT ...\n";
 * the mux below diverts them out of the motor-tap byte stream):
 *   QT lease              connect()+acquire() an EXECUTOR lease
 *                         (handle 1, monotonically increasing generation)
 *   QT emulate            request_emulate(owner, now, console_uart.tx_idle_low())
 *   QT motion <t> <h>     command_motion(speed_tenths, incline_half_pct)
 *   QT exit               request_normal_exit(owner, now)
 *   QT tread <0|1>        script the fake TREAD_OK level
 *   QT vbus <0|1>         script the fake VBUS-present level
 *   QT k1 <mode>          script the modeled K1 relay feedback path:
 *                         auto    command-coupled model (default)
 *                         stuck   freeze poles at the current settled
 *                                 state (coil commands stop moving them —
 *                                 script only while K1 is settled)
 *                         bypass|emulate|open|closed  force the pole state
 *                         Exercises the fail-closed feedback paths
 *                         (entry_feedback_timeout, relay_feedback_invalid)
 *                         that the command-coupled model can never reach.
 *   QT state              print one QTSTATE snapshot line
 * Observability (UART0 TX):
 *   QTAUDIT <abs_index> <event_text>   drained SafetyController audit ring
 *   QTSTATE mode=<name> relay=<0|1> tx=<0|1> fault=<0|1> speed=<tenths>
 *           incline=<half> cons_bytes=<n> motor_bytes=<n>
 *           io_relay=<0|1> io_tx=<0|1> t_us=<guest_us>
 *   relay/tx are SafetyController intent; io_relay/io_tx are the levels
 *   the shim OBSERVED cross the IO boundary (set_relay_cmd/set_tx_enable),
 *   so relay assertions are not purely self-reported; t_us is the guest
 *   monotonic clock at snapshot time (harness guest-time deadline bounds).
 */

#pragma once

#if !defined(ESP32TAP_QEMU_TEST)
#error "qemu_test_shim.h is test-build-only (ESP32TAP_QEMU_TEST=1)"
#endif

#include <array>
#include <atomic>
#include <cstdint>
#include <span>

#include "esp32_safety_io.h"

namespace esp32tap {
struct FirmwareContext;  // firmware_context.h includes this header first
}

namespace esp32tap::qemu_test {

// Scripted stand-in for esp_hal::Esp32SafetyIo (same concrete method
// shape; wraps a real one so output-pin init order semantics are
// preserved). Boot state models the bench rig: K1 released = BYPASS
// (NC closed/LOW, NO open/HIGH), TREAD_OK asserted, VBUS present —
// unlike the default build's floating-GPIO BOTH_CLOSED boot fault.
class QemuTestSafetyIo {
public:
    // Break-before-make transit time of the modeled K1 relay: for this
    // long after a set_relay_cmd() edge both poles read open (BOTH_OPEN),
    // then the target pole state appears. 2 ms sits inside the real-relay
    // envelope: with FEEDBACK_POLL_US=200 the feedback window sees
    // transition -> candidate -> 1 ms stable -> qualification well before
    // the 10 ms fail-closed deadline.
    static constexpr int64_t K1_TRANSIT_US = 2'000;

    // K1 feedback-path scripting (QT k1): AUTO is the command-coupled
    // model above; STUCK freezes the poles at the settled state captured
    // when scripted (subsequent coil commands stop moving them); the
    // FORCE_* modes pin the pole reads outright. These exist to exercise
    // the fail-closed feedback paths (entry/exit feedback timeouts,
    // EMULATING-time feedback loss) that a well-behaved relay never hits.
    enum class K1Mode { AUTO, STUCK, FORCE_BYPASS, FORCE_EMULATE,
                        FORCE_OPEN, FORCE_CLOSED };

    bool init();

    void set_relay_cmd(bool on);
    void set_tx_enable(bool on);
    bool tread_ok();
    bool k1_nc_high();
    bool k1_no_high();
    bool vbus_present();
    void set_status_led(bool on);

    // Harness overrides (QT tread / QT vbus / QT k1).
    void script_tread_ok(bool v) { tread_ok_.store(v, std::memory_order_relaxed); }
    void script_vbus_present(bool v) { vbus_.store(v, std::memory_order_relaxed); }
    void script_k1(K1Mode m);

    // IO-boundary-observed command levels (what set_relay_cmd /
    // set_tx_enable last drove), reported in QTSTATE as io_relay/io_tx so
    // the harness's relay/tx assertions are not controller-self-reported.
    bool observed_relay() const { return relay_on_.load(std::memory_order_relaxed); }
    bool observed_tx() const { return tx_on_.load(std::memory_order_relaxed); }

private:
    bool in_transit() const;

    esp_hal::Esp32SafetyIo real_;
    std::atomic<bool> relay_on_{false};
    std::atomic<bool> tx_on_{false};
    std::atomic<int64_t> relay_edge_us_{-K1_TRANSIT_US};
    std::atomic<bool> tread_ok_{true};
    std::atomic<bool> vbus_{true};
    std::atomic<K1Mode> k1_mode_{K1Mode::AUTO};
    std::atomic<bool> stuck_relay_on_{false};  // frozen pole state (STUCK)
};

// Motor-tap port remapped to UART0 RX (UART2 is unwireable in this QEMU).
// read() drains UART0 RX and runs a line mux: at line start, a line
// beginning "QT " is diverted into a fixed-size SPSC command queue;
// every other byte passes through as motor-tap data. Unambiguous because
// the harness is the only UART0-RX writer, frames commands "\nQT ...\n",
// and motor KV bytes contain no '\n'.
class QemuTestMotorTap {
public:
    static constexpr size_t CMD_MAX = 96;    // max command line length
    static constexpr size_t QUEUE_SLOTS = 8; // SPSC ring capacity

    bool init();
    size_t read(std::span<uint8_t> out);  // SerialReader Port concept

    // Pop one queued command line ("QT ..." without the newline) into
    // out (must be >= CMD_MAX+1; NUL-terminated). Returns false if empty.
    // Single consumer: the qemu_test task.
    bool pop_command(std::span<char> out);

private:
    void queue_push_locked_free();  // producer side helper

    bool ready_ = false;

    // Line mux state (touched only by the serial-engine task via read()).
    bool at_line_start_ = true;
    size_t prefix_matched_ = 0;  // bytes of "QT " matched at line start
    bool in_cmd_ = false;
    bool dropping_oversize_ = false;
    std::array<char, CMD_MAX + 1> cmd_buf_{};
    size_t cmd_len_ = 0;

    // SPSC command ring (producer: read(); consumer: qemu_test task).
    std::array<std::array<char, CMD_MAX + 1>, QUEUE_SLOTS> queue_{};
    std::atomic<uint32_t> q_head_{0};  // consumer index
    std::atomic<uint32_t> q_tail_{0};  // producer index
};

// Create the qemu_test task (prio 4, WDT-subscribed): drains the audit
// ring to QTAUDIT lines every 100 ms and executes queued QT commands
// under controller_mu.
void start_qemu_test_task(FirmwareContext* ctx);

}  // namespace esp32tap::qemu_test
