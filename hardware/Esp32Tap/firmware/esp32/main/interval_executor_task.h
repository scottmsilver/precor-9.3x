#pragma once

#include "firmware_context.h"

namespace esp32tap {

struct ServerContext;

// Interval executor task body (core 0, prio 5, 8 KB stack,
// WDT-subscribed): the on-device standing owner (EXECUTOR lease). Runs
// the ProgramState/WorkoutSession 1 s tick, serves the API RPC queue
// (single-writer rule), and surfaces auto-proxy arbitration to the app.
void interval_executor_task(void* arg);  // arg = FirmwareContext*

// Wire the server context before the task starts (app_main).
void set_executor_server_context(ServerContext* sctx);

}  // namespace esp32tap
