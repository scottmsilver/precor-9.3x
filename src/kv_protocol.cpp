/*
 * kv_protocol.cpp — KV parser + builder implementation
 */

#include "kv_protocol.h"
#include <charconv>

int kv_parse(std::span<const uint8_t> buf, KvPair* pairs, int max_pairs, int* consumed) {
    int len = static_cast<int>(buf.size());
    int i = 0, n = 0;

    while (i < len && n < max_pairs) {
        // Skip delimiters
        if (buf[i] == 0xFF || buf[i] == 0x00) {
            i++;
            continue;
        }
        if (buf[i] == '[') {
            // Find closing bracket
            int end = -1;
            for (int j = i + 1; j < len; j++) {
                if (buf[j] == ']') { end = j; break; }
            }
            if (end == -1) break;  // incomplete frame

            int raw_len = end - i - 1;
            // Validate: all bytes must be printable ASCII
            bool printable = true;
            for (int j = i + 1; j < end; j++) {
                if (buf[j] < 0x20 || buf[j] > 0x7E) {
                    printable = false;
                    break;
                }
            }

            if (printable && raw_len > 0 && raw_len < KV_FIELD_SIZE) {
                // Extract content between brackets as a string_view
                // reinterpret_cast: uint8_t -> char aliasing (standard-allowed)
                std::string_view content(reinterpret_cast<const char*>(buf.data() + i + 1), raw_len);

                auto colon_pos = content.find(':');
                auto& pair = pairs[n];

                if (colon_pos != std::string_view::npos) {
                    auto key_part = content.substr(0, colon_pos);
                    auto val_part = content.substr(colon_pos + 1);
                    // Safe copy into fixed arrays with bounds check
                    if (key_part.size() < KV_FIELD_SIZE && val_part.size() < KV_FIELD_SIZE) {
                        key_part.copy(pair.key.data(), key_part.size());
                        pair.key.at(key_part.size()) = '\0';
                        val_part.copy(pair.value.data(), val_part.size());
                        pair.value.at(val_part.size()) = '\0';
                        n++;
                    }
                } else {
                    // Bare key with no value
                    if (content.size() < KV_FIELD_SIZE) {
                        content.copy(pair.key.data(), content.size());
                        pair.key.at(content.size()) = '\0';
                        pair.value.at(0) = '\0';
                        n++;
                    }
                }
            }
            i = end + 1;
        } else {
            i++;
        }
    }

    *consumed = i;
    return n;
}

std::string kv_build(std::string_view key, std::string_view value) {
    std::string result;
    result.reserve(key.size() + value.size() + 4);
    result += '[';
    result += key;
    if (!value.empty()) {
        result += ':';
        result += value;
    }
    result += ']';
    result += static_cast<char>(0xFF);
    return result;
}

std::string encode_speed_hex(int tenths_mph) {
    // Speed wire format: mph * 100, in uppercase hex
    // tenths_mph is in tenths, so multiply by 10 to get hundredths
    int hundredths = tenths_mph * 10;
    std::array<char, 16> buf{};
    auto [ptr, ec] = std::to_chars(buf.data(), buf.data() + buf.size(), hundredths, 16);
    std::string result(buf.data(), ptr);
    // Convert to uppercase
    for (auto& c : result) {
        if (c >= 'a' && c <= 'f') c -= 32;
    }
    return result;
}

int decode_speed_hex(std::string_view hex) {
    if (hex.empty() || hex.size() > 10) return -1;

    unsigned long val = 0;
    auto [ptr, ec] = std::from_chars(hex.data(), hex.data() + hex.size(), val, 16);
    if (ec != std::errc{} || ptr != hex.data() + hex.size()) return -1;

    // val is in hundredths of mph, convert to tenths (round)
    return static_cast<int>((val + 5) / 10);
}

std::string encode_incline_hex(int percent) {
    // Incline wire format: half-percent units, uppercase hex
    // percent * 2 = half-percent value
    int half_pct = percent * 2;
    std::array<char, 16> buf{};
    auto [ptr, ec] = std::to_chars(buf.data(), buf.data() + buf.size(), half_pct, 16);
    std::string result(buf.data(), ptr);
    // Convert to uppercase
    for (auto& c : result) {
        if (c >= 'a' && c <= 'f') c -= 32;
    }
    return result;
}

int decode_incline_hex(std::string_view hex) {
    if (hex.empty() || hex.size() > 10) return -1;

    unsigned long val = 0;
    auto [ptr, ec] = std::from_chars(hex.data(), hex.data() + hex.size(), val, 16);
    if (ec != std::errc{} || ptr != hex.data() + hex.size()) return -1;

    // val is in half-percent units, convert to whole percent (round)
    return static_cast<int>((val + 1) / 2);
}
