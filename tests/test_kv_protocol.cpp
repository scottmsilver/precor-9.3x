/*
 * test_kv_protocol.cpp — Tests for KV parser/builder and hex encoding
 */

#define DOCTEST_CONFIG_IMPLEMENT_WITH_MAIN
#define DOCTEST_CONFIG_NO_EXCEPTIONS
#include <doctest.h>
#include "kv_protocol.h"
#include <cstring>

// ── kv_parse tests ──────────────────────────────────────────────────

TEST_CASE("kv_parse: basic key:value pair") {
    const uint8_t data[] = "[hmph:78]";
    KvPair pairs[4];
    int consumed = 0;
    int n = kv_parse(data, sizeof(data) - 1, pairs, 4, &consumed);

    CHECK(n == 1);
    CHECK(std::strcmp(pairs[0].key, "hmph") == 0);
    CHECK(std::strcmp(pairs[0].value, "78") == 0);
    CHECK(consumed == 9);
}

TEST_CASE("kv_parse: bare key without value") {
    const uint8_t data[] = "[amps]";
    KvPair pairs[4];
    int consumed = 0;
    int n = kv_parse(data, sizeof(data) - 1, pairs, 4, &consumed);

    CHECK(n == 1);
    CHECK(std::strcmp(pairs[0].key, "amps") == 0);
    CHECK(std::strcmp(pairs[0].value, "") == 0);
}

TEST_CASE("kv_parse: multiple pairs with 0xFF delimiter") {
    uint8_t data[] = "[inc:5]\xff[hmph:78]\xff";
    KvPair pairs[4];
    int consumed = 0;
    int n = kv_parse(data, sizeof(data) - 1, pairs, 4, &consumed);

    CHECK(n == 2);
    CHECK(std::strcmp(pairs[0].key, "inc") == 0);
    CHECK(std::strcmp(pairs[0].value, "5") == 0);
    CHECK(std::strcmp(pairs[1].key, "hmph") == 0);
    CHECK(std::strcmp(pairs[1].value, "78") == 0);
}

TEST_CASE("kv_parse: skips 0x00 and 0xFF delimiters") {
    uint8_t data[] = { 0xFF, 0x00, '[', 'k', ':', 'v', ']', 0xFF, 0x00 };
    KvPair pairs[4];
    int consumed = 0;
    int n = kv_parse(data, sizeof(data), pairs, 4, &consumed);

    CHECK(n == 1);
    CHECK(std::strcmp(pairs[0].key, "k") == 0);
    CHECK(std::strcmp(pairs[0].value, "v") == 0);
}

TEST_CASE("kv_parse: incomplete frame preserves bytes") {
    const uint8_t data[] = "[hmph:7";  // missing closing bracket
    KvPair pairs[4];
    int consumed = 0;
    int n = kv_parse(data, sizeof(data) - 1, pairs, 4, &consumed);

    CHECK(n == 0);
    CHECK(consumed < static_cast<int>(sizeof(data) - 1));  // not all consumed
}

TEST_CASE("kv_parse: rejects non-printable content") {
    uint8_t data[] = { '[', 'k', ':', 0x01, ']' };
    KvPair pairs[4];
    int consumed = 0;
    int n = kv_parse(data, sizeof(data), pairs, 4, &consumed);

    CHECK(n == 0);
}

TEST_CASE("kv_parse: max_pairs limit respected") {
    const uint8_t data[] = "[a:1][b:2][c:3]";
    KvPair pairs[2];
    int consumed = 0;
    int n = kv_parse(data, sizeof(data) - 1, pairs, 2, &consumed);

    CHECK(n == 2);
    CHECK(std::strcmp(pairs[0].key, "a") == 0);
    CHECK(std::strcmp(pairs[1].key, "b") == 0);
}

TEST_CASE("kv_parse: empty input") {
    KvPair pairs[4];
    int consumed = 0;
    int n = kv_parse(nullptr, 0, pairs, 4, &consumed);

    // Should handle gracefully (len=0 means while loop doesn't execute)
    // Note: buf=nullptr with len=0 is safe because loop checks i < len first
    CHECK(n == 0);
    CHECK(consumed == 0);
}

TEST_CASE("kv_parse: garbage between valid frames") {
    uint8_t data[] = "xyz[a:1]garbage[b:2]";
    KvPair pairs[4];
    int consumed = 0;
    int n = kv_parse(data, sizeof(data) - 1, pairs, 4, &consumed);

    CHECK(n == 2);
    CHECK(std::strcmp(pairs[0].key, "a") == 0);
    CHECK(std::strcmp(pairs[1].key, "b") == 0);
}

// ── kv_build tests ──────────────────────────────────────────────────

TEST_CASE("kv_build: key with value") {
    char out[128];
    int len = kv_build(out, sizeof(out), "inc", "5");

    CHECK(len == 8);  // "[inc:5]" (7) + 0xFF (1) = 8
    CHECK(std::memcmp(out, "[inc:5]", 7) == 0);
    CHECK(static_cast<uint8_t>(out[7]) == 0xFF);
}

TEST_CASE("kv_build: bare key") {
    char out[128];
    int len = kv_build(out, sizeof(out), "amps", nullptr);

    CHECK(len == 7);  // "[amps]" + 0xFF
    CHECK(std::memcmp(out, "[amps]", 6) == 0);
    CHECK(static_cast<uint8_t>(out[6]) == 0xFF);
}

TEST_CASE("kv_build: empty value treated as bare key") {
    char out[128];
    int len = kv_build(out, sizeof(out), "amps", "");

    CHECK(std::memcmp(out, "[amps]", 6) == 0);
    CHECK(static_cast<uint8_t>(out[6]) == 0xFF);
    (void)len;
}

// ── Hex encoding tests ──────────────────────────────────────────────

TEST_CASE("encode_speed_hex: 1.2 mph = 12 tenths -> 120 hundredths = 0x78") {
    char out[32];
    encode_speed_hex(12, out, sizeof(out));
    CHECK(std::strcmp(out, "78") == 0);
}

TEST_CASE("encode_speed_hex: 12.0 mph = 120 tenths -> 1200 hundredths = 0x4B0") {
    char out[32];
    encode_speed_hex(120, out, sizeof(out));
    CHECK(std::strcmp(out, "4B0") == 0);
}

TEST_CASE("encode_speed_hex: 0 mph") {
    char out[32];
    encode_speed_hex(0, out, sizeof(out));
    CHECK(std::strcmp(out, "0") == 0);
}

TEST_CASE("decode_speed_hex: 78 -> 12 tenths (1.2 mph)") {
    CHECK(decode_speed_hex("78") == 12);
}

TEST_CASE("decode_speed_hex: 4B0 -> 120 tenths (12.0 mph)") {
    CHECK(decode_speed_hex("4B0") == 120);
}

TEST_CASE("decode_speed_hex: 0 -> 0") {
    CHECK(decode_speed_hex("0") == 0);
}

TEST_CASE("decode_speed_hex: empty string -> -1") {
    CHECK(decode_speed_hex("") == -1);
}

TEST_CASE("decode_speed_hex: null -> -1") {
    CHECK(decode_speed_hex(nullptr) == -1);
}

TEST_CASE("encode/decode round-trip") {
    for (int t = 0; t <= 120; t++) {
        char hex[32];
        encode_speed_hex(t, hex, sizeof(hex));
        int decoded = decode_speed_hex(hex);
        CHECK(decoded == t);
    }
}
