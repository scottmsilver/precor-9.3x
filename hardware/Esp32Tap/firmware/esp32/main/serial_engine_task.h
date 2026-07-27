#pragma once

#include "firmware_context.h"

namespace esp32tap {

// Serial engine task body (core 0, prio 10, 8 KB stack, WDT-subscribed):
// drains both UART taps every <=5 ms, feeds the KV parser, updates the
// safety controller (console freshness, relay feedback, TREAD_OK, gap
// detection) and drives ModeStateMachine auto-transitions.
void serial_engine_task(void* arg);  // arg = FirmwareContext*

}  // namespace esp32tap
