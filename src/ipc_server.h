/*
 * ipc_server.h — Unix domain socket IPC server
 *
 * Manages up to MAX_CLIENTS connections, reads JSON commands,
 * dispatches to typed handlers, and drains ring buffer to clients.
 * No string parsing lives here — delegates entirely to IpcProtocol.
 *
 * RAII: closes all fds and unlinks socket on destruction.
 */

#pragma once

#include <cstdint>
#include <string>
#include <string_view>
#include <array>
#include <functional>
#include "ipc_protocol.h"
#include "ring_buffer.h"

constexpr int MAX_CLIENTS = 4;
constexpr int CMD_BUF_SIZE = 1024;
constexpr const char* SOCK_PATH = "/tmp/treadmill_io.sock";

class IpcServer {
public:
    using CommandCallback = std::function<void(const IpcCommand&)>;
    using DisconnectCallback = std::function<void(int remaining_clients)>;

    IpcServer(RingBuffer<>& ring);
    ~IpcServer();

    // Set handler for parsed commands
    void on_command(CommandCallback cb) { cmd_cb_ = std::move(cb); }

    // Set handler for client disconnects
    void on_client_disconnect(DisconnectCallback cb) { disconnect_cb_ = std::move(cb); }

    // Create and bind the server socket. Returns true on success.
    bool create();

    // Run one iteration of the event loop (select + read + flush).
    // Call this in a loop from the IPC thread.
    void poll();

    // Push a message into the ring
    void push_to_ring(std::string_view msg);

    // Cleanup
    void shutdown();

private:
    struct Client {
        int fd = -1;
        std::array<char, CMD_BUF_SIZE> buf{};
        int buf_len = 0;
        unsigned int ring_cursor = 0;
    };

    void accept_client();
    void read_client(int idx);
    void remove_client(int idx);
    void flush_ring_to_clients();

    RingBuffer<>& ring_;
    int server_fd_ = -1;
    std::array<Client, MAX_CLIENTS> clients_{};
    int num_clients_ = 0;
    CommandCallback cmd_cb_;
    DisconnectCallback disconnect_cb_;
};
