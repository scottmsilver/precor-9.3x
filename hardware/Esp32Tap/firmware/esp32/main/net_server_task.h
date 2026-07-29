#pragma once

#include "server_context.h"

namespace esp32tap {

// Core-1 net bring-up (prio 4, 8 KB stack, NOT WDT-supervised — a
// wedged TLS stack degrades the API, never the belt):
//   /data mount -> stores init -> boot recovery -> ready flag ->
//   netif -> TLS identity -> esp_https_server(:8000) -> mDNS.
// Also starts the core-1 storage task that drains the persist queue.
void start_net_server_task(ServerContext* sctx);

}  // namespace esp32tap
