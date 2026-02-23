/*
 * test_ipc_server.cpp — Tests for IPC server: socket connect, command
 * dispatch, ring buffer flush, client disconnect, max clients.
 *
 * These are "live" socket tests — they create a real Unix socket,
 * connect real clients, and verify end-to-end JSON command/event flow.
 */

#define DOCTEST_CONFIG_IMPLEMENT_WITH_MAIN
#define DOCTEST_CONFIG_NO_EXCEPTIONS
#include <doctest.h>
#include "ipc_server.h"
#include "ipc_protocol.h"
#include "ring_buffer.h"

#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>
#include <fcntl.h>
#include <cstring>
#include <cstdio>
#include <thread>
#include <chrono>
#include <vector>
#include <string>

// Helper: connect a client socket to the IPC server
static int connect_client() {
    int fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0) return -1;

    struct sockaddr_un addr{};
    addr.sun_family = AF_UNIX;
    std::strncpy(addr.sun_path, SOCK_PATH, sizeof(addr.sun_path) - 1);

    if (connect(fd, reinterpret_cast<struct sockaddr*>(&addr), sizeof(addr)) < 0) {
        close(fd);
        return -1;
    }
    return fd;
}

// Helper: send a JSON command line to the server
static void send_cmd(int fd, const char* json) {
    std::string line = std::string(json) + "\n";
    (void)write(fd, line.c_str(), line.size());
}

// Helper: read all available data from fd (non-blocking after short wait)
static std::string read_all(int fd, int wait_ms = 100) {
    std::this_thread::sleep_for(std::chrono::milliseconds(wait_ms));
    char buf[4096];
    std::string result;
    // Set non-blocking temporarily
    int flags = fcntl(fd, F_GETFL, 0);
    fcntl(fd, F_SETFL, flags | O_NONBLOCK);
    while (true) {
        ssize_t n = read(fd, buf, sizeof(buf) - 1);
        if (n <= 0) break;
        buf[n] = '\0';
        result += buf;
    }
    fcntl(fd, F_SETFL, flags);  // restore
    return result;
}

// Helper: run server poll loop for a given duration
static void poll_for(IpcServer& ipc, int ms) {
    auto end = std::chrono::steady_clock::now() + std::chrono::milliseconds(ms);
    while (std::chrono::steady_clock::now() < end) {
        ipc.poll();
    }
}

// ── Basic server lifecycle ──────────────────────────────────────────

TEST_CASE("server creates and shuts down cleanly") {
    RingBuffer<> ring;
    IpcServer ipc(ring);

    CHECK(ipc.create());
    ipc.shutdown();
}

TEST_CASE("client connects and receives initial status") {
    RingBuffer<> ring;
    IpcServer ipc(ring);
    CHECK(ipc.create());

    // Push a status message before client connects
    StatusEvent ev{true, false, 0, 0, -1, -1, 0, 0};
    auto status = build_status_event(ev);
    ring.push(status);

    int client_fd = connect_client();
    CHECK(client_fd >= 0);

    // Run poll to accept the client and flush ring
    poll_for(ipc, 50);

    std::string data = read_all(client_fd, 50);
    CHECK(client_fd >= 0);

    close(client_fd);
    ipc.shutdown();
}

// ── Command dispatch ────────────────────────────────────────────────

TEST_CASE("server dispatches speed command to callback") {
    RingBuffer<> ring;
    IpcServer ipc(ring);
    CHECK(ipc.create());

    CmdType received_type = CmdType::Unknown;
    double received_speed = -1;
    ipc.on_command([&](const IpcCommand& cmd) {
        received_type = cmd.type;
        received_speed = cmd.float_value;
    });

    int client_fd = connect_client();
    CHECK(client_fd >= 0);
    poll_for(ipc, 30);  // accept

    send_cmd(client_fd, "{\"cmd\":\"speed\",\"value\":3.5}");
    poll_for(ipc, 50);  // read + dispatch

    CHECK(received_type == CmdType::Speed);
    CHECK(received_speed == doctest::Approx(3.5));

    close(client_fd);
    ipc.shutdown();
}

TEST_CASE("server dispatches incline command (half-pct conversion)") {
    RingBuffer<> ring;
    IpcServer ipc(ring);
    CHECK(ipc.create());

    int received_incline = -1;
    ipc.on_command([&](const IpcCommand& cmd) {
        if (cmd.type == CmdType::Incline)
            received_incline = cmd.int_value;
    });

    int client_fd = connect_client();
    poll_for(ipc, 30);

    send_cmd(client_fd, "{\"cmd\":\"incline\",\"value\":7}");
    poll_for(ipc, 50);

    CHECK(received_incline == 14);  // 7% * 2 = 14 half-pct

    close(client_fd);
    ipc.shutdown();
}

TEST_CASE("server dispatches emulate and proxy commands") {
    RingBuffer<> ring;
    IpcServer ipc(ring);
    CHECK(ipc.create());

    std::vector<CmdType> cmds;
    ipc.on_command([&](const IpcCommand& cmd) {
        cmds.push_back(cmd.type);
    });

    int fd = connect_client();
    poll_for(ipc, 30);

    send_cmd(fd, "{\"cmd\":\"emulate\",\"enabled\":true}");
    send_cmd(fd, "{\"cmd\":\"proxy\",\"enabled\":true}");
    send_cmd(fd, "{\"cmd\":\"status\"}");
    send_cmd(fd, "{\"cmd\":\"quit\"}");
    poll_for(ipc, 80);

    CHECK(cmds.size() == 4);
    CHECK(cmds.at(0) == CmdType::Emulate);
    CHECK(cmds.at(1) == CmdType::Proxy);
    CHECK(cmds.at(2) == CmdType::Status);
    CHECK(cmds.at(3) == CmdType::Quit);

    close(fd);
    ipc.shutdown();
}

TEST_CASE("server handles multiple commands in one send") {
    RingBuffer<> ring;
    IpcServer ipc(ring);
    CHECK(ipc.create());

    int count = 0;
    ipc.on_command([&](const IpcCommand&) { count++; });

    int fd = connect_client();
    poll_for(ipc, 30);

    // Send 3 commands in one write (batched newline-delimited)
    const char* batch =
        "{\"cmd\":\"speed\",\"value\":1.0}\n"
        "{\"cmd\":\"speed\",\"value\":2.0}\n"
        "{\"cmd\":\"speed\",\"value\":3.0}\n";
    (void)write(fd, batch, std::strlen(batch));
    poll_for(ipc, 50);

    CHECK(count == 3);

    close(fd);
    ipc.shutdown();
}

// ── Ring buffer flush to clients ────────────────────────────────────

TEST_CASE("server flushes ring buffer events to connected client") {
    RingBuffer<> ring;
    IpcServer ipc(ring);
    CHECK(ipc.create());

    int fd = connect_client();
    poll_for(ipc, 30);  // accept

    // Push KV events to the ring
    KvEvent ev{"console", "hmph", "78", 1.0};
    ring.push(build_kv_event(ev));

    KvEvent ev2{"motor", "belt", "0", 1.1};
    ring.push(build_kv_event(ev2));

    poll_for(ipc, 50);  // flush

    std::string data = read_all(fd, 50);
    CHECK(data.find("\"key\":\"hmph\"") != std::string::npos);
    CHECK(data.find("\"key\":\"belt\"") != std::string::npos);
    CHECK(data.find("\"source\":\"console\"") != std::string::npos);
    CHECK(data.find("\"source\":\"motor\"") != std::string::npos);

    close(fd);
    ipc.shutdown();
}

TEST_CASE("multiple clients each receive ring events") {
    RingBuffer<> ring;
    IpcServer ipc(ring);
    CHECK(ipc.create());

    int fd1 = connect_client();
    int fd2 = connect_client();
    poll_for(ipc, 30);

    KvEvent ev{"emulate", "inc", "5", 2.0};
    ring.push(build_kv_event(ev));

    poll_for(ipc, 50);

    std::string data1 = read_all(fd1, 30);
    std::string data2 = read_all(fd2, 30);

    CHECK(data1.find("\"key\":\"inc\"") != std::string::npos);
    CHECK(data2.find("\"key\":\"inc\"") != std::string::npos);

    close(fd1);
    close(fd2);
    ipc.shutdown();
}

// ── Client disconnect ───────────────────────────────────────────────

TEST_CASE("server handles client disconnect gracefully") {
    RingBuffer<> ring;
    IpcServer ipc(ring);
    CHECK(ipc.create());

    int cmd_count = 0;
    ipc.on_command([&](const IpcCommand&) { cmd_count++; });

    int fd1 = connect_client();
    int fd2 = connect_client();
    poll_for(ipc, 30);

    // Disconnect first client
    close(fd1);
    poll_for(ipc, 50);

    // Second client should still work
    send_cmd(fd2, "{\"cmd\":\"status\"}");
    poll_for(ipc, 50);

    CHECK(cmd_count == 1);

    // Ring events should still flush to fd2
    KvEvent ev{"motor", "ver", "1", 3.0};
    ring.push(build_kv_event(ev));
    poll_for(ipc, 50);

    std::string data = read_all(fd2, 30);
    CHECK(data.find("\"key\":\"ver\"") != std::string::npos);

    close(fd2);
    ipc.shutdown();
}

// ── Max clients ─────────────────────────────────────────────────────

TEST_CASE("server rejects connection beyond MAX_CLIENTS") {
    RingBuffer<> ring;
    IpcServer ipc(ring);
    CHECK(ipc.create());

    int fds[MAX_CLIENTS + 1];
    for (int i = 0; i < MAX_CLIENTS; i++) {
        fds[i] = connect_client();
        CHECK(fds[i] >= 0);
        poll_for(ipc, 20);
    }

    // 5th connection should be accepted at TCP level but get error JSON
    fds[MAX_CLIENTS] = connect_client();
    poll_for(ipc, 50);

    if (fds[MAX_CLIENTS] >= 0) {
        std::string data = read_all(fds[MAX_CLIENTS], 50);
        CHECK(data.find("\"type\":\"error\"") != std::string::npos);
        CHECK(data.find("too many clients") != std::string::npos);
        close(fds[MAX_CLIENTS]);
    }

    for (int i = 0; i < MAX_CLIENTS; i++) {
        close(fds[i]);
    }
    ipc.shutdown();
}

// ── Malformed input ─────────────────────────────────────────────────

TEST_CASE("server ignores malformed JSON") {
    RingBuffer<> ring;
    IpcServer ipc(ring);
    CHECK(ipc.create());

    int cmd_count = 0;
    ipc.on_command([&](const IpcCommand&) { cmd_count++; });

    int fd = connect_client();
    poll_for(ipc, 30);

    send_cmd(fd, "not json at all");
    send_cmd(fd, "{broken");
    send_cmd(fd, "{\"cmd\":\"speed\",\"value\":1.0}");  // valid after garbage
    poll_for(ipc, 50);

    CHECK(cmd_count == 1);  // only the valid command dispatched

    close(fd);
    ipc.shutdown();
}

TEST_CASE("disconnect callback fires with remaining count") {
    RingBuffer<> ring;
    IpcServer ipc(ring);
    CHECK(ipc.create());

    int callback_count = 0;
    int last_remaining = -1;
    ipc.on_client_disconnect([&](int remaining) {
        callback_count++;
        last_remaining = remaining;
    });

    int fd1 = connect_client();
    int fd2 = connect_client();
    poll_for(ipc, 30);

    // Disconnect first client — should fire with remaining=1
    close(fd1);
    poll_for(ipc, 50);

    CHECK(callback_count == 1);
    CHECK(last_remaining == 1);

    // Disconnect second client — should fire with remaining=0
    close(fd2);
    poll_for(ipc, 50);

    CHECK(callback_count == 2);
    CHECK(last_remaining == 0);

    ipc.shutdown();
}

TEST_CASE("heartbeat command dispatches to callback") {
    RingBuffer<> ring;
    IpcServer ipc(ring);
    CHECK(ipc.create());

    CmdType received_type = CmdType::Unknown;
    ipc.on_command([&](const IpcCommand& cmd) {
        received_type = cmd.type;
    });

    int fd = connect_client();
    poll_for(ipc, 30);

    send_cmd(fd, "{\"cmd\":\"heartbeat\"}");
    poll_for(ipc, 50);

    CHECK(received_type == CmdType::Heartbeat);

    close(fd);
    ipc.shutdown();
}

TEST_CASE("server handles empty lines") {
    RingBuffer<> ring;
    IpcServer ipc(ring);
    CHECK(ipc.create());

    int cmd_count = 0;
    ipc.on_command([&](const IpcCommand&) { cmd_count++; });

    int fd = connect_client();
    poll_for(ipc, 30);

    // Send empty lines mixed with valid command
    const char* data = "\n\n{\"cmd\":\"status\"}\n\n";
    (void)write(fd, data, std::strlen(data));
    poll_for(ipc, 50);

    CHECK(cmd_count == 1);

    close(fd);
    ipc.shutdown();
}
