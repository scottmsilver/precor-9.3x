#pragma once

#include "firmware_context.h"

namespace esp32tap {

// Emulate cycle task body (core 0, prio 9, 6 KB stack, WDT-subscribed):
// when the safety controller is EMULATING, sends one 14-key-cycle burst
// per 100 ms via the mutex-guarded UART writer; owns the 3-hour timeout.
void emulate_cycle_task(void* arg);  // arg = FirmwareContext*

}  // namespace esp32tap
