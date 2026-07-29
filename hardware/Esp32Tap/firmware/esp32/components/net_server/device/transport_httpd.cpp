/*
 * transport_httpd.cpp — see transport_httpd.h.
 *
 * Threading: all handler code + queued work runs on the single httpd
 * worker task (core 1), so WsHub and the connection table need no lock;
 * the client count is atomic so the executor's cross-core reads
 * (ws_send early-out, dead-man sampler) are well-defined.
 *
 * WS fan-out (three tasks, one direction, no ownership in flight):
 *
 *   executor ──post()──▶ g_outbox ──drained by──▶ httpd task
 *                            ▲                       │
 *                            └── ws_pump wakes it ────┘
 *
 * The executor NEVER calls into lwIP (it is WDT-supervised: a blocking
 * tcpip round-trip inside a 250 ms slice is a stall risk), it only
 * takes a short mutex to hand the frame to api::WsOutbox — which is
 * bounded in BYTES and coalesces state snapshots (ws_outbox.h). A
 * dedicated ws_pump task turns "outbox non-empty" into
 * httpd_queue_work() wake-ups. The wake datagram carries NO pointer:
 * httpd_queue_work posts to the httpd control socket and lwIP silently
 * DROPS that datagram when the UDP receive mailbox is full
 * (CONFIG_LWIP_UDP_RECVMBOX_SIZE=6), so anything handed to it must be
 * disposable. Because the frames stay in the outbox (the sole owner) a
 * dropped wake can neither leak memory nor strand a counter — ws_pump
 * simply re-wakes 50 ms later.
 *
 * REQUEST-DURATION GUARD (g_conn + guarded_recv). esp_https_server runs
 * ONE worker task for every socket, serially: while it is parsing a
 * request the belt-stopping POST /api/program/stop on another socket is
 * not being served. IDF's only bound there is the per-recv SO_RCVTIMEO,
 * which is not a bound at all — a client dribbling one header byte just
 * inside each recv window never times out a single recv and holds the
 * worker for the whole header block (CONFIG_HTTPD_MAX_REQ_HDR_LEN x
 * recv_wait_timeout ~ minutes), then opens another connection. So every
 * session gets OUR recv function installed over IDF's (esp_https_server
 * calls user_cb AFTER httpd_sess_set_recv_override, and hands us the
 * esp_tls_t), and that function enforces a PHASE deadline: the header
 * block has HEADER_BUDGET_US from its first byte, the body phase has a
 * backstop above read_body()'s own budget, and an IDLE keep-alive
 * connection has no deadline at all (so pooled connections are not
 * churned). Over budget -> the recv fails -> httpd tears the session
 * down and the worker returns to select().
 *
 * RAM policy (PLAN): max_open_sockets 4 + LRU purge, WS clients capped
 * at 2 by WsHub, outbox bounded at WsOutbox::MAX_BYTES.
 */

#include "transport_httpd.h"

#include <unistd.h>

#include <array>
#include <cstdint>
#include <cstring>
#include <mutex>
#include <string_view>
#include <vector>

#include "freertos/task.h"
#include "freertos/semphr.h"

#include "esp_https_server.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_tls.h"

#include "api_call.h"
#include "ws_hub.h"
#include "ws_outbox.h"

namespace esp32tap::net {

namespace {

const char* TAG = "esp32tap";

httpd_handle_t g_server = nullptr;
QueueHandle_t g_api_queue = nullptr;
api::WsHub g_hub;
api::WsOutbox g_outbox;
std::mutex g_outbox_mu;
SemaphoreHandle_t g_outbox_wake = nullptr;
std::string g_cert_pem;
std::string g_key_pem;
// Monotonic per-connection id; only ever touched on the httpd task.
uint32_t g_ws_session_seq = 0;
#if defined(ESP32TAP_QEMU_TEST)
bool g_force_hello_drop = false;
#endif

constexpr size_t MAX_BODY = 8 * 1024;

// Endpoints whose bodies are multipart/binary by definition (a GPX
// route, an avatar image). They must reach the router — which answers
// 501 / the avatar stub — instead of being turned into the generic
// "body too large" 400 by the JSON body cap, because a real GPX route
// is tens of KB to megabytes and the app would then show the wrong
// message for every single upload.
constexpr size_t MAX_UPLOAD_DRAIN = 512 * 1024;

// Total wall-clock budget for receiving (or discarding) one request
// body, on top of the per-recv timeout (see the header comment).
constexpr int64_t BODY_BUDGET_US = 4'000'000;

// Header phase: from the first byte of a request to the end of the
// header block. Nothing legitimate needs a fraction of this; it is the
// bound that stops a header-dribbling client owning the single worker.
constexpr int64_t HEADER_BUDGET_US = 2'500'000;
// Body-phase backstop. read_body() bounds the body itself at
// BODY_BUDGET_US; this only catches a read that is not ours.
constexpr int64_t BODY_PHASE_BUDGET_US = 8'000'000;

// Scratch used to discard a body without allocating it.
constexpr size_t DISCARD_CHUNK = 512;

// --- per-connection state (httpd task only) -----------------------------

enum class Phase { IDLE, HEADERS, BODY };

struct Conn {
    int fd = -1;
    esp_tls_t* tls = nullptr;
    Phase phase = Phase::IDLE;
    int64_t phase_start_us = 0;
    bool is_ws = false;  // WS sessions legitimately sit idle forever
};

// max_open_sockets + slack for a session being torn down.
constexpr int MAX_CONNS = 8;
std::array<Conn, MAX_CONNS> g_conn{};

Conn* conn_find(int fd) {
    if (fd < 0) return nullptr;
    for (auto& c : g_conn) {
        if (c.fd == fd) return &c;
    }
    return nullptr;
}

Conn* conn_add(int fd, esp_tls_t* tls) {
    for (auto& c : g_conn) {
        if (c.fd < 0) {
            c = Conn{};
            c.fd = fd;
            c.tls = tls;
            c.phase = Phase::IDLE;
            return &c;
        }
    }
    return nullptr;
}

void conn_remove(int fd) {
    Conn* c = conn_find(fd);
    if (c != nullptr) *c = Conn{};
}

void conn_set_phase(int fd, Phase p) {
    Conn* c = conn_find(fd);
    if (c == nullptr) return;
    c->phase = p;
    c->phase_start_us = esp_timer_get_time();
}

int64_t phase_budget(Phase p) {
    switch (p) {
        case Phase::HEADERS: return HEADER_BUDGET_US;
        case Phase::BODY: return BODY_PHASE_BUDGET_US;
        default: return 0;  // IDLE: keep-alive, no deadline
    }
}

// Our recv, installed over esp_https_server's for every session. Runs
// on the httpd task inside httpd_parse_req / httpd_req_recv.
int guarded_recv(httpd_handle_t hd, int sockfd, char* buf, size_t buf_len,
                 int flags) {
    (void)hd;
    (void)flags;
    Conn* c = conn_find(sockfd);
    if (c == nullptr || c->tls == nullptr) return -1;
    if (!c->is_ws) {
        int64_t now = esp_timer_get_time();
        if (c->phase == Phase::IDLE) {
            // First byte of a new request on this (possibly long-idle
            // keep-alive) socket: the header clock starts HERE, not at
            // accept, so pooled idle connections are never reaped.
            c->phase = Phase::HEADERS;
            c->phase_start_us = now;
        }
        int64_t budget = phase_budget(c->phase);
        if (budget > 0 && now - c->phase_start_us > budget) {
            ESP_LOGW(TAG, "request over budget (phase %d) fd=%d — closing",
                     static_cast<int>(c->phase), sockfd);
            return -1;  // httpd tears the session down
        }
    }
    return esp_tls_conn_read(c->tls, buf, buf_len);
}

void ssl_user_cb(esp_https_server_user_cb_arg_t* arg) {
    if (arg == nullptr || arg->tls == nullptr) return;
    int fd = -1;
    if (esp_tls_get_conn_sockfd(arg->tls, &fd) != ESP_OK || fd < 0) {
        ESP_LOGE(TAG, "no sockfd for TLS session — request guard not armed");
        return;
    }
    if (arg->user_cb_state == HTTPD_SSL_USER_CB_SESS_CREATE) {
        conn_remove(fd);  // stale entry from a reused fd
        if (g_server == nullptr) {
            // Only reachable if a connection lands between httpd_start()
            // and start_httpd() recording the handle. Fail loud rather
            // than silently serving one unguarded session.
            ESP_LOGE(TAG, "server handle not ready — guard not armed fd=%d",
                     fd);
            return;
        }
        if (conn_add(fd, arg->tls) == nullptr) {
            ESP_LOGE(TAG, "conn table full fd=%d", fd);
            return;
        }
        // Installed AFTER esp_https_server set its own (it calls user_cb
        // at the end of httpd_ssl_open) — ours wraps esp_tls_conn_read.
        httpd_sess_set_recv_override(g_server, fd, guarded_recv);
    } else if (arg->user_cb_state == HTTPD_SSL_USER_CB_SESS_CLOSE) {
        conn_remove(fd);
    }
}

class HttpdFrameSink : public api::FrameSink {
public:
    bool send_text(int client_id, std::string_view json) override {
        httpd_ws_frame_t frame = {};
        frame.type = HTTPD_WS_TYPE_TEXT;
        frame.payload =
            const_cast<uint8_t*>(reinterpret_cast<const uint8_t*>(json.data()));
        frame.len = json.size();
        return httpd_ws_send_frame_async(g_server, client_id, &frame) ==
               ESP_OK;
    }
};

HttpdFrameSink g_sink;

const char* status_line(int code) {
    switch (code) {
        case 200: return "200 OK";
        case 400: return "400 Bad Request";
        case 404: return "404 Not Found";
        case 408: return "408 Request Timeout";
        case 409: return "409 Conflict";
        case 413: return "413 Payload Too Large";
        case 422: return "422 Unprocessable Entity";
        case 501: return "501 Not Implemented";
        case 503: return "503 Service Unavailable";
        default: return "500 Internal Server Error";
    }
}

void send_json(httpd_req_t* req, int code, const char* body) {
    httpd_resp_set_status(req, status_line(code));
    httpd_resp_set_type(req, "application/json");
    httpd_resp_sendstr(req, body);
}

// Dispatch a call to the executor and wait. Ownership is refcounted
// (see api_call.h): NOT_QUEUED means the executor never saw the call,
// TIMED_OUT means it may still be in flight — neither leaks and neither
// can free an object the executor is about to write.
enum class Dispatch { OK, NOT_QUEUED, TIMED_OUT };

Dispatch dispatch(ApiCall* call) {
    call->done = xSemaphoreCreateBinary();
    if (call->done == nullptr) return Dispatch::NOT_QUEUED;
    if (xQueueSend(g_api_queue, &call, pdMS_TO_TICKS(100)) != pdTRUE) {
        return Dispatch::NOT_QUEUED;
    }
    // The executor is WDT-supervised: if it wedges, the device reboots.
    // A 5 s bound keeps httpd responsive in the interim.
    if (xSemaphoreTake(call->done, pdMS_TO_TICKS(5000)) != pdTRUE) {
        ESP_LOGE(TAG, "executor RPC timeout");
        return Dispatch::TIMED_OUT;
    }
    return Dispatch::OK;
}

const char* method_name(int m) {
    switch (m) {
        case HTTP_GET: return "GET";
        case HTTP_POST: return "POST";
        case HTTP_PUT: return "PUT";
        case HTTP_DELETE: return "DELETE";
        default: return "OTHER";
    }
}

bool ends_with(std::string_view s, std::string_view suffix) {
    return s.size() >= suffix.size() &&
           s.substr(s.size() - suffix.size()) == suffix;
}

bool is_upload_path(std::string_view path) {
    return path == "/api/gpx/upload" || ends_with(path, "/avatar");
}

enum class BodyResult { OK, ABORT };

// Read `remaining` bytes into `out` (nullptr == discard), bounded by
// BODY_BUDGET_US of wall clock. ABORT means the client did not deliver
// within budget (or died): the caller must stop touching the socket and
// return ESP_FAIL so httpd tears the session down instead of entering
// its own unbounded purge loop in httpd_req_delete().
BodyResult read_body(httpd_req_t* req, size_t remaining, std::string* out) {
    if (out != nullptr) out->resize(remaining);
    std::array<char, DISCARD_CHUNK> scratch{};
    const int64_t deadline = esp_timer_get_time() + BODY_BUDGET_US;
    size_t off = 0;
    while (off < remaining) {
        if (esp_timer_get_time() > deadline) return BodyResult::ABORT;
        size_t want = remaining - off;
        char* dst = nullptr;
        if (out != nullptr) {
            dst = out->data() + off;
        } else {
            if (want > scratch.size()) want = scratch.size();
            dst = scratch.data();
        }
        int n = httpd_req_recv(req, dst, want);
        if (n <= 0) return BodyResult::ABORT;
        off += static_cast<size_t>(n);
    }
    return BodyResult::OK;
}

esp_err_t api_handler(httpd_req_t* req) {
    int fd = httpd_req_to_sockfd(req);
    conn_set_phase(fd, Phase::BODY);
    auto* call = new ApiCall(2);  // handler ref + executor ref
    call->method = method_name(req->method);
    std::string_view uri(req->uri);
    auto q = uri.find('?');
    call->path.assign(uri.substr(0, q));

    size_t remaining = req->content_len;
    const bool upload = is_upload_path(call->path);
    if (remaining > (upload ? MAX_UPLOAD_DRAIN : MAX_BODY)) {
        // Discard the over-cap body FIRST: replying while the body is
        // still queued leaves httpd to purge it in httpd_req_delete()
        // (one recv_wait_timeout per chunk, unbounded in total) and, on
        // an abortive close, can cost the client the response entirely.
        BodyResult r = read_body(req, remaining, nullptr);
        ApiCall::release_unqueued(call);
        send_json(req, 400, "{\"error\":\"body too large\"}");
        conn_set_phase(fd, Phase::IDLE);
        return r == BodyResult::OK ? ESP_OK : ESP_FAIL;
    }
    if (upload) {
        // Drain (never store) and dispatch with an EMPTY body: these
        // handlers do not read it, and holding a multi-hundred-KB
        // multipart in RAM on a no-PSRAM part only to ignore it would
        // be the actual hazard.
        if (read_body(req, remaining, nullptr) != BodyResult::OK) {
            ApiCall::release_unqueued(call);
            send_json(req, 408, "{\"error\":\"request body timeout\"}");
            return ESP_FAIL;
        }
    } else if (read_body(req, remaining, &call->body) != BodyResult::OK) {
        ApiCall::release_unqueued(call);
        send_json(req, 408, "{\"error\":\"request body timeout\"}");
        return ESP_FAIL;  // drop the socket: the client is not delivering
    }

    Dispatch res = dispatch(call);
    if (res != Dispatch::OK) {
        if (res == Dispatch::TIMED_OUT) {
            ApiCall::release(call);  // executor still holds its ref
        } else {
            ApiCall::release_unqueued(call);
        }
        send_json(req, 503, "{\"error\":\"treadmill_io disconnected\"}");
        conn_set_phase(fd, Phase::IDLE);
        return ESP_OK;
    }
    httpd_resp_set_status(req, status_line(call->resp.status));
    httpd_resp_set_type(req, "application/json");
    httpd_resp_send(req, call->resp.body.c_str(),
                    static_cast<ssize_t>(call->resp.body.size()));
    ApiCall::release(call);
    conn_set_phase(fd, Phase::IDLE);
    return ESP_OK;
}

// Runs on the httpd task right after the 101 handshake (IDF 5.5 does
// not invoke the URI handler for the handshake GET).
//
// The client is registered HERE, unconditionally — the 101 has already
// gone out, so an unregistered client would sit "connected" forever
// receiving nothing, never reconnecting (the app only reconnects on
// onClosed/onFailure) until the executor's dead-man paused its program.
// Registration therefore may not depend on the hello frames arriving.
//
// The ordered triple-send (status, session-if-active, program-if-loaded
// — server.py /ws parity) is built by the executor, but the handshake
// NEVER blocks on the executor RPC: a slow executor would stall the
// single shared httpd worker, including POST /api/program/stop from
// other sockets. The call is fire-and-forget, and the hub holds back a
// few broadcasts (WsHub hello hold) so the hello still normally lands
// first.
esp_err_t ws_post_handshake(httpd_req_t* req) {
    int fd = httpd_req_to_sockfd(req);
    uint32_t session = ++g_ws_session_seq;
    Conn* c = conn_find(fd);
    if (c != nullptr) {
        c->is_ws = true;  // exempt from the request-duration guard
        c->phase = Phase::IDLE;
    }
    if (!g_hub.add_client(fd, session, api::WsHub::DEFAULT_HELLO_HOLD)) {
        ESP_LOGW(TAG, "ws: client table full, closing fd=%d", fd);
        return ESP_FAIL;  // httpd closes the socket
    }
    // The app never sends anything on /ws after the handshake (no
    // pingInterval, no writes), and IDF bumps a session's LRU counter
    // only from httpd_sess_process — i.e. per INBOUND request. Without
    // this the WS session's counter is frozen at its handshake value,
    // making it permanently the LOWEST and therefore the first victim
    // of httpd_sess_close_lru whenever the app's own request burst
    // needs a socket. Refreshed here and on every fan-out (pump_fn).
    httpd_sess_update_lru_counter(g_server, fd);
    auto* call = new ApiCall(1);  // fire-and-forget: executor-owned
    call->ws_connect = true;
    call->ws_fd = fd;
    call->ws_session = session;
    bool queued = false;
#if defined(ESP32TAP_QEMU_TEST)
    // Injected saturation: skip the enqueue entirely so the client
    // genuinely never receives hello frames.
    if (!g_force_hello_drop)
#endif
        queued = xQueueSend(g_api_queue, &call, 0) == pdTRUE;
    if (!queued) {
        // RPC queue saturated: no hello is coming, so stop holding back
        // the broadcast stream for this client.
        ApiCall::release(call);
        g_hub.release_hold(fd);
        ESP_LOGW(TAG, "ws: hello dropped (queue full) fd=%d", fd);
    }
    ESP_LOGI(TAG, "ws: client connected fd=%d", fd);
    return ESP_OK;
}

constexpr size_t MAX_WS_RX_FRAME = 1024;

esp_err_t ws_handler(httpd_req_t* req) {
    if (req->method == HTTP_GET) {
        // Handshake GET (not reached on IDF 5.5 — kept for
        // forward-compat with versions that do call the handler).
        return ESP_OK;
    }
    // handle_ws_control_frames=true means httpd's automatic PING->PONG
    // and CLOSE echo are disabled — we owe both replies ourselves
    // (OkHttp with pingInterval would otherwise drop the connection on
    // ping timeout).
    int fd = httpd_req_to_sockfd(req);
    httpd_ws_frame_t frame = {};
    if (httpd_ws_recv_frame(req, &frame, 0) != ESP_OK) return ESP_FAIL;
    if (frame.type == HTTPD_WS_TYPE_CLOSE) {
        g_hub.remove_client(fd);
        httpd_ws_frame_t out = {};
        out.type = HTTPD_WS_TYPE_CLOSE;
        httpd_ws_send_frame(req, &out);  // complete the close handshake
        return ESP_OK;
    }
    static uint8_t sink_buf[MAX_WS_RX_FRAME];
    if (frame.type == HTTPD_WS_TYPE_PING) {
        if (frame.len > 125) {  // RFC 6455: control payload cap
            g_hub.remove_client(fd);
            return ESP_FAIL;  // httpd tears the session down
        }
        if (frame.len > 0) {
            frame.payload = sink_buf;
            if (httpd_ws_recv_frame(req, &frame, frame.len) != ESP_OK) {
                return ESP_FAIL;
            }
        }
        httpd_ws_frame_t out = {};
        out.type = HTTPD_WS_TYPE_PONG;
        out.payload = frame.len > 0 ? sink_buf : nullptr;
        out.len = frame.len;
        return httpd_ws_send_frame(req, &out);
    }
    if (frame.len > MAX_WS_RX_FRAME) {
        // The app never sends data frames on /ws; an oversized frame is
        // hostile or a broken client. Leaving the payload unread would
        // desync the httpd WS parser — drop the connection instead.
        ESP_LOGW(TAG, "ws: oversized frame (%u B) fd=%d — closing",
                 static_cast<unsigned>(frame.len), fd);
        g_hub.remove_client(fd);
        return ESP_FAIL;
    }
    if (frame.len > 0) {
        // Drain the payload so the parser stays in sync; content ignored.
        frame.payload = sink_buf;
        if (httpd_ws_recv_frame(req, &frame, frame.len) != ESP_OK) {
            return ESP_FAIL;
        }
    }
    return ESP_OK;
}

void on_close(httpd_handle_t hd, int sockfd) {
    g_hub.remove_client(sockfd);
    conn_remove(sockfd);
    close(sockfd);
    (void)hd;
}

// Wake ws_pump so it re-queues pump_fn promptly.
void outbox_signal() {
    if (g_outbox_wake != nullptr) xSemaphoreGive(g_outbox_wake);
}

// Frames drained per httpd work callback. The worker also has to answer
// POST /api/program/stop, and every send to a client whose TCP window
// is full costs up to send_wait_timeout; draining the WHOLE outbox in
// one callback therefore put multi-second fan-out latency on the belt's
// stop path. Bounded here, with an immediate re-wake when more remains.
constexpr int PUMP_DRAIN_PER_CALL = 3;

// Runs on the httpd task.
void pump_fn(void* arg) {
    (void)arg;
    for (int i = 0; i < PUMP_DRAIN_PER_CALL; i++) {
        api::WsOutItem item;
        {
            std::lock_guard<std::mutex> lk(g_outbox_mu);
            if (!g_outbox.take(item)) break;
        }
        if (item.kind != api::WsKind::HELLO) {
            g_hub.broadcast(item.json);
            continue;
        }
        // fd-reuse guard: between the handshake and this callback the
        // socket may have closed and the fd been handed to a brand-new
        // accept. Only deliver to the exact connection that asked.
        if (!g_hub.is_session(item.fd, item.session)) continue;
        bool ok = true;
        for (const std::string& frame : item.frames) {
            httpd_ws_frame_t f = {};
            f.type = HTTPD_WS_TYPE_TEXT;
            f.payload = const_cast<uint8_t*>(
                reinterpret_cast<const uint8_t*>(frame.data()));
            f.len = frame.size();
            if (httpd_ws_send_frame_async(g_server, item.fd, &f) != ESP_OK) {
                ok = false;
                break;
            }
        }
        if (ok) {
            g_hub.release_hold(item.fd);
        } else {
            g_hub.remove_client(item.fd);  // client died mid-hello
        }
    }
    // Outbound traffic counts as "this session is in use" for the LRU
    // victim choice (see ws_post_handshake).
    g_hub.for_each_client([](int fd) {
        httpd_sess_update_lru_counter(g_server, fd);
    });
    bool more = false;
    {
        std::lock_guard<std::mutex> lk(g_outbox_mu);
        more = !g_outbox.empty();
    }
    if (more) outbox_signal();
}

// Turns "outbox non-empty" into httpd work. Blocks with zero CPU while
// the outbox is empty; re-checks every WS_PUMP_RETRY_MS so a wake
// datagram silently dropped by lwIP costs at most that much latency.
constexpr int WS_PUMP_RETRY_MS = 50;

void ws_pump_task(void* arg) {
    (void)arg;
    for (;;) {
        // Zero CPU while the outbox is empty (every post gives this).
        xSemaphoreTake(g_outbox_wake, portMAX_DELAY);
        for (;;) {
            bool pending = false;
            {
                std::lock_guard<std::mutex> lk(g_outbox_mu);
                pending = !g_outbox.empty();
            }
            if (!pending || g_server == nullptr) break;
            httpd_queue_work(g_server, pump_fn, nullptr);
            // Re-check after the retry interval: a wake datagram lwIP
            // dropped costs this much latency and nothing else.
            vTaskDelay(pdMS_TO_TICKS(WS_PUMP_RETRY_MS));
        }
    }
}

// Hand an item to the outbox. Non-blocking and lwIP-free: safe from the
// WDT-supervised executor.
void outbox_post(api::WsOutItem&& item) {
    if (g_outbox_wake == nullptr) return;
    {
        std::lock_guard<std::mutex> lk(g_outbox_mu);
        g_outbox.post(std::move(item));
    }
    outbox_signal();
}

}  // namespace

int ws_client_count() { return g_hub.client_count(); }

#if defined(ESP32TAP_QEMU_TEST)
void ws_test_force_hello_drop(bool on) { g_force_hello_drop = on; }
#endif

void ws_send_hello(int client_fd, uint32_t session,
                   std::vector<std::string>&& frames) {
    if (g_server == nullptr) return;
    api::WsOutItem item;
    item.kind = api::WsKind::HELLO;
    item.fd = client_fd;
    item.session = session;
    item.frames = std::move(frames);
    outbox_post(std::move(item));
}

void ws_send(std::string&& json, api::WsKind kind) {
    if (g_server == nullptr) return;
    if (g_hub.client_count() == 0) {
        // Cheap early-out; registry read is racy but only skips work.
        // Registration now happens at handshake time, so a client that
        // is up can never be missed by this test.
        return;
    }
    api::WsOutItem item;
    item.kind = kind;
    item.json = std::move(json);
    outbox_post(std::move(item));
}

bool start_httpd(const std::string& cert_pem, const std::string& key_pem,
                 QueueHandle_t api_queue) {
    g_api_queue = api_queue;
    g_cert_pem = cert_pem;
    g_key_pem = key_pem;
    g_hub.set_sink(&g_sink);
    for (auto& c : g_conn) c = Conn{};
    if (g_outbox_wake == nullptr) {
        g_outbox_wake = xSemaphoreCreateBinary();
        if (g_outbox_wake == nullptr) {
            ESP_LOGE(TAG, "ws outbox semaphore alloc failed");
            return false;
        }
    }

    httpd_ssl_config_t cfg = HTTPD_SSL_CONFIG_DEFAULT();
    cfg.port_secure = 8000;  // mDNS contract advertises 8000
    cfg.servercert = reinterpret_cast<const uint8_t*>(g_cert_pem.c_str());
    cfg.servercert_len = g_cert_pem.size() + 1;  // PEM: include NUL
    cfg.prvtkey_pem = reinterpret_cast<const uint8_t*>(g_key_pem.c_str());
    cfg.prvtkey_len = g_key_pem.size() + 1;
    // The TLS handshake also runs on the single worker: without an
    // explicit bound esp_tls falls back to 10 s, which is 10 s of
    // worker time a hostile client can buy for the price of a SYN.
    cfg.tls_handshake_timeout_ms = 3000;
    cfg.user_cb = ssl_user_cb;  // arms the request-duration guard
    cfg.httpd.core_id = 1;             // PLAN core pinning
    cfg.httpd.stack_size = 12 * 1024;  // TLS handshake headroom
    // The app's own emergencyStop fires POST /api/speed, /api/incline
    // and /api/program/stop from three concurrent coroutines, which
    // OkHttp puts on three separate connections — on top of the
    // WebSocket. At 3 sockets that burst forced an LRU purge and the
    // frozen-counter WS session was always the victim, so the app went
    // blind for a reconnect cycle at the exact moment the user hit
    // Stop. 4 covers the burst; the LRU refresh above covers the rest.
    cfg.httpd.max_open_sockets = 4;
    cfg.httpd.lru_purge_enable = true;
    cfg.httpd.max_uri_handlers = 8;
    cfg.httpd.uri_match_fn = httpd_uri_match_wildcard;
    cfg.httpd.close_fn = on_close;
    // Explicit socket timeouts: all handlers + WS fan-out share this
    // one worker task, so a non-reading client must not be able to
    // wedge it for the default 5 s per send (it would delay every
    // other request, including Stop). The per-recv timeout is only the
    // inner bound — guarded_recv bounds the request as a whole.
    cfg.httpd.recv_wait_timeout = 2;
    cfg.httpd.send_wait_timeout = 2;

    if (httpd_ssl_start(&g_server, &cfg) != ESP_OK) {
        ESP_LOGE(TAG, "https server start failed");
        g_server = nullptr;
        return false;
    }

    // Core 1 (PLAN pinning), prio 3: below the httpd worker so a wake
    // never preempts an in-progress send.
    xTaskCreatePinnedToCore(ws_pump_task, "ws_pump", 2560, nullptr, 3, nullptr,
                            1);

    static httpd_uri_t ws_uri = {};
    ws_uri.uri = "/ws";
    ws_uri.method = HTTP_GET;
    ws_uri.handler = ws_handler;
    ws_uri.is_websocket = true;
    ws_uri.handle_ws_control_frames = true;
    ws_uri.ws_post_handshake_cb = ws_post_handshake;
    esp_err_t reg = httpd_register_uri_handler(g_server, &ws_uri);
    if (reg != ESP_OK) {
        ESP_LOGE(TAG, "ws uri register failed: %d", static_cast<int>(reg));
    }

    auto register_catchall = [](httpd_method_t method) {
        httpd_uri_t uri = {};
        uri.uri = "/*";
        uri.method = method;
        uri.handler = api_handler;
        esp_err_t err = httpd_register_uri_handler(g_server, &uri);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "catchall register failed (method %d): %d",
                     static_cast<int>(method), static_cast<int>(err));
        }
    };
    register_catchall(HTTP_GET);
    register_catchall(HTTP_POST);
    register_catchall(HTTP_PUT);
    register_catchall(HTTP_DELETE);

    ESP_LOGI(TAG, "https server up on :8000 (TLS, WS, core 1)");
    return true;
}

}  // namespace esp32tap::net
