/*
 * kv_protocol.h — KV parser + builder for the treadmill wire protocol
 *
 * Pure functions, no I/O, no state. The treadmill uses a unique text
 * protocol: [key:value]\xff framing at 9600 baud.
 */

#pragma once

#include <cstdint>

struct KvPair {
    char key[64];
    char value[64];
};

/*
 * Parse [key:value] pairs from a raw byte buffer.
 * Skips \xff and \x00 delimiters, rejects non-printable content.
 *
 * Returns the number of pairs found.
 * Sets *consumed to the number of bytes processed (unconsumed bytes
 * should be kept for the next call).
 */
int kv_parse(const uint8_t* buf, int len, KvPair* pairs, int max_pairs, int* consumed);

/*
 * Build a KV command in wire format: [key:value]\xff
 * If value is null or empty, builds [key]\xff
 * Returns the length written (including \xff).
 */
int kv_build(char* out, int out_size, const char* key, const char* value);

/*
 * Encode speed in tenths of mph to uppercase hex string (mph * 100).
 * E.g., 12 (1.2 mph) -> "78", 120 (12.0 mph) -> "4B0"
 * Returns the length of the hex string written.
 */
int encode_speed_hex(int tenths_mph, char* out, int out_size);

/*
 * Decode uppercase hex string to speed in tenths of mph.
 * E.g., "78" -> 12 (1.2 mph), "4B0" -> 120 (12.0 mph)
 * Returns -1 on parse error.
 */
int decode_speed_hex(const char* hex);
