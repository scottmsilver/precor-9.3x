/* probe_alloc.cpp — see probe_alloc.h. Host-only measurement tool. */

#include "probe_alloc.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <new>

extern "C" {
#include "multi_heap.h"
}

namespace probe {
namespace {

constexpr uint32_t MAGIC_PLAIN = 0x50524f42u;  // 'PROB'
constexpr uint32_t MAGIC_MH = 0x50524f4du;     // 'PROM'

struct Hdr {
    size_t size;
    uint32_t magic;
    uint32_t pad;
};
constexpr size_t HDR = (sizeof(Hdr) + 15u) & ~size_t(15);

struct State {
    size_t live = 0;
    size_t peak = 0;
    size_t cum = 0;
    size_t n_alloc = 0, n_free = 0, n_realloc = 0;
    size_t live_blocks = 0, peak_blocks = 0;
    size_t max_block = 0;
    size_t hist[24] = {};
    bool use_mh = false;
    multi_heap_handle_t mh = nullptr;
    bool oom = false;
};

State& st() {
    static State s;
    return s;
}

void note_size(size_t n) {
    State& s = st();
    if (n > s.max_block) s.max_block = n;
    size_t b = 0, v = n;
    while (v > 1 && b < 23) {
        v >>= 1;
        b++;
    }
    s.hist[b]++;
}

}  // namespace

size_t live_bytes() { return st().live; }
bool oom_seen() { return st().oom; }
void clear_oom() { st().oom = false; }

void use_multi_heap(void* region, size_t bytes) {
    State& s = st();
    s.mh = multi_heap_register(region, bytes);
    s.use_mh = (s.mh != nullptr);
    if (s.mh == nullptr) s.oom = true;
}
void use_plain() { st().use_mh = false; }

void* alloc(size_t n) {
    State& s = st();
    void* raw = nullptr;
    uint32_t magic = MAGIC_PLAIN;
    if (s.use_mh) {
        raw = multi_heap_malloc(s.mh, n + HDR);
        magic = MAGIC_MH;
    } else {
        raw = std::malloc(n + HDR);
    }
    if (raw == nullptr) {
        s.oom = true;
        return nullptr;
    }
    Hdr* h = static_cast<Hdr*>(raw);
    h->size = n;
    h->magic = magic;
    s.live += n;
    s.cum += n;
    s.n_alloc++;
    s.live_blocks++;
    if (s.live > s.peak) s.peak = s.live;
    if (s.live_blocks > s.peak_blocks) s.peak_blocks = s.live_blocks;
    note_size(n);
    return static_cast<char*>(raw) + HDR;
}

void dealloc(void* p) {
    if (p == nullptr) return;
    State& s = st();
    Hdr* h = reinterpret_cast<Hdr*>(static_cast<char*>(p) - HDR);
    if (h->magic != MAGIC_PLAIN && h->magic != MAGIC_MH) {
        std::fprintf(stderr, "probe: free of foreign pointer\n");
        std::abort();
    }
    s.live -= h->size;
    s.live_blocks--;
    s.n_free++;
    bool mh = (h->magic == MAGIC_MH);
    h->magic = 0;
    if (mh) {
        multi_heap_free(s.mh, h);
    } else {
        std::free(h);
    }
}

void* re_alloc(void* p, size_t n) {
    if (p == nullptr) return alloc(n);
    State& s = st();
    Hdr* h = reinterpret_cast<Hdr*>(static_cast<char*>(p) - HDR);
    if (h->magic != MAGIC_PLAIN && h->magic != MAGIC_MH) std::abort();
    size_t old = h->size;
    void* raw = nullptr;
    if (h->magic == MAGIC_MH) {
        raw = multi_heap_realloc(s.mh, h, n + HDR);
    } else {
        raw = std::realloc(h, n + HDR);
    }
    if (raw == nullptr) {
        s.oom = true;
        return nullptr;
    }
    Hdr* nh = static_cast<Hdr*>(raw);
    nh->size = n;
    s.live = s.live - old + n;
    if (n > old) s.cum += (n - old);
    s.n_realloc++;
    if (s.live > s.peak) s.peak = s.live;
    note_size(n);
    return static_cast<char*>(raw) + HDR;
}

Window win_open() {
    State& s = st();
    Window w{s.live, s.cum, s.n_alloc, s.n_free, s.n_realloc, s.live_blocks};
    s.peak = s.live;
    s.max_block = 0;
    s.peak_blocks = s.live_blocks;
    std::memset(s.hist, 0, sizeof(s.hist));
    return w;
}

Result win_close(const Window& w) {
    State& s = st();
    Result r{};
    r.cumulative = s.cum - w.cum0;
    r.peak_live = s.peak - w.live0;
    r.residual = s.live - w.live0;
    r.n_alloc = s.n_alloc - w.alloc0;
    r.n_free = s.n_free - w.free0;
    r.n_realloc = s.n_realloc - w.realloc0;
    r.peak_blocks = s.peak_blocks - w.blocks0;
    r.max_block = s.max_block;
    std::memcpy(r.hist, s.hist, sizeof(r.hist));
    return r;
}

}  // namespace probe

// ---- door 1: global operator new/delete --------------------------------
void* operator new(size_t n) {
    void* p = probe::alloc(n);
    if (p == nullptr) {
        // -fno-exceptions firmware behaviour: a failed operator new
        // calls abort(). Reproduce it so a region-too-small trial dies
        // exactly the way the device would.
        std::abort();
    }
    return p;
}
void* operator new[](size_t n) { return operator new(n); }
void* operator new(size_t n, const std::nothrow_t&) noexcept {
    return probe::alloc(n);
}
void* operator new[](size_t n, const std::nothrow_t&) noexcept {
    return probe::alloc(n);
}
void operator delete(void* p) noexcept { probe::dealloc(p); }
void operator delete[](void* p) noexcept { probe::dealloc(p); }
void operator delete(void* p, size_t) noexcept { probe::dealloc(p); }
void operator delete[](void* p, size_t) noexcept { probe::dealloc(p); }
void operator delete(void* p, const std::nothrow_t&) noexcept {
    probe::dealloc(p);
}
void operator delete[](void* p, const std::nothrow_t&) noexcept {
    probe::dealloc(p);
}
