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
#include <functional>
#include "ipc_protocol.h"
#include "ring_buffer.h"

constexpr int MAX_CLIENTS = 4;
constexpr int CMD_BUF_SIZE = 1024;
constexpr const char* SOCK_PATH = "/tmp/treadmill_io.sock";

class IpcServer {
public:
    using CommandCallback = std::function<void(const IpcCommand&)>;

    IpcServer(RingBuffer<>& ring);
    ~IpcServer();

    // Set handler for parsed commands
    void on_command(CommandCallback cb) { cmd_cb_ = std::move(cb); }

    // Create and bind the server socket. Returns true on success.
    bool create();

    // Run one iteration of the event loop (select + read + flush).
    // Call this in a loop from the IPC thread.
    void poll();

    // Push a status event into the ring (convenience for triggering status updates)
    void push_to_ring(const char* msg);

    // Cleanup
    void shutdown();

private:
    struct Client {
        int fd = -1;
        char buf[CMD_BUF_SIZE]{};
        int buf_len = 0;
        unsigned int ring_cursor = 0;
    };

    void accept_client();
    void read_client(int idx);
    void remove_client(int idx);
    void flush_ring_to_clients();

    RingBuffer<>& ring_;
    int server_fd_ = -1;
    Client clients_[MAX_CLIENTS]{};
    int num_clients_ = 0;
    CommandCallback cmd_cb_;
};
