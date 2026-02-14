/*
 * treadmill_io.c — Low-latency GPIO I/O daemon for Precor 9.3x treadmill
 *
 * This is the core I/O binary for the treadmill intercept system. It links
 * libpigpio directly (library mode, not daemon) for minimal-latency GPIO
 * access, and exposes a Unix domain socket for Python client tools.
 *
 * WHAT IT DOES:
 *   - Reads RS-485 serial from the treadmill console (controller) and motor
 *   - Parses the [key:value] KV text protocol used by both directions
 *   - In proxy mode: forwards console commands to the motor unchanged
 *   - In emulate mode: replaces the console, sending a synthesized 14-key
 *     KV command cycle with controllable speed and incline
 *   - Serves parsed KV events and status to up to 4 Python clients over
 *     a Unix socket using newline-delimited JSON
 *
 * HARDWARE:
 *   Pin 6 of the treadmill cable is CUT through the Pi (intercept path).
 *   Pin 3 is tapped passively (read-only motor responses).
 *
 *     Console ──pin6──> [console_read GPIO] Pi [motor_write GPIO] ──pin6──> Motor
 *                                              Motor ──pin3──> [motor_read GPIO] Pi
 *
 *   GPIO pin numbers are loaded from gpio.json at startup.
 *   Serial: 9600 baud, 8N1, RS-485 inverted polarity (idle LOW).
 *
 * THREADS:
 *   1. console_read — polls GPIO for console serial data, proxies to motor
 *   2. motor_read   — polls GPIO for motor response data (read-only)
 *   3. emulate      — sends synthesized KV cycle when emulate mode is on
 *   4. ipc          — Unix socket server: accepts clients, reads commands,
 *                     flushes KV events from ring buffer to clients
 *   5. main         — setup, signal handling, waits for shutdown
 *
 * IPC PROTOCOL (newline-delimited JSON over /tmp/treadmill_io.sock):
 *
 *   Server → Client (events):
 *     {"type":"kv","ts":1.23,"source":"console","key":"hmph","value":"78"}
 *     {"type":"kv","ts":1.23,"source":"motor","key":"belt","value":"0"}
 *     {"type":"kv","ts":1.23,"source":"emulate","key":"inc","value":"5"}
 *     {"type":"status","proxy":true,"emulate":false,"emu_speed":0,
 *      "emu_incline":0,"console_bytes":1234,"motor_bytes":567}
 *
 *   Client → Server (commands):
 *     {"cmd":"proxy","enabled":true}       — enable proxy (disables emulate)
 *     {"cmd":"proxy","enabled":false}      — disable proxy
 *     {"cmd":"emulate","enabled":true}     — enable emulate (disables proxy)
 *     {"cmd":"emulate","enabled":false}    — disable emulate
 *     {"cmd":"speed","value":1.2}          — set emulate speed (mph float)
 *     {"cmd":"incline","value":5}          — set emulate incline (int 0-99)
 *     {"cmd":"status"}                     — request a status message
 *     {"cmd":"quit"}                       — shut down the daemon
 *
 *   IMPORTANT: JSON must use compact encoding (no spaces after colons/commas)
 *   because the C parser uses strstr() for pattern matching.
 *
 * SAFETY:
 *   - Entering emulate mode always resets speed and incline to 0
 *   - After 3 hours of continuous emulation, emulate mode is stopped
 *     automatically (speed/incline reset to 0, mode set to off)
 *
 * BUILD:
 *   make
 *   (or: gcc -Wall -O2 -pthread -o treadmill_io treadmill_io.c -lpigpio -lrt)
 *
 * RUN:
 *   sudo ./treadmill_io          (pigpiod must NOT be running)
 *
 * REQUIRES:
 *   - Root access (for GPIO)
 *   - libpigpio (apt install pigpio)
 *   - gpio.json in the working directory
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <signal.h>
#include <time.h>
#include <errno.h>
#include <pthread.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/select.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <pigpio.h>

/* ── GPIO pins (loaded from gpio.json at startup) ──────────────────── */

static int GPIO_CONSOLE_READ = -1;  /* reads serial from console (controller) */
static int GPIO_MOTOR_WRITE  = -1;  /* writes serial to motor (proxy/emulate) */
static int GPIO_MOTOR_READ   = -1;  /* reads serial from motor (passive tap) */

#define GPIO_JSON "gpio.json"
#define BAUD  9600
#define BIT_US (1000000 / BAUD)  /* ~104 µs per bit at 9600 baud */

/* Speed/incline limits — mirrored in treadmill_client.py */
#define MAX_SPEED_TENTHS 120    /* 12.0 mph max, in tenths */
#define MAX_INCLINE       99

/*
 * Load GPIO pin assignments from gpio.json.
 * Looks up "console_read", "motor_write", "motor_read" keys and extracts
 * the "gpio" integer from each object. Simple strstr-based parser — works
 * because gpio.json has a fixed, known structure.
 */
static int gpio_json_lookup(const char *json, const char *name) {
    const char *p = strstr(json, name);
    if (!p) return -1;
    p = strstr(p, "\"gpio\"");
    if (!p) return -1;
    p = strchr(p + 6, ':');
    if (!p) return -1;
    return atoi(p + 1);
}

static int load_gpio_config(void) {
    FILE *f = fopen(GPIO_JSON, "r");
    if (!f) {
        fprintf(stderr, "Error: cannot open %s\n", GPIO_JSON);
        return -1;
    }
    char buf[2048];
    size_t n = fread(buf, 1, sizeof(buf) - 1, f);
    fclose(f);
    buf[n] = '\0';

    GPIO_CONSOLE_READ = gpio_json_lookup(buf, "\"console_read\"");
    GPIO_MOTOR_WRITE  = gpio_json_lookup(buf, "\"motor_write\"");
    GPIO_MOTOR_READ   = gpio_json_lookup(buf, "\"motor_read\"");

    if (GPIO_CONSOLE_READ < 0 || GPIO_MOTOR_WRITE < 0 || GPIO_MOTOR_READ < 0) {
        fprintf(stderr, "Error: missing pins in %s (console_read=%d, motor_write=%d, motor_read=%d)\n",
                GPIO_JSON, GPIO_CONSOLE_READ, GPIO_MOTOR_WRITE, GPIO_MOTOR_READ);
        return -1;
    }
    return 0;
}

/* ── IPC constants ─────────────────────────────────────────────────── */

#define SOCK_PATH "/tmp/treadmill_io.sock"
#define MAX_CLIENTS 4       /* max simultaneous Python clients */
#define CMD_BUF_SIZE 1024   /* per-client command receive buffer */

/* ── Ring buffer ───────────────────────────────────────────────────── *
 *
 * Lock-free-ish circular buffer that decouples the GPIO read threads
 * (producers) from the IPC thread (consumer). Each entry is a JSON
 * string (a KV event or status message). If a client falls behind,
 * oldest messages are skipped — GPIO readers never block on slow clients.
 */

#define RING_SIZE 2048
#define MSG_MAX  256

typedef struct {
    char msgs[RING_SIZE][MSG_MAX];
    int  head;           /* next write position (wraps mod RING_SIZE) */
    unsigned int count;  /* total messages ever written (for sequence tracking) */
    pthread_mutex_t lock;
} ring_t;

static ring_t ring;

static void ring_init(ring_t *r) {
    memset(r, 0, sizeof(*r));
    pthread_mutex_init(&r->lock, NULL);
}

static void ring_push(ring_t *r, const char *msg) {
    pthread_mutex_lock(&r->lock);
    strncpy(r->msgs[r->head], msg, MSG_MAX - 1);
    r->msgs[r->head][MSG_MAX - 1] = '\0';
    r->head = (r->head + 1) % RING_SIZE;
    r->count++;
    pthread_mutex_unlock(&r->lock);
}

/* ── Shared state ──────────────────────────────────────────────────── *
 *
 * These volatile ints are shared between threads. On 32-bit ARM,
 * aligned int reads/writes are naturally atomic, so volatile is
 * sufficient for simple flag checks. The write_lock mutex serializes
 * GPIO wave output; emu_lock protects emulate thread lifecycle.
 */

static volatile int running = 1;
static volatile int proxy_enabled = 1;
static volatile int emulate_enabled = 0;
static volatile int emu_speed = 0;        /* tenths of mph (12 = 1.2 mph) */
static volatile int emu_speed_raw = 0;    /* hundredths of mph for hex encoding */
static volatile int emu_incline = 0;      /* incline percentage (0-99) */

static char last_console_hmph[32] = "";  /* last seen hmph value from console */
static char last_console_inc[32] = "";   /* last seen inc value from console */

static volatile uint32_t console_bytes = 0;  /* total bytes received from console */
static volatile uint32_t motor_bytes = 0;    /* total bytes received from motor */

static pthread_mutex_t write_lock = PTHREAD_MUTEX_INITIALIZER;  /* serializes GPIO wave output */
static pthread_mutex_t emu_lock   = PTHREAD_MUTEX_INITIALIZER;  /* protects emu_thread_running */

static struct timespec start_ts;        /* program start time (for elapsed timestamps) */
static pthread_t emu_thread_id;
static volatile int emu_thread_running = 0;

/* ── Timing helpers ────────────────────────────────────────────────── */

/* Seconds since program start (monotonic clock) */
static double elapsed_sec(void) {
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    return (now.tv_sec - start_ts.tv_sec) +
           (now.tv_nsec - start_ts.tv_nsec) / 1e9;
}

static void sleep_ms(int ms) {
    struct timespec ts = { ms / 1000, (ms % 1000) * 1000000L };
    nanosleep(&ts, NULL);
}

/* ── KV protocol parser ───────────────────────────────────────────── *
 *
 * The treadmill uses a text-based KV protocol in both directions:
 *   Console→Motor: [key:value]\xff  or  [key]\xff
 *   Motor→Console: [key:value]      (no \xff delimiter)
 *
 * This parser extracts [key:value] pairs from a raw byte buffer,
 * skipping \xff and \x00 delimiters, and rejecting non-printable content.
 * Returns the number of pairs found and how many bytes were consumed.
 * Unconsumed bytes (incomplete frames) should be kept for the next call.
 */

typedef struct {
    char key[64];
    char value[64];
} kv_pair_t;

static int kv_parse(const unsigned char *buf, int len,
                    kv_pair_t *pairs, int max_pairs, int *consumed)
{
    int i = 0, n = 0;

    while (i < len && n < max_pairs) {
        /* Skip delimiters */
        if (buf[i] == 0xFF || buf[i] == 0x00) {
            i++;
            continue;
        }
        if (buf[i] == '[') {
            /* Find closing bracket */
            int end = -1;
            for (int j = i + 1; j < len; j++) {
                if (buf[j] == ']') { end = j; break; }
            }
            if (end == -1) break;  /* incomplete frame — keep in buffer */

            int raw_len = end - i - 1;
            /* Validate: all bytes must be printable ASCII */
            int printable = 1;
            for (int j = i + 1; j < end; j++) {
                if (buf[j] < 0x20 || buf[j] > 0x7E) {
                    printable = 0;
                    break;
                }
            }

            if (printable && raw_len > 0 && raw_len < 64) {
                char content[128];
                memcpy(content, buf + i + 1, raw_len);
                content[raw_len] = '\0';

                char *colon = strchr(content, ':');
                if (colon) {
                    *colon = '\0';
                    strncpy(pairs[n].key, content, sizeof(pairs[n].key) - 1);
                    pairs[n].key[sizeof(pairs[n].key) - 1] = '\0';
                    strncpy(pairs[n].value, colon + 1, sizeof(pairs[n].value) - 1);
                    pairs[n].value[sizeof(pairs[n].value) - 1] = '\0';
                    n++;
                } else {
                    /* Bare key with no value, e.g. [amps] */
                    strncpy(pairs[n].key, content, sizeof(pairs[n].key) - 1);
                    pairs[n].key[sizeof(pairs[n].key) - 1] = '\0';
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

/* ── Build KV command ──────────────────────────────────────────────── *
 *
 * Builds a command string in the format the motor expects:
 *   [key:value]\xff   (if value is non-empty)
 *   [key]\xff         (if value is NULL or empty)
 *
 * Returns the length of the output (including the \xff byte).
 */

static int build_kv_cmd(char *out, int out_size, const char *key, const char *value) {
    int len;
    if (value && value[0]) {
        len = snprintf(out, out_size, "[%s:%s]", key, value);
    } else {
        len = snprintf(out, out_size, "[%s]", key);
    }
    if (len >= 0 && len < out_size - 1) {
        out[len] = (char)0xFF;
        len++;
        out[len] = '\0';
    }
    return len;
}

/* ── GPIO wave write (inverted RS-485 polarity) ───────────────────── *
 *
 * Sends bytes as bit-banged serial on a GPIO pin using pigpio's DMA
 * waveform engine. The polarity is INVERTED for RS-485:
 *   - Idle state: LOW  (standard UART idles HIGH)
 *   - Start bit:  HIGH (standard is LOW)
 *   - Data bits:  inverted (1→LOW, 0→HIGH)
 *   - Stop bit:   LOW  (standard is HIGH)
 *
 * Thread-safe: serialized by write_lock mutex. Blocks until the
 * waveform completes (synchronous write).
 */

static void gpio_write_bytes(int gpio, const unsigned char *data, int len) {
    if (len <= 0) return;

    uint32_t mask = 1 << gpio;
    /* 10 pulses per byte: 1 start + 8 data + 1 stop */
    gpioPulse_t pulses[len * 10 + 1];
    int np = 0;

    for (int b = 0; b < len; b++) {
        unsigned char byte_val = data[b];
        /* Start bit: HIGH (inverted) */
        pulses[np].gpioOn  = mask;
        pulses[np].gpioOff = 0;
        pulses[np].usDelay = BIT_US;
        np++;
        /* 8 data bits, LSB first, INVERTED */
        for (int bit = 0; bit < 8; bit++) {
            if ((byte_val >> bit) & 1) {
                pulses[np].gpioOn  = 0;
                pulses[np].gpioOff = mask;  /* 1 → LOW */
            } else {
                pulses[np].gpioOn  = mask;
                pulses[np].gpioOff = 0;     /* 0 → HIGH */
            }
            pulses[np].usDelay = BIT_US;
            np++;
        }
        /* Stop bit: LOW (inverted idle) */
        pulses[np].gpioOn  = 0;
        pulses[np].gpioOff = mask;
        pulses[np].usDelay = BIT_US;
        np++;
    }

    pthread_mutex_lock(&write_lock);

    while (gpioWaveTxBusy()) {
        sleep_ms(1);
    }

    gpioWaveClear();
    gpioWaveAddGeneric(np, pulses);
    int wid = gpioWaveCreate();
    if (wid >= 0) {
        gpioWaveTxSend(wid, PI_WAVE_MODE_ONE_SHOT);
        while (gpioWaveTxBusy()) {
            sleep_ms(1);
        }
        gpioWaveDelete(wid);
    }

    pthread_mutex_unlock(&write_lock);
}

/* ── JSON event helpers ────────────────────────────────────────────── *
 *
 * Push events into the ring buffer as JSON strings. The IPC thread
 * drains the ring and writes these to connected clients.
 */

static void push_kv_event(const char *source, const char *key, const char *value) {
    char msg[MSG_MAX];
    double ts = elapsed_sec();
    snprintf(msg, sizeof(msg),
             "{\"type\":\"kv\",\"ts\":%.2f,\"source\":\"%s\",\"key\":\"%s\",\"value\":\"%s\"}\n",
             ts, source, key, value);
    ring_push(&ring, msg);
}

static void push_status(void) {
    char msg[MSG_MAX];
    snprintf(msg, sizeof(msg),
             "{\"type\":\"status\",\"proxy\":%s,\"emulate\":%s,"
             "\"emu_speed\":%d,\"emu_incline\":%d,"
             "\"console_bytes\":%u,\"motor_bytes\":%u}\n",
             proxy_enabled ? "true" : "false",
             emulate_enabled ? "true" : "false",
             emu_speed, emu_incline,
             console_bytes, motor_bytes);
    ring_push(&ring, msg);
}

/* ── KV emulation cycle ───────────────────────────────────────────── *
 *
 * The real treadmill console sends a repeating 14-key command cycle
 * to the motor, grouped into 5 bursts with ~100ms gaps:
 *
 *   Burst 0: inc, hmph         (incline + speed)
 *   Burst 1: amps, err, belt
 *   Burst 2: vbus, lift, lfts, lftg
 *   Burst 3: part, ver, type
 *   Burst 4: diag, loop
 *
 * Keys with has_value=1 send [key:value]\xff; others send [key]\xff.
 * Speed is encoded as mph×100 in uppercase hex via the "hmph" key.
 */

typedef struct {
    const char *key;
    int has_value;   /* 1 = dynamic value, 0 = bare [key] command */
} kv_cycle_entry_t;

static const kv_cycle_entry_t KV_CYCLE[14] = {
    { "inc",  1 },    /*  0: incline (decimal int) */
    { "hmph", 1 },    /*  1: speed (mph×100, uppercase hex) */
    { "amps", 0 },    /*  2 */
    { "err",  0 },    /*  3 */
    { "belt", 0 },    /*  4 */
    { "vbus", 0 },    /*  5 */
    { "lift", 0 },    /*  6 */
    { "lfts", 0 },    /*  7 */
    { "lftg", 0 },    /*  8 */
    { "part", 1 },    /*  9: always "6" */
    { "ver",  0 },    /* 10 */
    { "type", 0 },    /* 11 */
    { "diag", 1 },    /* 12: always "0" */
    { "loop", 1 },    /* 13: always "5550" */
};

/* Which KV_CYCLE indices belong to each burst (-1 = end of burst) */
static const int BURSTS[5][4] = {
    { 0, 1, -1, -1 },        /* inc, hmph */
    { 2, 3, 4, -1 },         /* amps, err, belt */
    { 5, 6, 7, 8 },          /* vbus, lift, lfts, lftg */
    { 9, 10, 11, -1 },       /* part, ver, type */
    { 12, 13, -1, -1 },      /* diag, loop */
};

/* Return the value string for a given KV_CYCLE index during emulation */
static const char *emu_value_for(int idx) {
    static char vbuf[32];
    switch (idx) {
        case 0:  /* inc — decimal incline */
            snprintf(vbuf, sizeof(vbuf), "%d", emu_incline);
            return vbuf;
        case 1:  /* hmph — speed as mph×100, uppercase hex */
            snprintf(vbuf, sizeof(vbuf), "%X", emu_speed_raw);
            return vbuf;
        case 9:  return "6";     /* part — hardware type */
        case 12: return "0";     /* diag — no diagnostics */
        case 13: return "5550";  /* loop — heartbeat value */
        default: return NULL;
    }
}

/* ── Console read thread ───────────────────────────────────────────── *
 *
 * Reads serial data from the console (controller) on GPIO_CONSOLE_READ.
 * In proxy mode, forwards the raw bytes to the motor via GPIO_MOTOR_WRITE
 * BEFORE parsing — this keeps proxy latency minimal.
 * Parses KV pairs and pushes them to the ring buffer for IPC clients.
 */

static void *console_read_fn(void *arg) {
    (void)arg;

    int rc = gpioSerialReadOpen(GPIO_CONSOLE_READ, BAUD, 8);
    if (rc < 0) {
        fprintf(stderr, "[console] gpioSerialReadOpen failed: %d\n", rc);
        return NULL;
    }
    gpioSerialReadInvert(GPIO_CONSOLE_READ, 1);  /* RS-485 inverted polarity */

    unsigned char rawbuf[512];
    unsigned char parsebuf[4096];
    int parse_len = 0;

    while (running) {
        int count = gpioSerialRead(GPIO_CONSOLE_READ, rawbuf, sizeof(rawbuf));
        if (count > 0) {
            console_bytes += count;

            /* Proxy: forward raw bytes to motor BEFORE parsing (low latency) */
            if (proxy_enabled && !emulate_enabled) {
                gpio_write_bytes(GPIO_MOTOR_WRITE, rawbuf, count);
            }

            /* Append to parse buffer */
            int space = (int)sizeof(parsebuf) - parse_len;
            if (count > space) count = space;
            memcpy(parsebuf + parse_len, rawbuf, count);
            parse_len += count;

            /* Parse KV pairs from accumulated data */
            kv_pair_t pairs[32];
            int consumed = 0;
            int n = kv_parse(parsebuf, parse_len, pairs, 32, &consumed);

            for (int i = 0; i < n; i++) {
                push_kv_event("console", pairs[i].key, pairs[i].value);
            }

            /* Auto-detect: if console hmph/inc changes while in emulate mode,
             * the user pressed physical buttons — switch back to proxy */
            for (int i = 0; i < n; i++) {
                if (strcmp(pairs[i].key, "hmph") == 0 || strcmp(pairs[i].key, "inc") == 0) {
                    char *last = strcmp(pairs[i].key, "hmph") == 0 ? last_console_hmph : last_console_inc;
                    if (last[0] != '\0' && strcmp(last, pairs[i].value) != 0 && emulate_enabled) {
                        /* Console activity detected — user pressed physical buttons */
                        fprintf(stderr, "[auto] console %s changed %s -> %s, switching to proxy\n",
                                pairs[i].key, last, pairs[i].value);
                        stop_emulate();
                        proxy_enabled = 1;
                        push_status();
                    }
                    strncpy(last, pairs[i].value, 31);
                    last[31] = '\0';
                }
            }

            /* Shift unconsumed bytes to front of buffer */
            if (consumed > 0 && consumed < parse_len) {
                memmove(parsebuf, parsebuf + consumed, parse_len - consumed);
            }
            parse_len -= consumed;
        } else {
            sleep_ms(5);
        }
    }

    gpioSerialReadClose(GPIO_CONSOLE_READ);
    return NULL;
}

/* ── Motor read thread ─────────────────────────────────────────────── *
 *
 * Reads serial data from the motor on GPIO_MOTOR_READ (passive tap).
 * Parse-only — never writes to GPIO. Motor responses use the same
 * [key:value] format but without the \xff delimiter.
 */

static void *motor_read_fn(void *arg) {
    (void)arg;

    int rc = gpioSerialReadOpen(GPIO_MOTOR_READ, BAUD, 8);
    if (rc < 0) {
        fprintf(stderr, "[motor] gpioSerialReadOpen failed: %d\n", rc);
        return NULL;
    }
    gpioSerialReadInvert(GPIO_MOTOR_READ, 1);

    unsigned char rawbuf[512];
    unsigned char parsebuf[4096];
    int parse_len = 0;

    while (running) {
        int count = gpioSerialRead(GPIO_MOTOR_READ, rawbuf, sizeof(rawbuf));
        if (count > 0) {
            motor_bytes += count;

            int space = (int)sizeof(parsebuf) - parse_len;
            if (count > space) count = space;
            memcpy(parsebuf + parse_len, rawbuf, count);
            parse_len += count;

            kv_pair_t pairs[32];
            int consumed = 0;
            int n = kv_parse(parsebuf, parse_len, pairs, 32, &consumed);

            for (int i = 0; i < n; i++) {
                push_kv_event("motor", pairs[i].key, pairs[i].value);
            }

            if (consumed > 0 && consumed < parse_len) {
                memmove(parsebuf, parsebuf + consumed, parse_len - consumed);
            }
            parse_len -= consumed;
        } else {
            sleep_ms(5);
        }
    }

    gpioSerialReadClose(GPIO_MOTOR_READ);
    return NULL;
}

/* ── Emulate thread ────────────────────────────────────────────────── *
 *
 * Replaces the real console by sending a synthesized 14-key KV command
 * cycle to the motor. Runs only when emulate mode is enabled.
 * Sends 5 bursts per cycle with 100ms gaps (matching real console timing).
 *
 * Safety: automatically stops after EMU_TIMEOUT_SEC (3 hours),
 * resetting speed and incline to 0.
 */

#define EMU_TIMEOUT_SEC (3 * 3600)  /* 3 hours */

static void *emulate_fn(void *arg) {
    (void)arg;
    double start_time = elapsed_sec();

    while (running && emulate_enabled) {
        /* Safety timeout: reset speed/incline to 0 after 3 hours */
        if (elapsed_sec() - start_time >= EMU_TIMEOUT_SEC) {
            if (emu_speed != 0 || emu_incline != 0) {
                emu_speed = 0;
                emu_speed_raw = 0;
                emu_incline = 0;
                fprintf(stderr, "[emulate] 3-hour safety timeout — speed/incline reset to 0\n");
                push_status();
            }
        }

        for (int burst = 0; burst < 5; burst++) {
            if (!running || !emulate_enabled) goto done;

            for (int slot = 0; slot < 4; slot++) {
                int idx = BURSTS[burst][slot];
                if (idx < 0) break;
                if (!running || !emulate_enabled) goto done;

                const char *key = KV_CYCLE[idx].key;
                const char *value = NULL;
                if (KV_CYCLE[idx].has_value) {
                    value = emu_value_for(idx);
                }

                char cmd[128];
                int cmd_len = build_kv_cmd(cmd, sizeof(cmd), key, value);
                gpio_write_bytes(GPIO_MOTOR_WRITE,
                                 (unsigned char *)cmd, cmd_len);

                push_kv_event("emulate", key, value ? value : "");
            }
            sleep_ms(100);  /* ~100ms gap between bursts, matching real console */
        }
    }

done:
    pthread_mutex_lock(&emu_lock);
    emu_thread_running = 0;
    pthread_mutex_unlock(&emu_lock);
    return NULL;
}

/* Stop emulate thread and wait for it to exit */
static void stop_emulate(void) {
    emulate_enabled = 0;
    pthread_mutex_lock(&emu_lock);
    int was_running = emu_thread_running;
    pthread_mutex_unlock(&emu_lock);
    if (was_running) {
        pthread_join(emu_thread_id, NULL);
    }
}

/* Start emulate thread (joins any existing one first) */
static void start_emulate_thread(void) {
    stop_emulate();
    /* Safety: always start emulate at 0 speed, 0 incline */
    emu_speed = 0;
    emu_speed_raw = 0;
    emu_incline = 0;
    emulate_enabled = 1;
    pthread_mutex_lock(&emu_lock);
    emu_thread_running = 1;
    pthread_mutex_unlock(&emu_lock);
    pthread_create(&emu_thread_id, NULL, emulate_fn, NULL);
}

/* ── IPC: command dispatch ─────────────────────────────────────────── *
 *
 * Parses JSON commands from clients using strstr() pattern matching.
 * This is intentionally simple — only 6 command types, and the JSON
 * is always compact (no spaces), so strstr is reliable.
 */

static void handle_command(const char *line) {
    if (strstr(line, "\"cmd\":\"proxy\"")) {
        if (strstr(line, "\"enabled\":true")) {
            stop_emulate();
            proxy_enabled = 1;
        } else if (strstr(line, "\"enabled\":false")) {
            proxy_enabled = 0;
        }
        push_status();
    }
    else if (strstr(line, "\"cmd\":\"emulate\"")) {
        if (strstr(line, "\"enabled\":true")) {
            proxy_enabled = 0;
            start_emulate_thread();
        } else if (strstr(line, "\"enabled\":false")) {
            stop_emulate();
        }
        push_status();
    }
    else if (strstr(line, "\"cmd\":\"speed\"")) {
        double mph = 0;
        const char *vp = strstr(line, "\"value\":");
        if (vp) {
            sscanf(vp + 8, "%lf", &mph);
            int tenths = (int)(mph * 10 + 0.5);
            if (tenths < 0) tenths = 0;
            if (tenths > MAX_SPEED_TENTHS) tenths = MAX_SPEED_TENTHS;
            /* Auto-enable emulate when speed is set */
            if (!emulate_enabled) {
                proxy_enabled = 0;
                start_emulate_thread();
            }
            emu_speed = tenths;
            emu_speed_raw = tenths * 10;
        }
        push_status();
    }
    else if (strstr(line, "\"cmd\":\"incline\"")) {
        int val = 0;
        const char *vp = strstr(line, "\"value\":");
        if (vp) {
            sscanf(vp + 8, "%d", &val);
            if (val < 0) val = 0;
            if (val > MAX_INCLINE) val = MAX_INCLINE;
            /* Auto-enable emulate when incline is set */
            if (!emulate_enabled) {
                proxy_enabled = 0;
                start_emulate_thread();
            }
            emu_incline = val;
        }
        push_status();
    }
    else if (strstr(line, "\"cmd\":\"status\"")) {
        push_status();
    }
    else if (strstr(line, "\"cmd\":\"quit\"")) {
        running = 0;
    }
}

/* ── IPC: Unix socket server ───────────────────────────────────────── *
 *
 * Manages up to MAX_CLIENTS simultaneous connections on a Unix domain
 * socket. Uses select() with a 20ms timeout for the event loop.
 * Each client has a per-connection command buffer for reading JSON
 * commands, and a ring_cursor tracking which ring buffer messages
 * have been sent to them.
 */

typedef struct {
    int fd;
    char buf[CMD_BUF_SIZE];
    int buf_len;
    unsigned int ring_cursor;  /* next ring sequence number to send */
} client_t;

static int server_fd = -1;
static client_t clients[MAX_CLIENTS];
static int num_clients = 0;

static int create_server_socket(void) {
    struct sockaddr_un addr;

    unlink(SOCK_PATH);

    int fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0) {
        perror("socket");
        return -1;
    }

    memset(&addr, 0, sizeof(addr));
    addr.sun_family = AF_UNIX;
    strncpy(addr.sun_path, SOCK_PATH, sizeof(addr.sun_path) - 1);

    if (bind(fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        perror("bind");
        close(fd);
        return -1;
    }

    chmod(SOCK_PATH, 0777);  /* allow non-root clients to connect */

    if (listen(fd, MAX_CLIENTS) < 0) {
        perror("listen");
        close(fd);
        return -1;
    }

    int flags = fcntl(fd, F_GETFL, 0);
    fcntl(fd, F_SETFL, flags | O_NONBLOCK);

    return fd;
}

static void remove_client(int idx) {
    close(clients[idx].fd);
    for (int i = idx; i < num_clients - 1; i++) {
        clients[i] = clients[i + 1];
    }
    num_clients--;
}

static void accept_client(void) {
    int cfd = accept(server_fd, NULL, NULL);
    if (cfd < 0) return;

    if (num_clients >= MAX_CLIENTS) {
        const char *err = "{\"type\":\"error\",\"msg\":\"too many clients\"}\n";
        (void)write(cfd, err, strlen(err));
        close(cfd);
        return;
    }

    int flags = fcntl(cfd, F_GETFL, 0);
    fcntl(cfd, F_SETFL, flags | O_NONBLOCK);

    client_t *c = &clients[num_clients];
    c->fd = cfd;
    c->buf_len = 0;
    /* Start at current ring position (don't replay old events) */
    pthread_mutex_lock(&ring.lock);
    c->ring_cursor = ring.count;
    pthread_mutex_unlock(&ring.lock);
    num_clients++;

    push_status();  /* send initial status to new client */

    fprintf(stderr, "[ipc] client connected (fd=%d, total=%d)\n",
            cfd, num_clients);
}

/* Read and process commands from a client */
static void read_client(int idx) {
    client_t *c = &clients[idx];
    int space = CMD_BUF_SIZE - c->buf_len - 1;
    if (space <= 0) {
        c->buf_len = 0;  /* buffer full with no newline — discard */
        space = CMD_BUF_SIZE - 1;
    }

    ssize_t n = read(c->fd, c->buf + c->buf_len, space);
    if (n <= 0) {
        fprintf(stderr, "[ipc] client disconnected (fd=%d)\n", c->fd);
        remove_client(idx);
        return;
    }

    c->buf_len += n;
    c->buf[c->buf_len] = '\0';

    /* Process complete newline-delimited JSON commands */
    char *start = c->buf;
    char *nl;
    while ((nl = strchr(start, '\n')) != NULL) {
        *nl = '\0';
        if (nl > start) {
            handle_command(start);
        }
        start = nl + 1;
    }

    /* Shift unprocessed data to front of buffer */
    int remaining = c->buf_len - (int)(start - c->buf);
    if (remaining > 0 && start != c->buf) {
        memmove(c->buf, start, remaining);
    }
    c->buf_len = remaining;
}

/*
 * Drain new messages from the ring buffer to all connected clients.
 * Uses unsigned arithmetic for sequence tracking (wraps correctly).
 * If a client falls behind by more than RING_SIZE messages, skips
 * to the current position (oldest messages are lost for that client).
 */
static void flush_ring_to_clients(void) {
    pthread_mutex_lock(&ring.lock);
    int head = ring.head;
    unsigned int total = ring.count;
    pthread_mutex_unlock(&ring.lock);

    for (int ci = 0; ci < num_clients; /* no increment */) {
        client_t *c = &clients[ci];

        unsigned int pending = total - c->ring_cursor;
        if (pending == 0) { ci++; continue; }
        if (pending > RING_SIZE) {
            /* Client fell too far behind — skip to current */
            c->ring_cursor = total - RING_SIZE;
            pending = RING_SIZE;
        }

        int start_idx = (head - (int)pending + RING_SIZE) % RING_SIZE;
        int failed = 0;

        for (unsigned int i = 0; i < pending && !failed; i++) {
            int ri = (start_idx + i) % RING_SIZE;
            const char *msg = ring.msgs[ri];
            int msg_len = (int)strlen(msg);
            if (msg_len > 0) {
                ssize_t w = write(c->fd, msg, msg_len);
                if (w < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
                    break;  /* client socket full — skip remaining */
                } else if (w <= 0) {
                    failed = 1;  /* client gone */
                }
            }
        }

        if (failed) {
            fprintf(stderr, "[ipc] client write error (fd=%d)\n", c->fd);
            remove_client(ci);
        } else {
            c->ring_cursor = total;
            ci++;
        }
    }
}

/* ── IPC event loop thread ─────────────────────────────────────────── */

static void *ipc_fn(void *arg) {
    (void)arg;

    server_fd = create_server_socket();
    if (server_fd < 0) {
        fprintf(stderr, "Failed to create server socket\n");
        running = 0;
        return NULL;
    }

    fprintf(stderr, "[ipc] listening on %s\n", SOCK_PATH);

    while (running) {
        fd_set rfds;
        FD_ZERO(&rfds);
        FD_SET(server_fd, &rfds);
        int maxfd = server_fd;

        for (int i = 0; i < num_clients; i++) {
            FD_SET(clients[i].fd, &rfds);
            if (clients[i].fd > maxfd) maxfd = clients[i].fd;
        }

        struct timeval tv = { 0, 20000 };  /* 20ms poll interval */
        int sel = select(maxfd + 1, &rfds, NULL, NULL, &tv);

        if (sel > 0) {
            if (FD_ISSET(server_fd, &rfds)) {
                accept_client();
            }

            for (int i = 0; i < num_clients; /* no increment */) {
                if (FD_ISSET(clients[i].fd, &rfds)) {
                    int prev = num_clients;
                    read_client(i);
                    if (num_clients < prev) {
                        continue;  /* client removed, don't increment */
                    }
                }
                i++;
            }
        }

        flush_ring_to_clients();
    }

    /* Cleanup */
    for (int i = 0; i < num_clients; i++) {
        close(clients[i].fd);
    }
    num_clients = 0;

    close(server_fd);
    unlink(SOCK_PATH);
    server_fd = -1;
    return NULL;
}

/* ── Signal handling ───────────────────────────────────────────────── */

static void sig_handler(int sig) {
    (void)sig;
    running = 0;
}

/* ── Main ──────────────────────────────────────────────────────────── */

int main(void) {
    if (geteuid() != 0) {
        fprintf(stderr, "Error: must run as root (sudo ./treadmill_io)\n");
        return 1;
    }

    fprintf(stderr, "treadmill_io starting...\n");

    if (load_gpio_config() < 0) return 1;

    fprintf(stderr, "  Console read: GPIO %d\n", GPIO_CONSOLE_READ);
    fprintf(stderr, "  Motor write:  GPIO %d\n", GPIO_MOTOR_WRITE);
    fprintf(stderr, "  Motor read:   GPIO %d\n", GPIO_MOTOR_READ);
    fprintf(stderr, "  Baud:         %d\n", BAUD);

    /* Initialize pigpio in library mode (conflicts with pigpiod) */
    if (gpioInitialise() < 0) {
        fprintf(stderr, "Failed to initialize pigpio (is pigpiod running? kill it first)\n");
        return 1;
    }

    /* Motor write pin: output, idle LOW (inverted RS-485) */
    gpioSetMode(GPIO_MOTOR_WRITE, PI_OUTPUT);
    gpioWrite(GPIO_MOTOR_WRITE, 0);

    clock_gettime(CLOCK_MONOTONIC, &start_ts);
    ring_init(&ring);

    signal(SIGINT, sig_handler);
    signal(SIGTERM, sig_handler);
    signal(SIGPIPE, SIG_IGN);

    pthread_t t_console, t_motor, t_ipc;

    pthread_create(&t_console, NULL, console_read_fn, NULL);
    pthread_create(&t_motor, NULL, motor_read_fn, NULL);
    pthread_create(&t_ipc, NULL, ipc_fn, NULL);

    fprintf(stderr, "treadmill_io ready (proxy=%s)\n",
            proxy_enabled ? "on" : "off");

    while (running) {
        sleep_ms(200);
    }

    fprintf(stderr, "\nShutting down...\n");

    stop_emulate();

    pthread_join(t_console, NULL);
    pthread_join(t_motor, NULL);
    pthread_join(t_ipc, NULL);

    gpioWrite(GPIO_MOTOR_WRITE, 0);
    gpioSetMode(GPIO_MOTOR_WRITE, PI_INPUT);

    gpioTerminate();

    fprintf(stderr, "treadmill_io stopped.\n");
    return 0;
}
