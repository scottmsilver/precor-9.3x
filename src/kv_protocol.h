/*
 * kv_protocol.h — KV parser + builder for the treadmill wire protocol
 *
 * Pure functions, no I/O, no state. The treadmill uses a unique text
 * protocol: [key:value]\xff framing at 9600 baud.
 */

#pragma once

#include <cstdint>
#include <span>
#include <string>
#include <string_view>
#include <array>

static constexpr int KV_FIELD_SIZE = 64;
static constexpr int MAX_KV_CONTENT_LEN = 127;

struct KvPair {
    std::array<char, KV_FIELD_SIZE> key{};
    std::array<char, KV_FIELD_SIZE> value{};

    std::string_view key_view() const { return key.data(); }
    std::string_view value_view() const { return value.data(); }
};

/*
 * Parse [key:value] pairs from a raw byte buffer.
 * Skips \xff and \x00 delimiters, rejects non-printable content.
 *
 * This is on the hot path (serial read loop) — uses fixed-size arrays,
 * no heap allocation.
 *
 * Returns the number of pairs found.
 * Sets *consumed to the number of bytes processed (unconsumed bytes
 * should be kept for the next call).
 */
int kv_parse(std::span<const uint8_t> buf, KvPair* pairs, int max_pairs, int* consumed);

/*
 * Build a KV command in wire format: [key:value]\xff
 * If value is empty, builds [key]\xff
 */
std::string kv_build(std::string_view key, std::string_view value = {});

/*
 * Encode speed in tenths of mph to uppercase hex string (mph * 100).
 * E.g., 12 (1.2 mph) -> "78", 120 (12.0 mph) -> "4B0"
 */
std::string encode_speed_hex(int tenths_mph);

/*
 * Decode uppercase hex string to speed in tenths of mph.
 * E.g., "78" -> 12 (1.2 mph), "4B0" -> 120 (12.0 mph)
 * Returns -1 on parse error.
 */
int decode_speed_hex(std::string_view hex);

/*
 * Encode incline percent to uppercase hex string (half-percent units).
 * E.g., 5 -> "A" (5*2=10, hex A), 15 -> "1E" (15*2=30, hex 1E)
 */
std::string encode_incline_hex(int percent);

/*
 * Decode uppercase hex string (half-percent units) to incline percent.
 * E.g., "A" -> 5 (hex 10, /2), "1E" -> 15 (hex 30, /2)
 * Returns -1 on parse error.
 */
int decode_incline_hex(std::string_view hex);
