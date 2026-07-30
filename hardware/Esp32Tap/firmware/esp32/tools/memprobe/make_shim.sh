#!/usr/bin/env bash
# make_shim.sh SRC_RAPIDJSON_ROOT OUT_DIR MODE(pool|crt)
#
# Produces a shimmed copy of the vendored rapidjson under OUT_DIR so the
# measurement build can see every byte rapidjson allocates. The vendored
# tree is never modified.
#
#   pool : CrtAllocator routed to the probe. Document/Value keep
#          MemoryPoolAllocator -> exactly today's firmware behaviour.
#   crt  : additionally retypedefs Value/Document onto CrtAllocator
#          (kNeedFree = true) -> the behaviour the mandate calls for
#          (Erase()/destruction actually return memory).
set -euo pipefail
SRC=$1
OUT=$2
MODE=$3
HERE=$(cd "$(dirname "$0")" && pwd)

rm -rf "$OUT"
mkdir -p "$OUT"
cp -r "$SRC/rapidjson" "$OUT/rapidjson"
cp "$HERE/probe_rj_hooks.h" "$HERE/probe_alloc.h" "$OUT/rapidjson/"

A="$OUT/rapidjson/allocators.h"
# 1. hooks visible inside allocators.h
printf '#include "probe_rj_hooks.h"\n' | cat - "$A" > "$A.tmp" && mv "$A.tmp" "$A"
# 2. CrtAllocator bodies -> probe
sed -i \
  -e 's|return std::malloc(size);|return probe_rj_malloc(size);|' \
  -e 's|std::free(originalPtr);|probe_rj_free(originalPtr);|' \
  -e 's|return std::realloc(originalPtr, newSize);|return probe_rj_realloc(originalPtr, newSize);|' \
  -e 's|static void Free(void \*ptr) { std::free(ptr); }|static void Free(void *ptr) { probe_rj_free(ptr); }|' \
  "$A"
# 3. json_store.h::compact_if_bloated() introspects the allocator.
#    A free-capable allocator has no dead space, so report none.
sed -i 's|    static const bool kNeedFree = true;|    static const bool kNeedFree = true;\n    size_t Capacity() const { return 0; }\n    size_t Size() const { return 0; }\n    void Clear() {}|' "$A"

if [ "$MODE" = "crt" ]; then
  D="$OUT/rapidjson/document.h"
  sed -i \
    -e 's|^typedef GenericValue<UTF8<> > Value;|typedef GenericValue<UTF8<>, CrtAllocator > Value;|' \
    -e 's|^typedef GenericDocument<UTF8<> > Document;|typedef GenericDocument<UTF8<>, CrtAllocator, CrtAllocator > Document;|' \
    "$D"
  grep -q 'GenericValue<UTF8<>, CrtAllocator > Value;' "$D"
  grep -q 'GenericDocument<UTF8<>, CrtAllocator, CrtAllocator > Document;' "$D"
fi
grep -q 'probe_rj_malloc' "$A"
echo "shim($MODE) -> $OUT"
