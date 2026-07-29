/*
 * api_call.h — the RPC envelope between the httpd worker (core 1) and
 * the interval executor (core 0, single writer). One heap ApiCall per
 * in-flight request; the handler blocks on `done`.
 *
 * OWNERSHIP (why this is refcounted): the handler's wait on `done` is
 * bounded (5 s) so a wedged executor cannot pin the single shared httpd
 * worker. A timeout therefore creates two live references to the same
 * call — the handler is giving up while the executor may still be about
 * to write `resp` and give `done`. Releasing the call from either side
 * alone would be a leak (handler walks away) or a write-after-free
 * (handler frees, executor writes). An atomic refcount makes the LAST
 * releaser the deleter, so a timeout can do neither: it drops one ref
 * and the executor's later write lands in a still-live object.
 *
 * Refcount contract:
 *   REST call  : starts at 2 (handler + executor). Handler releases
 *                after reading resp OR on timeout; executor releases
 *                after giving `done`.
 *   Not queued : starts at 2 but the executor never got it — the
 *                handler releases twice (release_unqueued()).
 *   ws_connect : starts at 1, owned solely by the executor.
 */

#pragma once

#include <atomic>
#include <cstdint>
#include <string>
#include <vector>

#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

#include "server_core.h"

namespace esp32tap::net {

struct ApiCall {
    bool ws_connect = false;  // true: fill `frames` instead of `resp`
    int ws_fd = -1;           // ws_connect only: target client socket
    uint32_t ws_session = 0;  // ws_connect only: fd-reuse guard
    std::string method;
    std::string path;
    std::string body;
    api::ApiResponse resp;
    std::vector<std::string> frames;
    // REST calls only: signalled by the executor once `resp` is set.
    SemaphoreHandle_t done = nullptr;

    explicit ApiCall(int initial_refs = 1) : refs(initial_refs) {}

    ~ApiCall() {
        if (done != nullptr) vSemaphoreDelete(done);
    }

    // Drop one reference; deletes when the last one goes away.
    static void release(ApiCall* call) {
        if (call == nullptr) return;
        if (call->refs.fetch_sub(1, std::memory_order_acq_rel) == 1) {
            delete call;
        }
    }

    // The executor never saw this call: drop the reference reserved for
    // it as well as the caller's.
    static void release_unqueued(ApiCall* call) {
        if (call == nullptr) return;
        release(call);
        release(call);
    }

    std::atomic<int> refs;
};

}  // namespace esp32tap::net
