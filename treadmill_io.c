/*
 * treadmill_io.c — Low-latency GPIO I/O for Precor 9.3x treadmill
 *
 * Links libpigpio directly (no daemon hop). Reads RS-485 KV protocol
 * from console (GPIO 27) and motor (GPIO 17), proxies/emulates via
 * GPIO 22, and serves parsed data over a Unix domain socket.
 *
 * Build: make  (or: gcc -Wall -O2 -pthread -o treadmill_io treadmill_io.c -lpigpio -lrt -pthread)
 * Run:   sudo ./treadmill_io
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

/* ── GPIO pins (from gpio.json) ────────────────────────────────────── */

#define GPIO_CONSOLE_READ  27   /* Pin 6 console side — reads from controller */
#define GPIO_MOTOR_WRITE   22   /* Pin 6 motor side — proxy/emulate output */
#define GPIO_MOTOR_READ    17   /* Pin 3 — reads responses from motor */

#define BAUD  9600
#define BIT_US (1000000 / BAUD)  /* ~104 µs per bit */

/* ── IPC ───────────────────────────────────────────────────────────── */

#define SOCK_PATH "/tmp/treadmill_io.sock"
#define MAX_CLIENTS 4
#define CMD_BUF_SIZE 1024

/* ── Ring buffer for KV events ─────────────────────────────────────── */

#define RING_SIZE 2048
#define MSG_MAX  256

typedef struct {
    char msgs[RING_SIZE][MSG_MAX];
    int  head;   /* next write position */
    int  count;  /* total written (for sequence tracking) */
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

/* ── Shared state ──────────────────────────────────────────────────── */

static volatile int running = 1;
static volatile int proxy_enabled = 1;
static volatile int emulate_enabled = 0;
static volatile int emu_speed = 0;        /* tenths of mph (12 = 1.2 mph) */
static volatile int emu_speed_raw = 0;    /* hundredths, hex-encoded (120) */
static volatile int emu_incline = 0;

static volatile uint64_t console_bytes = 0;
static volatile uint64_t motor_bytes = 0;

static pthread_mutex_t write_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_mutex_t emu_lock   = PTHREAD_MUTEX_INITIALIZER;

static struct timespec start_ts;
static pthread_t emu_thread_id;
static volatile int emu_thread_running = 0;

/* ── Timing helper ─────────────────────────────────────────────────── */

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

/* ── KV parser (port of parse_kv_stream) ───────────────────────────── */

typedef struct {
    char key[64];
    char value[64];
} kv_pair_t;

/*
 * Parse [key:value] pairs from buf of length len.
 * Writes up to max_pairs results into pairs[].
 * Returns number of pairs found. *consumed = bytes consumed from buf.
 */
static int kv_parse(const unsigned char *buf, int len,
                    kv_pair_t *pairs, int max_pairs, int *consumed)
{
    int i = 0, n = 0;

    while (i < len && n < max_pairs) {
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
            if (end == -1) break;  /* incomplete — keep in buffer */

            int raw_len = end - i - 1;
            /* Check all printable ASCII */
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

/* ── Build KV command: [key:value]\xff ──────────────────────────────── */

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

/* ── GPIO wave write (inverted RS-485 polarity) ───────────────────── */

static void gpio_write_bytes(int gpio, const unsigned char *data, int len) {
    if (len <= 0) return;

    uint32_t mask = 1 << gpio;
    gpioPulse_t pulses[len * 10 + 1];
    int np = 0;

    for (int b = 0; b < len; b++) {
        unsigned char byte_val = data[b];
        /* Start bit: HIGH (inverted from standard LOW) */
        pulses[np].gpioOn  = mask;
        pulses[np].gpioOff = 0;
        pulses[np].usDelay = BIT_US;
        np++;
        /* 8 data bits, LSB first, INVERTED */
        for (int bit = 0; bit < 8; bit++) {
            if ((byte_val >> bit) & 1) {
                /* 1 → LOW (inverted) */
                pulses[np].gpioOn  = 0;
                pulses[np].gpioOff = mask;
            } else {
                /* 0 → HIGH (inverted) */
                pulses[np].gpioOn  = mask;
                pulses[np].gpioOff = 0;
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

    /* Wait for any previous wave to finish */
    while (gpioWaveTxBusy()) {
        sleep_ms(1);
    }

    gpioWaveAddNew();
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

/* ── Push a KV event as JSON into the ring buffer ──────────────────── */

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
             "\"console_bytes\":%llu,\"motor_bytes\":%llu}\n",
             proxy_enabled ? "true" : "false",
             emulate_enabled ? "true" : "false",
             emu_speed, emu_incline,
             (unsigned long long)console_bytes,
             (unsigned long long)motor_bytes);
    ring_push(&ring, msg);
}

/* ── KV emulation cycle (port of KV_CYCLE / KV_BURSTS) ────────────── */

/* 14-key cycle: key name, has_value flag (0=send bare [key]) */
typedef struct {
    const char *key;
    int has_value;   /* 1 = dynamic value, 0 = bare key */
} kv_cycle_entry_t;

static const kv_cycle_entry_t KV_CYCLE[14] = {
    { "inc",  1 },
    { "hmph", 1 },
    { "amps", 0 },
    { "err",  0 },
    { "belt", 0 },
    { "vbus", 0 },
    { "lift", 0 },
    { "lfts", 0 },
    { "lftg", 0 },
    { "part", 1 },
    { "ver",  0 },
    { "type", 0 },
    { "diag", 1 },
    { "loop", 1 },
};

/* Burst groupings */
static const int BURSTS[5][4] = {
    { 0, 1, -1, -1 },        /* inc, hmph */
    { 2, 3, 4, -1 },         /* amps, err, belt */
    { 5, 6, 7, 8 },          /* vbus, lift, lfts, lftg */
    { 9, 10, 11, -1 },       /* part, ver, type */
    { 12, 13, -1, -1 },      /* diag, loop */
};

static const char *emu_value_for(int idx) {
    static char vbuf[32];
    switch (idx) {
        case 0:  /* inc */
            snprintf(vbuf, sizeof(vbuf), "%d", emu_incline);
            return vbuf;
        case 1:  /* hmph */
            snprintf(vbuf, sizeof(vbuf), "%X", emu_speed_raw);
            return vbuf;
        case 9:  /* part */
            return "6";
        case 12: /* diag */
            return "0";
        case 13: /* loop */
            return "5550";
        default:
            return NULL;
    }
}

/* ── Console read thread (GPIO 27) ─────────────────────────────────── */

static void *console_read_fn(void *arg) {
    (void)arg;

    /* Open bit-banged serial with inverted polarity */
    gpioSerialReadOpen(GPIO_CONSOLE_READ, BAUD, 8);
    gpioSerialReadInvert(GPIO_CONSOLE_READ, 1);

    unsigned char rawbuf[512];
    unsigned char parsebuf[4096];
    int parse_len = 0;

    while (running) {
        int count = gpioSerialRead(GPIO_CONSOLE_READ, rawbuf, sizeof(rawbuf));
        if (count > 0) {
            console_bytes += count;

            /* Proxy: forward raw bytes to motor BEFORE parsing */
            if (proxy_enabled && !emulate_enabled) {
                gpio_write_bytes(GPIO_MOTOR_WRITE, rawbuf, count);
            }

            /* Append to parse buffer */
            int space = (int)sizeof(parsebuf) - parse_len;
            if (count > space) count = space;
            memcpy(parsebuf + parse_len, rawbuf, count);
            parse_len += count;

            /* Parse KV pairs */
            kv_pair_t pairs[32];
            int consumed = 0;
            int n = kv_parse(parsebuf, parse_len, pairs, 32, &consumed);

            for (int i = 0; i < n; i++) {
                push_kv_event("console", pairs[i].key, pairs[i].value);
            }

            /* Shift unconsumed data to front */
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

/* ── Motor read thread (GPIO 17) ───────────────────────────────────── */

static void *motor_read_fn(void *arg) {
    (void)arg;

    gpioSerialReadOpen(GPIO_MOTOR_READ, BAUD, 8);
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

/* ── Emulate thread (GPIO 22) ──────────────────────────────────────── */

static void *emulate_fn(void *arg) {
    (void)arg;

    while (running && emulate_enabled) {
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
            sleep_ms(100);
        }
    }

done:
    pthread_mutex_lock(&emu_lock);
    emu_thread_running = 0;
    pthread_mutex_unlock(&emu_lock);
    return NULL;
}

static void start_emulate_thread(void) {
    pthread_mutex_lock(&emu_lock);
    if (emu_thread_running) {
        pthread_mutex_unlock(&emu_lock);
        return;
    }
    emu_thread_running = 1;
    pthread_mutex_unlock(&emu_lock);

    pthread_create(&emu_thread_id, NULL, emulate_fn, NULL);
    pthread_detach(emu_thread_id);
}

static void stop_emulate(void) {
    emulate_enabled = 0;
    /* Thread will exit on its own */
}

/* ── IPC: command dispatch ─────────────────────────────────────────── */

static void handle_command(const char *line) {
    /* Parse simple JSON commands using strstr/sscanf */

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
            emulate_enabled = 1;
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
            if (tenths > 120) tenths = 120;
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
            if (val > 99) val = 99;
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

/* ── IPC: Unix socket server ───────────────────────────────────────── */

typedef struct {
    int fd;
    char buf[CMD_BUF_SIZE];
    int buf_len;
    int ring_cursor;  /* sequence number of last sent msg */
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

    /* Allow non-root clients to connect */
    chmod(SOCK_PATH, 0777);

    if (listen(fd, MAX_CLIENTS) < 0) {
        perror("listen");
        close(fd);
        return -1;
    }

    /* Non-blocking for select */
    int flags = fcntl(fd, F_GETFL, 0);
    fcntl(fd, F_SETFL, flags | O_NONBLOCK);

    return fd;
}

static void remove_client(int idx) {
    close(clients[idx].fd);
    /* Shift remaining clients down */
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
    /* Start at current ring head so we don't replay old events */
    pthread_mutex_lock(&ring.lock);
    c->ring_cursor = ring.count;
    pthread_mutex_unlock(&ring.lock);
    num_clients++;

    /* Send initial status */
    push_status();

    fprintf(stderr, "[ipc] client connected (fd=%d, total=%d)\n",
            cfd, num_clients);
}

static void read_client(int idx) {
    client_t *c = &clients[idx];
    int space = CMD_BUF_SIZE - c->buf_len - 1;
    if (space <= 0) {
        /* Buffer full with no newline — discard */
        c->buf_len = 0;
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

    /* Process complete lines */
    char *start = c->buf;
    char *nl;
    while ((nl = strchr(start, '\n')) != NULL) {
        *nl = '\0';
        if (nl > start) {
            handle_command(start);
        }
        start = nl + 1;
    }

    /* Shift unprocessed data to front */
    int remaining = c->buf_len - (int)(start - c->buf);
    if (remaining > 0 && start != c->buf) {
        memmove(c->buf, start, remaining);
    }
    c->buf_len = remaining;
}

static void flush_ring_to_clients(void) {
    pthread_mutex_lock(&ring.lock);
    int head = ring.head;
    int total = ring.count;
    pthread_mutex_unlock(&ring.lock);

    for (int ci = 0; ci < num_clients; /* no increment */) {
        client_t *c = &clients[ci];

        /* How many new messages? */
        int pending = total - c->ring_cursor;
        if (pending <= 0) { ci++; continue; }
        if (pending > RING_SIZE) {
            /* Client fell too far behind — skip to current */
            c->ring_cursor = total - RING_SIZE;
            pending = RING_SIZE;
        }

        int start_idx = (head - pending + RING_SIZE) % RING_SIZE;
        int failed = 0;

        for (int i = 0; i < pending && !failed; i++) {
            int ri = (start_idx + i) % RING_SIZE;
            const char *msg = ring.msgs[ri];
            int msg_len = (int)strlen(msg);
            if (msg_len > 0) {
                ssize_t w = write(c->fd, msg, msg_len);
                if (w < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
                    /* Client can't keep up — skip remaining */
                    break;
                } else if (w <= 0) {
                    /* Client gone */
                    failed = 1;
                }
            }
        }

        if (failed) {
            fprintf(stderr, "[ipc] client write error (fd=%d)\n", c->fd);
            remove_client(ci);
            /* Don't increment ci — next client shifted into this slot */
        } else {
            c->ring_cursor = total;
            ci++;
        }
    }
}

/* ── IPC thread ────────────────────────────────────────────────────── */

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

        struct timeval tv = { 0, 20000 };  /* 20ms timeout */
        int sel = select(maxfd + 1, &rfds, NULL, NULL, &tv);

        if (sel > 0) {
            /* Accept new connections */
            if (FD_ISSET(server_fd, &rfds)) {
                accept_client();
            }

            /* Read from existing clients */
            for (int i = 0; i < num_clients; /* no increment */) {
                if (FD_ISSET(clients[i].fd, &rfds)) {
                    int prev = num_clients;
                    read_client(i);
                    if (num_clients < prev) {
                        /* Client was removed, don't increment */
                        continue;
                    }
                }
                i++;
            }
        }

        /* Flush pending ring buffer messages to all clients */
        flush_ring_to_clients();
    }

    /* Cleanup clients */
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
    /* Must run as root for GPIO access */
    if (geteuid() != 0) {
        fprintf(stderr, "Error: must run as root (sudo ./treadmill_io)\n");
        return 1;
    }

    fprintf(stderr, "treadmill_io starting...\n");
    fprintf(stderr, "  Console read: GPIO %d\n", GPIO_CONSOLE_READ);
    fprintf(stderr, "  Motor write:  GPIO %d\n", GPIO_MOTOR_WRITE);
    fprintf(stderr, "  Motor read:   GPIO %d\n", GPIO_MOTOR_READ);
    fprintf(stderr, "  Baud:         %d\n", BAUD);

    /* Initialize pigpio (library mode, not daemon) */
    if (gpioInitialise() < 0) {
        fprintf(stderr, "Failed to initialize pigpio\n");
        return 1;
    }

    /* Setup write pin: output, idle LOW (inverted RS-485) */
    gpioSetMode(GPIO_MOTOR_WRITE, PI_OUTPUT);
    gpioWrite(GPIO_MOTOR_WRITE, 0);

    clock_gettime(CLOCK_MONOTONIC, &start_ts);
    ring_init(&ring);

    signal(SIGINT, sig_handler);
    signal(SIGTERM, sig_handler);
    signal(SIGPIPE, SIG_IGN);

    /* Start threads */
    pthread_t t_console, t_motor, t_ipc;

    pthread_create(&t_console, NULL, console_read_fn, NULL);
    pthread_create(&t_motor, NULL, motor_read_fn, NULL);
    pthread_create(&t_ipc, NULL, ipc_fn, NULL);

    fprintf(stderr, "treadmill_io ready (proxy=%s)\n",
            proxy_enabled ? "on" : "off");

    /* Main thread just waits */
    while (running) {
        sleep_ms(200);
    }

    fprintf(stderr, "\nShutting down...\n");

    /* Stop emulate if running */
    stop_emulate();

    /* Wait for threads */
    pthread_join(t_console, NULL);
    pthread_join(t_motor, NULL);
    pthread_join(t_ipc, NULL);

    /* Reset write pin */
    gpioWrite(GPIO_MOTOR_WRITE, 0);
    gpioSetMode(GPIO_MOTOR_WRITE, PI_INPUT);

    gpioTerminate();

    fprintf(stderr, "treadmill_io stopped.\n");
    return 0;
}
