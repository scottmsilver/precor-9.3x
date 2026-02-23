/*
 * test_kv_protocol.cpp — Tests for KV parser/builder and hex encoding
 */

#define DOCTEST_CONFIG_IMPLEMENT_WITH_MAIN
#define DOCTEST_CONFIG_NO_EXCEPTIONS
#include <doctest.h>
#include "kv_protocol.h"
#include <span>

// ── kv_parse tests ──────────────────────────────────────────────────

TEST_CASE("kv_parse: basic key:value pair") {
    const uint8_t data[] = "[hmph:78]";
    KvPair pairs[4];
    int consumed = 0;
    int n = kv_parse({data, sizeof(data) - 1}, pairs, 4, &consumed);

    CHECK(n == 1);
    CHECK(pairs[0].key_view() == "hmph");
    CHECK(pairs[0].value_view() == "78");
    CHECK(consumed == 9);
}

TEST_CASE("kv_parse: bare key without value") {
    const uint8_t data[] = "[amps]";
    KvPair pairs[4];
    int consumed = 0;
    int n = kv_parse({data, sizeof(data) - 1}, pairs, 4, &consumed);

    CHECK(n == 1);
    CHECK(pairs[0].key_view() == "amps");
    CHECK(pairs[0].value_view() == "");
}

TEST_CASE("kv_parse: multiple pairs with 0xFF delimiter") {
    uint8_t data[] = "[inc:5]\xff[hmph:78]\xff";
    KvPair pairs[4];
    int consumed = 0;
    int n = kv_parse({data, sizeof(data) - 1}, pairs, 4, &consumed);

    CHECK(n == 2);
    CHECK(pairs[0].key_view() == "inc");
    CHECK(pairs[0].value_view() == "5");
    CHECK(pairs[1].key_view() == "hmph");
    CHECK(pairs[1].value_view() == "78");
}

TEST_CASE("kv_parse: skips 0x00 and 0xFF delimiters") {
    uint8_t data[] = { 0xFF, 0x00, '[', 'k', ':', 'v', ']', 0xFF, 0x00 };
    KvPair pairs[4];
    int consumed = 0;
    int n = kv_parse(data, pairs, 4, &consumed);

    CHECK(n == 1);
    CHECK(pairs[0].key_view() == "k");
    CHECK(pairs[0].value_view() == "v");
}

TEST_CASE("kv_parse: incomplete frame preserves bytes") {
    const uint8_t data[] = "[hmph:7";  // missing closing bracket
    KvPair pairs[4];
    int consumed = 0;
    int n = kv_parse({data, sizeof(data) - 1}, pairs, 4, &consumed);

    CHECK(n == 0);
    CHECK(consumed < static_cast<int>(sizeof(data) - 1));  // not all consumed
}

TEST_CASE("kv_parse: rejects non-printable content") {
    uint8_t data[] = { '[', 'k', ':', 0x01, ']' };
    KvPair pairs[4];
    int consumed = 0;
    int n = kv_parse(data, pairs, 4, &consumed);

    CHECK(n == 0);
}

TEST_CASE("kv_parse: max_pairs limit respected") {
    const uint8_t data[] = "[a:1][b:2][c:3]";
    KvPair pairs[2];
    int consumed = 0;
    int n = kv_parse({data, sizeof(data) - 1}, pairs, 2, &consumed);

    CHECK(n == 2);
    CHECK(pairs[0].key_view() == "a");
    CHECK(pairs[1].key_view() == "b");
}

TEST_CASE("kv_parse: empty input") {
    KvPair pairs[4];
    int consumed = 0;
    int n = kv_parse({}, pairs, 4, &consumed);

    CHECK(n == 0);
    CHECK(consumed == 0);
}

TEST_CASE("kv_parse: garbage between valid frames") {
    uint8_t data[] = "xyz[a:1]garbage[b:2]";
    KvPair pairs[4];
    int consumed = 0;
    int n = kv_parse({data, sizeof(data) - 1}, pairs, 4, &consumed);

    CHECK(n == 2);
    CHECK(pairs[0].key_view() == "a");
    CHECK(pairs[1].key_view() == "b");
}

// ── kv_build tests ──────────────────────────────────────────────────

TEST_CASE("kv_build: key with value") {
    auto result = kv_build("inc", "5");

    CHECK(result.size() == 8);  // "[inc:5]" (7) + 0xFF (1) = 8
    CHECK(result.substr(0, 7) == "[inc:5]");
    CHECK(static_cast<uint8_t>(result.at(7)) == 0xFF);
}

TEST_CASE("kv_build: bare key") {
    auto result = kv_build("amps");

    CHECK(result.size() == 7);  // "[amps]" + 0xFF
    CHECK(result.substr(0, 6) == "[amps]");
    CHECK(static_cast<uint8_t>(result.at(6)) == 0xFF);
}

TEST_CASE("kv_build: empty value treated as bare key") {
    auto result = kv_build("amps", "");

    CHECK(result.substr(0, 6) == "[amps]");
    CHECK(static_cast<uint8_t>(result.at(6)) == 0xFF);
}

// ── Hex encoding tests ──────────────────────────────────────────────

TEST_CASE("encode_speed_hex: 1.2 mph = 12 tenths -> 120 hundredths = 0x78") {
    CHECK(encode_speed_hex(12) == "78");
}

TEST_CASE("encode_speed_hex: 12.0 mph = 120 tenths -> 1200 hundredths = 0x4B0") {
    CHECK(encode_speed_hex(120) == "4B0");
}

TEST_CASE("encode_speed_hex: 0 mph") {
    CHECK(encode_speed_hex(0) == "0");
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

TEST_CASE("encode/decode round-trip") {
    for (int t = 0; t <= 120; t++) {
        auto hex = encode_speed_hex(t);
        int decoded = decode_speed_hex(hex);
        CHECK(decoded == t);
    }
}

// ── Incline hex encoding tests (half-pct units) ─────────────────────

TEST_CASE("encode_incline_hex: 0 half-pct (0%) -> 0x0") {
    CHECK(encode_incline_hex(0) == "0");
}

TEST_CASE("encode_incline_hex: 10 half-pct (5%) -> 0xA") {
    CHECK(encode_incline_hex(10) == "A");
}

TEST_CASE("encode_incline_hex: 30 half-pct (15%) -> 0x1E") {
    CHECK(encode_incline_hex(30) == "1E");
}

TEST_CASE("encode_incline_hex: 14 half-pct (7%) -> 0xE") {
    CHECK(encode_incline_hex(14) == "E");
}

TEST_CASE("encode_incline_hex: 1 half-pct (0.5%) -> 0x1") {
    CHECK(encode_incline_hex(1) == "1");
}

TEST_CASE("decode_incline_hex: A -> 10 half-pct (5%)") {
    CHECK(decode_incline_hex("A") == 10);
}

TEST_CASE("decode_incline_hex: 1E -> 30 half-pct (15%)") {
    CHECK(decode_incline_hex("1E") == 30);
}

TEST_CASE("decode_incline_hex: 0 -> 0 half-pct") {
    CHECK(decode_incline_hex("0") == 0);
}

TEST_CASE("decode_incline_hex: 1 -> 1 half-pct (0.5%)") {
    CHECK(decode_incline_hex("1") == 1);
}

TEST_CASE("decode_incline_hex: B -> 11 half-pct (5.5%)") {
    CHECK(decode_incline_hex("B") == 11);
}

TEST_CASE("decode_incline_hex: empty string -> -1") {
    CHECK(decode_incline_hex("") == -1);
}

TEST_CASE("encode/decode incline round-trip (half-pct)") {
    for (int hp = 0; hp <= 198; hp++) {
        auto hex = encode_incline_hex(hp);
        int decoded = decode_incline_hex(hex);
        CHECK(decoded == hp);
    }
}
