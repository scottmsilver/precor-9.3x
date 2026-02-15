/*
 * kv_protocol.cpp — KV parser + builder implementation
 */

#include "kv_protocol.h"
#include <cstdio>
#include <cstring>
#include <cstdlib>

int kv_parse(const uint8_t* buf, int len, KvPair* pairs, int max_pairs, int* consumed) {
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

            if (printable && raw_len > 0 && raw_len < 64) {
                char content[128];
                std::memcpy(content, buf + i + 1, raw_len);
                content[raw_len] = '\0';

                char* colon = std::strchr(content, ':');
                if (colon) {
                    *colon = '\0';
                    std::snprintf(pairs[n].key, sizeof(pairs[n].key), "%s", content);
                    std::snprintf(pairs[n].value, sizeof(pairs[n].value), "%s", colon + 1);
                    n++;
                } else {
                    // Bare key with no value, e.g. [amps]
                    std::snprintf(pairs[n].key, sizeof(pairs[n].key), "%s", content);
                    pairs[n].value[0] = '\0';
                    n++;
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

int kv_build(char* out, int out_size, const char* key, const char* value) {
    int len;
    if (value && value[0]) {
        len = std::snprintf(out, out_size, "[%s:%s]", key, value);
    } else {
        len = std::snprintf(out, out_size, "[%s]", key);
    }
    if (len >= 0 && len < out_size - 1) {
        out[len] = static_cast<char>(0xFF);
        len++;
        out[len] = '\0';
    }
    return len;
}

int encode_speed_hex(int tenths_mph, char* out, int out_size) {
    // Speed wire format: mph * 100, in uppercase hex
    // tenths_mph is in tenths, so multiply by 10 to get hundredths
    int hundredths = tenths_mph * 10;
    return std::snprintf(out, out_size, "%X", hundredths);
}

int decode_speed_hex(const char* hex) {
    if (!hex || !hex[0]) return -1;

    char* end = nullptr;
    long val = std::strtol(hex, &end, 16);
    if (end == hex || val < 0) return -1;

    // val is in hundredths of mph, convert to tenths (round)
    return static_cast<int>((val + 5) / 10);
}
