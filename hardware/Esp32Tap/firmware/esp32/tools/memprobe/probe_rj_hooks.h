/*
 * probe_rj_hooks.h — injected at the top of the shimmed
 * rapidjson/allocators.h so CrtAllocator's three bodies land in the
 * probe instead of libc malloc. Host-only measurement tool.
 */

#pragma once

#include "probe_alloc.h"

inline void* probe_rj_malloc(size_t n) { return probe::alloc(n); }
inline void probe_rj_free(void* p) { probe::dealloc(p); }
inline void* probe_rj_realloc(void* p, size_t n) {
    return probe::re_alloc(p, n);
}
