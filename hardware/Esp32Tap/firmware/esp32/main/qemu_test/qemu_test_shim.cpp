/*
 * qemu_test_shim.cpp — ESP32TAP_QEMU_TEST-only harness surface.
 *
 * See qemu_test_shim.h for the rationale and the command surface. All of
 * this is cold-path test plumbing (std::string_view + from_chars parsing,
 * bounded buffers, printf on the debug console); it never runs in the
 * default build.
 */

#include "qemu_test_shim.h"

#include <charconv>
#include <cstdio>
#include <mutex>
#include <optional>
#include <string_view>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/uart.h"
#include "esp_log.h"
#include "esp_system.h"
#include "esp_timer.h"

#include "firmware_context.h"
#include "transport_httpd.h"
#include "wdt.h"

namespace esp32tap::qemu_test {

namespace {

const char* TAG = "esp32tap";

constexpr TickType_t kQemuTestDelayTicks = pdMS_TO_TICKS(100);
static_assert(kQemuTestDelayTicks > 0, "CONFIG_FREERTOS_HZ too low");

constexpr std::string_view kCmdPrefix = "QT ";

const char* mode_name(safety::SafeMode m) {
    using safety::SafeMode;
    switch (m) {
        case SafeMode::PROXY: return "PROXY";
        case SafeMode::ENTRY_WAIT_GAP: return "ENTRY_WAIT_GAP";
        case SafeMode::ENTRY_WAIT_FEEDBACK: return "ENTRY_WAIT_FEEDBACK";
        case SafeMode::EMULATING: return "EMULATING";
        case SafeMode::EXIT_WAIT_GAP: return "EXIT_WAIT_GAP";
        case SafeMode::EXIT_WAIT_FEEDBACK: return "EXIT_WAIT_FEEDBACK";
    }
    return "UNKNOWN";
}

std::optional<int> parse_int(std::string_view s) {
    if (s.empty() || s.size() > 10) return std::nullopt;
    int value = 0;
    auto [ptr, ec] = std::from_chars(s.data(), s.data() + s.size(), value);
    if (ec != std::errc{} || ptr != s.data() + s.size()) return std::nullopt;
    return value;
}

}  // namespace

// --- QemuTestSafetyIo ---------------------------------------------------

bool QemuTestSafetyIo::init() {
    // Wrap the real HAL so output-pin configuration order (outputs low
    // FIRST) is preserved; input configs are harmless GPIO-stub writes.
    return real_.init();
}

void QemuTestSafetyIo::set_relay_cmd(bool on) {
    real_.set_relay_cmd(on);
    bool prev = relay_on_.exchange(on, std::memory_order_relaxed);
    if (prev != on) {
        relay_edge_us_.store(esp_timer_get_time(), std::memory_order_relaxed);
    }
}

void QemuTestSafetyIo::set_tx_enable(bool on) {
    real_.set_tx_enable(on);
    tx_on_.store(on, std::memory_order_relaxed);
}

bool QemuTestSafetyIo::tread_ok() {
    return tread_ok_.load(std::memory_order_relaxed);
}

bool QemuTestSafetyIo::in_transit() const {
    int64_t edge = relay_edge_us_.load(std::memory_order_relaxed);
    return esp_timer_get_time() - edge < K1_TRANSIT_US;
}

void QemuTestSafetyIo::script_k1(K1Mode m) {
    if (m == K1Mode::STUCK) {
        // Freeze at the current settled pole state (the harness scripts
        // STUCK only outside the 2 ms transit window).
        stuck_relay_on_.store(relay_on_.load(std::memory_order_relaxed),
                              std::memory_order_relaxed);
    }
    k1_mode_.store(m, std::memory_order_relaxed);
}

bool QemuTestSafetyIo::k1_nc_high() {
    // Pull-up semantics: HIGH = contact OPEN. Break-before-make transit
    // shows BOTH_OPEN, then the target pole state:
    //   relay released -> BYPASS  (NC closed/LOW, NO open/HIGH)
    //   relay energized -> EMULATE (NC open/HIGH, NO closed/LOW)
    switch (k1_mode_.load(std::memory_order_relaxed)) {
        case K1Mode::STUCK:
            return stuck_relay_on_.load(std::memory_order_relaxed);
        case K1Mode::FORCE_BYPASS: return false;  // NC closed
        case K1Mode::FORCE_EMULATE: return true;  // NC open
        case K1Mode::FORCE_OPEN: return true;
        case K1Mode::FORCE_CLOSED: return false;
        case K1Mode::AUTO: break;
    }
    if (in_transit()) return true;
    return relay_on_.load(std::memory_order_relaxed);
}

bool QemuTestSafetyIo::k1_no_high() {
    switch (k1_mode_.load(std::memory_order_relaxed)) {
        case K1Mode::STUCK:
            return !stuck_relay_on_.load(std::memory_order_relaxed);
        case K1Mode::FORCE_BYPASS: return true;    // NO open
        case K1Mode::FORCE_EMULATE: return false;  // NO closed
        case K1Mode::FORCE_OPEN: return true;
        case K1Mode::FORCE_CLOSED: return false;
        case K1Mode::AUTO: break;
    }
    if (in_transit()) return true;
    return !relay_on_.load(std::memory_order_relaxed);
}

bool QemuTestSafetyIo::vbus_present() {
    return vbus_.load(std::memory_order_relaxed);
}

void QemuTestSafetyIo::set_status_led(bool on) { real_.set_status_led(on); }

// --- QemuTestMotorTap ---------------------------------------------------

bool QemuTestMotorTap::init() {
    // Motor-tap RX on UART0 (the only free chardev-wired UART RX in this
    // QEMU). Do NOT reconfigure params: UART0 stays the debug console;
    // chardev bytes ignore baud anyway.
    if (!uart_is_driver_installed(UART_NUM_0)) {
        if (uart_driver_install(UART_NUM_0, 1024, 0, 0, nullptr, 0) !=
            ESP_OK) {
            return false;
        }
    }
    ready_ = true;
    return true;
}

size_t QemuTestMotorTap::read(std::span<uint8_t> out) {
    if (!ready_ || out.size() < 8) return 0;
    // Leave room for a flushed partial "QT " prefix (<= 3 bytes).
    std::array<uint8_t, 256> tmp{};
    size_t want = out.size() - 4;
    if (want > tmp.size()) want = tmp.size();
    int n = uart_read_bytes(UART_NUM_0, tmp.data(), want, 0);
    if (n <= 0) return 0;

    size_t w = 0;
    for (int i = 0; i < n; i++) {
        uint8_t b = tmp.at(static_cast<size_t>(i));
        if (in_cmd_) {
            if (dropping_oversize_) {
                if (b == static_cast<uint8_t>('\n')) {
                    dropping_oversize_ = false;
                    in_cmd_ = false;
                    at_line_start_ = true;
                }
                continue;
            }
            if (b == static_cast<uint8_t>('\n')) {
                cmd_buf_.at(cmd_len_) = '\0';
                uint32_t head = q_head_.load(std::memory_order_acquire);
                uint32_t tail = q_tail_.load(std::memory_order_relaxed);
                if (tail - head < QUEUE_SLOTS) {
                    queue_.at(tail % QUEUE_SLOTS) = cmd_buf_;
                    q_tail_.store(tail + 1, std::memory_order_release);
                }  // else: drop (harness sends at low rate)
                in_cmd_ = false;
                cmd_len_ = 0;
                at_line_start_ = true;
                continue;
            }
            if (cmd_len_ >= CMD_MAX) {
                dropping_oversize_ = true;  // length-validated: drop line
                cmd_len_ = 0;
                continue;
            }
            cmd_buf_.at(cmd_len_) = static_cast<char>(b);
            cmd_len_++;
            continue;
        }
        if (at_line_start_ || prefix_matched_ > 0) {
            if (b == static_cast<uint8_t>(kCmdPrefix.at(prefix_matched_))) {
                prefix_matched_++;
                at_line_start_ = false;
                if (prefix_matched_ == kCmdPrefix.size()) {
                    in_cmd_ = true;
                    cmd_len_ = kCmdPrefix.size();
                    kCmdPrefix.copy(cmd_buf_.data(), kCmdPrefix.size());
                    prefix_matched_ = 0;
                }
                continue;
            }
            // Mismatch: flush the partially matched prefix as motor data.
            for (size_t p = 0; p < prefix_matched_; p++) {
                out[w] = static_cast<uint8_t>(kCmdPrefix.at(p));
                w++;
            }
            prefix_matched_ = 0;
        }
        out[w] = b;
        w++;
        at_line_start_ = (b == static_cast<uint8_t>('\n'));
    }
    return w;
}

bool QemuTestMotorTap::pop_command(std::span<char> out) {
    if (out.size() < CMD_MAX + 1) return false;
    uint32_t head = q_head_.load(std::memory_order_relaxed);
    uint32_t tail = q_tail_.load(std::memory_order_acquire);
    if (head == tail) return false;
    const auto& slot = queue_.at(head % QUEUE_SLOTS);
    for (size_t i = 0; i < slot.size(); i++) {
        out[i] = slot.at(i);
    }
    q_head_.store(head + 1, std::memory_order_release);
    return true;
}

// --- qemu_test task -----------------------------------------------------

namespace {

void print_state(FirmwareContext* ctx) {
    // Snapshot under controller_mu, print outside.
    const char* mode = nullptr;
    int relay = 0, tx = 0, fault = 0, speed = 0, incline = 0;
    int io_relay = 0, io_tx = 0;
    int64_t t_us = 0;
    uint32_t cons = 0, motor = 0;
    {
        std::lock_guard<std::mutex> lk(ctx->controller_mu);
        mode = mode_name(ctx->controller.mode());
        relay = ctx->controller.relay_cmd() ? 1 : 0;
        tx = ctx->controller.tx_enable() ? 1 : 0;
        fault = ctx->controller.fault_latched() ? 1 : 0;
        speed = ctx->controller.speed_tenths();
        incline = ctx->controller.incline_half_percent();
        cons = ctx->mode.console_bytes();
        motor = ctx->mode.motor_bytes();
        // IO-boundary-observed levels (not controller self-reports) and
        // the guest clock, sampled in the same critical section so one
        // QTSTATE line is a coherent instant.
        io_relay = ctx->safety_io.observed_relay() ? 1 : 0;
        io_tx = ctx->safety_io.observed_tx() ? 1 : 0;
        t_us = ctx->clock.now_us();
    }
    std::printf(
        "QTSTATE mode=%s relay=%d tx=%d fault=%d speed=%d incline=%d "
        "cons_bytes=%u motor_bytes=%u io_relay=%d io_tx=%d t_us=%lld\n",
        mode, relay, tx, fault, speed, incline,
        static_cast<unsigned>(cons), static_cast<unsigned>(motor),
        io_relay, io_tx, static_cast<long long>(t_us));
}

void execute_command(FirmwareContext* ctx, std::string_view line,
                     safety::ConnectionIdentity& owner, int64_t& lease_gen) {
    using safety::ConnectionIdentity;
    using safety::Transport;
    if (line.size() < kCmdPrefix.size() ||
        line.substr(0, kCmdPrefix.size()) != kCmdPrefix) {
        std::printf("QTERR bad_frame\n");
        return;
    }
    std::string_view rest = line.substr(kCmdPrefix.size());
    while (!rest.empty() && (rest.back() == '\r' || rest.back() == ' ')) {
        rest.remove_suffix(1);
    }
    auto sp = rest.find(' ');
    std::string_view verb =
        sp == std::string_view::npos ? rest : rest.substr(0, sp);
    std::string_view args =
        sp == std::string_view::npos ? std::string_view{} : rest.substr(sp + 1);

    if (verb == "state") {
        print_state(ctx);
        return;
    }
    if (verb == "lease") {
        bool connected = false, acquired = false;
        {
            std::lock_guard<std::mutex> lk(ctx->controller_mu);
            lease_gen++;
            owner = ConnectionIdentity{Transport::EXECUTOR, 1, lease_gen};
            connected = ctx->controller.connect(owner);
            acquired = ctx->controller.acquire(owner, ctx->clock.now_us());
        }
        std::printf("QTOK lease connect=%d acquire=%d gen=%lld\n",
                    connected ? 1 : 0, acquired ? 1 : 0,
                    static_cast<long long>(lease_gen));
        return;
    }
    if (verb == "emulate") {
        // Same gate expression production would use: the physical TX pad
        // level (GPIO17 reads 0 in QEMU -> idle-low true).
        bool idle_low = ctx->console_uart.tx_idle_low();
        bool ok = false;
        {
            std::lock_guard<std::mutex> lk(ctx->controller_mu);
            ok = ctx->controller.request_emulate(owner, ctx->clock.now_us(),
                                                 idle_low);
            ctx->apply_outputs_locked();
        }
        std::printf("QTOK emulate ok=%d\n", ok ? 1 : 0);
        return;
    }
    if (verb == "motion") {
        auto sp2 = args.find(' ');
        if (sp2 == std::string_view::npos) {
            std::printf("QTERR motion_args\n");
            return;
        }
        auto speed = parse_int(args.substr(0, sp2));
        auto incline = parse_int(args.substr(sp2 + 1));
        if (!speed.has_value() || !incline.has_value()) {
            std::printf("QTERR motion_args\n");
            return;
        }
        bool ok = false;
        {
            std::lock_guard<std::mutex> lk(ctx->controller_mu);
            ok = ctx->controller.command_motion(owner, *speed, *incline,
                                                ctx->clock.now_us());
        }
        std::printf("QTOK motion ok=%d\n", ok ? 1 : 0);
        return;
    }
    if (verb == "exit") {
        bool ok = false;
        {
            std::lock_guard<std::mutex> lk(ctx->controller_mu);
            ok = ctx->controller.request_normal_exit(owner,
                                                     ctx->clock.now_us());
            ctx->apply_outputs_locked();
        }
        std::printf("QTOK exit ok=%d\n", ok ? 1 : 0);
        return;
    }
    if (verb == "k1") {
        using K1Mode = QemuTestSafetyIo::K1Mode;
        std::optional<K1Mode> m;
        if (args == "auto") m = K1Mode::AUTO;
        else if (args == "stuck") m = K1Mode::STUCK;
        else if (args == "bypass") m = K1Mode::FORCE_BYPASS;
        else if (args == "emulate") m = K1Mode::FORCE_EMULATE;
        else if (args == "open") m = K1Mode::FORCE_OPEN;
        else if (args == "closed") m = K1Mode::FORCE_CLOSED;
        if (!m.has_value()) {
            std::printf("QTERR k1_args\n");
            return;
        }
        {
            std::lock_guard<std::mutex> lk(ctx->controller_mu);
            ctx->safety_io.script_k1(*m);
        }
        std::printf("QTOK k1 mode=%.*s\n", static_cast<int>(args.size()),
                    args.data());
        return;
    }
    if (verb == "wsdrophello") {
        // Fault injection for the native server tier: force the WS
        // handshake's "hello could not be queued" branch so the harness
        // can prove a client that never receives its hello frames is
        // still registered and still gets the 1 Hz broadcast stream
        // (regression: registration used to live inside the hello
        // delivery callback).
        auto v = parse_int(args);
        if (!v.has_value() || (*v != 0 && *v != 1)) {
            std::printf("QTERR level_args\n");
            return;
        }
        net::ws_test_force_hello_drop(*v == 1);
        std::printf("QTOK wsdrophello v=%d\n", *v);
        return;
    }
    if (verb == "tread" || verb == "vbus") {
        auto v = parse_int(args);
        if (!v.has_value() || (*v != 0 && *v != 1)) {
            std::printf("QTERR level_args\n");
            return;
        }
        {
            std::lock_guard<std::mutex> lk(ctx->controller_mu);
            if (verb == "tread") {
                ctx->safety_io.script_tread_ok(*v == 1);
            } else {
                ctx->safety_io.script_vbus_present(*v == 1);
            }
        }
        std::printf("QTOK %.*s v=%d\n", static_cast<int>(verb.size()),
                    verb.data(), *v);
        return;
    }
    std::printf("QTERR unknown_verb %.*s\n", static_cast<int>(verb.size()),
                verb.data());
}

void qemu_test_task(void* arg) {
    auto* ctx = static_cast<FirmwareContext*>(arg);
    if (!esp_hal::wdt_subscribe_current_task()) {
        esp_system_abort("qemu_test: task WDT subscribe failed");
    }
    ESP_LOGI(TAG, "qemu_test task started (WDT-supervised)");

    // Static (not task stack): batch buffers are multi-KB.
    static constexpr int kBatch = 32;
    static std::array<
        std::array<char, safety::SafetyController::EVENT_MAX_LEN + 1>, kBatch>
        texts;
    static std::array<uint64_t, kBatch> idxs;
    static std::array<char, QemuTestMotorTap::CMD_MAX + 1> cmd;

    uint64_t next_event = 0;
    int64_t lease_gen = 0;
    safety::ConnectionIdentity owner{safety::Transport::EXECUTOR, 1, 0};

    for (;;) {
        esp_hal::wdt_feed();

        // (i) Drain the audit ring, <= kBatch events per lock hold,
        // looping until caught up.
        for (;;) {
            int n = 0;
            {
                std::lock_guard<std::mutex> lk(ctx->controller_mu);
                uint64_t total = ctx->controller.event_count();
                constexpr uint64_t kCap = static_cast<uint64_t>(
                    safety::SafetyController::EVENT_CAPACITY);
                if (total > next_event && total - next_event > kCap) {
                    // Evicted (should not happen at scenario event rates —
                    // surfaced so the harness can detect the gap).
                    next_event = total - kCap;
                }
                while (next_event < total && n < kBatch) {
                    std::string_view ev = ctx->controller.event_at(next_event);
                    auto& slot = texts.at(static_cast<size_t>(n));
                    size_t len = ev.copy(slot.data(), slot.size() - 1);
                    slot.at(len) = '\0';
                    idxs.at(static_cast<size_t>(n)) = next_event;
                    n++;
                    next_event++;
                }
            }
            for (int i = 0; i < n; i++) {
                std::printf("QTAUDIT %llu %s\n",
                            static_cast<unsigned long long>(
                                idxs.at(static_cast<size_t>(i))),
                            texts.at(static_cast<size_t>(i)).data());
            }
            if (n < kBatch) break;
        }

        // (ii) Execute queued harness commands (never from inside the
        // serial-engine poll — that would deadlock on controller_mu).
        while (ctx->motor_tap.pop_command(std::span<char>(cmd))) {
            execute_command(ctx, std::string_view(cmd.data()), owner,
                            lease_gen);
        }

        std::fflush(stdout);
        vTaskDelay(kQemuTestDelayTicks);
    }
}

}  // namespace

void start_qemu_test_task(FirmwareContext* ctx) {
    xTaskCreatePinnedToCore(qemu_test_task, "qemu_test", 6144, ctx, 4,
                            nullptr, 0);
}

}  // namespace esp32tap::qemu_test
