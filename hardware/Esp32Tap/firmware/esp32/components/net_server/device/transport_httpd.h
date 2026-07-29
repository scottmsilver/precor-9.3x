/*
 * transport_httpd.h — esp_https_server binding: REST catch-all + /ws.
 * The ONLY file talking to esp_http_server/esp_https_server.
 */

#pragma once

#include <cstdint>
#include <string>
#include <vector>

#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"

#include "ws_outbox.h"

namespace esp32tap::net {

// Start the TLS server on port 8000 (the mDNS contract port). cert/key
// PEM strings must outlive the server (copied by start_httpd).
bool start_httpd(const std::string& cert_pem, const std::string& key_pem,
                 QueueHandle_t api_queue);

// Queue a JSON text frame to all connected WS clients. Callable from
// any task INCLUDING the WDT-supervised executor: it only takes a
// short mutex to hand the frame to the bounded outbox (never lwIP,
// never a blocking call). `kind` lets the outbox coalesce whole-state
// snapshots (a newer status/session/program supersedes a queued older
// one) and evict incremental frames first — see ws_outbox.h.
void ws_send(std::string&& json, api::WsKind kind = api::WsKind::OTHER);

// Deliver the on-connect hello frames to one client. The client is
// already registered (registration happens at handshake time so it can
// never be lost); the hub holds back a few broadcasts so these frames
// still normally arrive first. `session` guards against the fd having
// been closed and reused by a different connection in the meantime.
// Called by the executor for ws_connect ApiCalls; same non-blocking
// outbox path as ws_send.
void ws_send_hello(int client_fd, uint32_t session,
                   std::vector<std::string>&& frames);

// Current registered WS client count (atomic; callable from any task).
// 0 until the server is up.
int ws_client_count();

#if defined(ESP32TAP_QEMU_TEST)
// Fault injection, QEMU test image ONLY (not compiled into production —
// the S6 strings gate asserts the test surface is absent there).
// Forces the "hello could not be queued" branch of the WS handshake so
// the harness can prove a client that never receives its hello frames
// is still registered and still gets the broadcast stream.
void ws_test_force_hello_drop(bool on);
#endif

}  // namespace esp32tap::net
