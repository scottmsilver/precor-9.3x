/*
 * ws_hub.h — pure WebSocket client registry + broadcast fan-out.
 * The device transport (transport_httpd.cpp) adapts httpd socket fds to
 * FrameSink and marshals sends onto the httpd task; host tests use a
 * recording sink. Backpressure: the device side bounds its outbox queue
 * (drop-on-full); the hub itself is synchronous.
 *
 * Two properties the transport depends on:
 *
 *  (1) SESSION IDENTITY. httpd socket fds are reused: a client can
 *      close and a brand-new accept can land on the same fd before a
 *      queued hello reaches the httpd task. Every registration carries
 *      a monotonic session id, and the transport re-validates
 *      (fd, session) at send time so a stale hello can never be
 *      delivered to — or release the hold of — a different client.
 *
 *  (2) HELLO HOLD. A client is registered the instant its handshake
 *      completes (it must never be left connected-but-unregistered),
 *      but its first frames should be the ordered hello triple-send.
 *      add_client(..., hold) therefore holds back up to `hold`
 *      broadcasts; release_hold() clears it when the hello lands.
 *      The hold is a COUNT, not a flag, so a lost hello (dropped
 *      outbox item) can never strand the client silently — it starts
 *      receiving the stream a few frames later.
 */

#pragma once

#include <array>
#include <atomic>
#include <cstdint>
#include <string_view>

namespace esp32tap::api {

class FrameSink {
public:
    virtual ~FrameSink() = default;
    // false == client is dead (hub drops it).
    virtual bool send_text(int client_id, std::string_view json) = 0;
};

class WsHub {
public:
    static constexpr int MAX_CLIENTS = 2;  // PLAN RAM policy concurrency cap
    // Broadcasts a newly-registered client may miss while its hello is
    // in flight. Bounded so a lost hello degrades to "starts a few
    // frames late", never "never receives anything".
    static constexpr int DEFAULT_HELLO_HOLD = 3;

    void set_sink(FrameSink* sink) { sink_ = sink; }

    // false when the table is full (caller closes the new connection —
    // httpd LRU purge keeps this rare). `session` identifies this
    // connection for the life of the fd; `hold` holds back that many
    // broadcasts while the hello triple-send is in flight.
    bool add_client(int client_id, uint32_t session = 0, int hold = 0) {
        int n = count_.load(std::memory_order_relaxed);
        if (n >= MAX_CLIENTS) return false;
        auto& c = clients_.at(static_cast<size_t>(n));
        c.id = client_id;
        c.session = session;
        c.hold = hold;
        count_.store(n + 1, std::memory_order_relaxed);
        return true;
    }

    void remove_client(int client_id) {
        int n = count_.load(std::memory_order_relaxed);
        for (int i = 0; i < n; i++) {
            if (clients_.at(static_cast<size_t>(i)).id == client_id) {
                for (int j = i; j < n - 1; j++) {
                    clients_.at(static_cast<size_t>(j)) =
                        clients_.at(static_cast<size_t>(j + 1));
                }
                count_.store(n - 1, std::memory_order_relaxed);
                return;
            }
        }
    }

    bool has_client(int client_id) const {
        return find(client_id) != nullptr;
    }

    // 0 when the fd is not registered (sessions start at 1).
    uint32_t session_of(int client_id) const {
        const Client* c = find(client_id);
        return c == nullptr ? 0 : c->session;
    }

    // True only when the fd is registered AND still belongs to the same
    // connection — the fd-reuse guard for queued per-client sends.
    bool is_session(int client_id, uint32_t session) const {
        const Client* c = find(client_id);
        return c != nullptr && c->session == session;
    }

    // Visit every registered client id (httpd task only).
    template <typename Fn>
    void for_each_client(Fn&& fn) const {
        int n = count_.load(std::memory_order_relaxed);
        for (int i = 0; i < n; i++) {
            fn(clients_.at(static_cast<size_t>(i)).id);
        }
    }

    // Hello delivered (or known lost): let broadcasts through.
    void release_hold(int client_id) {
        Client* c = find_mut(client_id);
        if (c != nullptr) c->hold = 0;
    }

    // Safe to read from any thread (the executor's ws_send early-out
    // and dead-man sampler): count_ is atomic, so the cross-core read
    // is well-defined even though registrations happen on the httpd
    // task. The value may be momentarily stale — callers only use it
    // as a hint (skip work / grace-period timer), never for indexing.
    int client_count() const {
        return count_.load(std::memory_order_relaxed);
    }

    // Send to every client; dead clients are dropped (python
    // ConnectionManager.broadcast parity). Clients still holding for
    // their hello frames consume one hold credit instead.
    void broadcast(std::string_view json) {
        if (sink_ == nullptr) return;
        int i = 0;
        while (i < count_.load(std::memory_order_relaxed)) {
            Client& c = clients_.at(static_cast<size_t>(i));
            if (c.hold > 0) {
                c.hold--;
                i++;
                continue;
            }
            int id = c.id;
            if (sink_->send_text(id, json)) {
                i++;
            } else {
                remove_client(id);
            }
        }
    }

private:
    struct Client {
        int id = 0;
        uint32_t session = 0;
        int hold = 0;
    };

    const Client* find(int client_id) const {
        int n = count_.load(std::memory_order_relaxed);
        for (int i = 0; i < n; i++) {
            const Client& c = clients_.at(static_cast<size_t>(i));
            if (c.id == client_id) return &c;
        }
        return nullptr;
    }
    Client* find_mut(int client_id) {
        int n = count_.load(std::memory_order_relaxed);
        for (int i = 0; i < n; i++) {
            Client& c = clients_.at(static_cast<size_t>(i));
            if (c.id == client_id) return &c;
        }
        return nullptr;
    }

    FrameSink* sink_ = nullptr;
    std::array<Client, MAX_CLIENTS> clients_{};
    std::atomic<int> count_{0};
};

}  // namespace esp32tap::api
