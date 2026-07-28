/*
 * probe_alloc.h — allocation interposer for the request-heap sizing
 * study. NOT firmware. Host-only measurement tool.
 *
 * Every dynamic allocation the native server tier makes goes through
 * exactly two doors:
 *   1. global operator new/delete   (std::string, std::vector, ApiCall)
 *   2. rapidjson's CrtAllocator     (the DOM, the parse stack, the
 *      StringBuffer and the Writer level stack — the shim rewrites
 *      CrtAllocator's three bodies to land here, so nothing in
 *      rapidjson can reach libc malloc behind our back)
 * Both route into probe::alloc/dealloc/re_alloc below, which keep:
 *   - cumulative bytes requested (what a BUMP ARENA must cover)
 *   - peak simultaneously-live bytes (what a REAL allocator must cover)
 *
 * Backend is per block (recorded in the header): PLAIN = libc malloc,
 * MH = a real ESP-IDF multi_heap region. That lets a request run
 * against a fixed-size region while the stores it reads stay in the
 * global heap — which is exactly the shape of the proposed design.
 */

#pragma once

#include <cstddef>
#include <cstdint>

namespace probe {

struct Result {
    size_t cumulative;   // bytes requested during the window
    size_t peak_live;    // max live-above-baseline during the window
    size_t residual;     // live at close - live at open (store growth)
    size_t n_alloc, n_free, n_realloc;
    size_t peak_blocks;  // above baseline
    size_t max_block;
    size_t hist[24];     // power-of-two size buckets
};

struct Window {
    size_t live0, cum0, alloc0, free0, realloc0, blocks0;
};

void* alloc(size_t n);
void dealloc(void* p);
void* re_alloc(void* p, size_t n);

Window win_open();
Result win_close(const Window& w);

// Switch the backend used for NEW allocations. Blocks remember their
// own backend, so mixing is safe.
void use_multi_heap(void* region, size_t bytes);
void use_plain();
bool oom_seen();
void clear_oom();

size_t live_bytes();

}  // namespace probe
