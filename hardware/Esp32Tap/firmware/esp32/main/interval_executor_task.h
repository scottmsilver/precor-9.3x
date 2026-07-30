#pragma once

#include "firmware_context.h"

namespace esp32tap {

// Interval executor task body (core 0, prio 5, 4 KB stack,
// WDT-subscribed). PLAN's normative task-WDT section names this task as
// supervised, so it exists — and trips the WDT if stalled — even though
// the executor body itself is deferred. TODO(M5): port ProgramState's
// 1 s tick loop (intervals/pause/skip/extend) with push-down-then-mirror
// and checkpoint buffering.
void interval_executor_task(void* arg);  // arg = FirmwareContext*

}  // namespace esp32tap
