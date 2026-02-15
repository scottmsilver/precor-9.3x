/*
 * ipc_server.cpp — Unix domain socket IPC server implementation
 */

#include "ipc_server.h"
#include <cstdio>
#include <cerrno>
#include <unistd.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/select.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <algorithm>

IpcServer::IpcServer(RingBuffer<>& ring) : ring_(ring) {}

IpcServer::~IpcServer() {
    shutdown();
}

bool IpcServer::create() {
    unlink(SOCK_PATH);

    server_fd_ = socket(AF_UNIX, SOCK_STREAM, 0);
    if (server_fd_ < 0) {
        std::perror("socket");
        return false;
    }

    struct sockaddr_un addr{};
    addr.sun_family = AF_UNIX;
    // sockaddr_un.sun_path is a C array — this is the POSIX socket boundary
    std::string_view path(SOCK_PATH);
    auto copy_len = std::min(path.size(), sizeof(addr.sun_path) - 1);
    path.copy(addr.sun_path, copy_len);
    addr.sun_path[copy_len] = '\0';

    if (bind(server_fd_, reinterpret_cast<struct sockaddr*>(&addr), sizeof(addr)) < 0) {
        std::perror("bind");
        close(server_fd_);
        server_fd_ = -1;
        return false;
    }

    chmod(SOCK_PATH, 0777);

    if (listen(server_fd_, MAX_CLIENTS) < 0) {
        std::perror("listen");
        close(server_fd_);
        server_fd_ = -1;
        return false;
    }

    int flags = fcntl(server_fd_, F_GETFL, 0);
    fcntl(server_fd_, F_SETFL, flags | O_NONBLOCK);

    return true;
}

void IpcServer::accept_client() {
    int cfd = accept(server_fd_, nullptr, nullptr);
    if (cfd < 0) return;

    if (num_clients_ >= MAX_CLIENTS) {
        auto errmsg = build_error_event("too many clients");
        ssize_t wr = write(cfd, errmsg.data(), errmsg.size());
        (void)wr;  // best-effort error message
        close(cfd);
        return;
    }

    int flags = fcntl(cfd, F_GETFL, 0);
    fcntl(cfd, F_SETFL, flags | O_NONBLOCK);

    auto& c = clients_.at(num_clients_);
    c.fd = cfd;
    c.buf_len = 0;
    auto snap = ring_.snapshot();
    c.ring_cursor = snap.count;
    num_clients_++;

    std::fprintf(stderr, "[ipc] client connected (fd=%d, total=%d)\n", cfd, num_clients_);
}

void IpcServer::remove_client(int idx) {
    std::fprintf(stderr, "[ipc] client removed (fd=%d, remaining=%d)\n",
                 clients_.at(idx).fd, num_clients_ - 1);
    close(clients_.at(idx).fd);
    for (int i = idx; i < num_clients_ - 1; i++) {
        clients_.at(i) = clients_.at(i + 1);
    }
    num_clients_--;

    if (disconnect_cb_) {
        disconnect_cb_(num_clients_);
    }
}

void IpcServer::read_client(int idx) {
    auto& c = clients_.at(idx);
    int space = CMD_BUF_SIZE - c.buf_len - 1;
    if (space <= 0) {
        c.buf_len = 0;
        space = CMD_BUF_SIZE - 1;
    }

    ssize_t n = read(c.fd, c.buf.data() + c.buf_len, space);
    if (n <= 0) {
        std::fprintf(stderr, "[ipc] client disconnected (fd=%d)\n", c.fd);
        remove_client(idx);
        return;
    }

    c.buf_len += static_cast<int>(n);
    c.buf.at(c.buf_len) = '\0';

    // Process complete newline-delimited JSON commands
    std::string_view buf_view(c.buf.data(), c.buf_len);
    size_t processed = 0;

    while (true) {
        auto nl_pos = buf_view.find('\n', processed);
        if (nl_pos == std::string_view::npos) break;

        auto line = buf_view.substr(processed, nl_pos - processed);
        processed = nl_pos + 1;

        if (!line.empty() && cmd_cb_) {
            if (auto cmd = parse_command(line)) {
                cmd_cb_(*cmd);
            }
        }
    }

    // Shift unprocessed data to front (dst < src, std::copy is safe)
    int remaining = c.buf_len - static_cast<int>(processed);
    if (remaining > 0 && processed > 0) {
        std::copy(c.buf.data() + processed,
                  c.buf.data() + processed + remaining,
                  c.buf.data());
    }
    c.buf_len = remaining;
}

void IpcServer::flush_ring_to_clients() {
    auto snap = ring_.snapshot();
    int head = snap.head;
    unsigned int total = snap.count;
    constexpr int RING_SZ = RingBuffer<>::size();

    for (int ci = 0; ci < num_clients_; ) {
        auto& c = clients_.at(ci);

        unsigned int pending = total - c.ring_cursor;
        if (pending == 0) { ci++; continue; }
        if (pending > static_cast<unsigned>(RING_SZ)) {
            c.ring_cursor = total - RING_SZ;
            pending = RING_SZ;
        }

        int start_idx = (head - static_cast<int>(pending) + RING_SZ) % RING_SZ;
        bool failed = false;

        for (unsigned int i = 0; i < pending && !failed; i++) {
            int ri = (start_idx + static_cast<int>(i)) % RING_SZ;
            auto msg = ring_.at(ri);
            if (!msg.empty()) {
                ssize_t w = write(c.fd, msg.data(), msg.size());
                if (w < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
                    break;
                } else if (w <= 0) {
                    failed = true;
                }
            }
        }

        if (failed) {
            std::fprintf(stderr, "[ipc] client write error (fd=%d)\n", c.fd);
            remove_client(ci);
        } else {
            c.ring_cursor = total;
            ci++;
        }
    }
}

void IpcServer::poll() {
    if (server_fd_ < 0) return;

    fd_set rfds;
    FD_ZERO(&rfds);
    FD_SET(server_fd_, &rfds);
    int maxfd = server_fd_;

    for (int i = 0; i < num_clients_; i++) {
        FD_SET(clients_.at(i).fd, &rfds);
        if (clients_.at(i).fd > maxfd) maxfd = clients_.at(i).fd;
    }

    struct timeval tv = { 0, 20000 };  // 20ms poll interval
    int sel = select(maxfd + 1, &rfds, nullptr, nullptr, &tv);

    if (sel > 0) {
        if (FD_ISSET(server_fd_, &rfds)) {
            accept_client();
        }

        for (int i = 0; i < num_clients_; ) {
            if (FD_ISSET(clients_.at(i).fd, &rfds)) {
                int prev = num_clients_;
                read_client(i);
                if (num_clients_ < prev) {
                    continue;  // client removed
                }
            }
            i++;
        }
    }

    flush_ring_to_clients();
}

void IpcServer::push_to_ring(std::string_view msg) {
    ring_.push(msg);
}

void IpcServer::shutdown() {
    for (int i = 0; i < num_clients_; i++) {
        close(clients_.at(i).fd);
    }
    num_clients_ = 0;

    if (server_fd_ >= 0) {
        close(server_fd_);
        unlink(SOCK_PATH);
        server_fd_ = -1;
    }
}
