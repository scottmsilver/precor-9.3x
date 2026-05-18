#!/usr/bin/env bash
set -euo pipefail

# ship-check.sh — "is this device ready to ship?" acceptance gate.
#
# Runs the full integration stack against a real Pi + treadmill and prints
# ONE verdict: READY TO SHIP / NOT READY. Exits 0 only if every required
# check passes AND the belt is confirmed stopped at the end.
#
# Layers exercised (lowest first, same order we validate by hand):
#   L0  services active + treadmill-io never-restarted + ports listening
#   L0  memory headroom (MemAvailable >= 40 MB, 0 oom-kills)
#   L1  C++ treadmill_io: REST /api/speed emulate -> motor drives belt -> stop
#   L2  FTMS Control Point (BLE-equivalent) -> belt drives -> stop
#   L3  HRM daemon healthy (graceful with no strap)
#   L4  Python program engine (the UI's path) -> belt drives -> stop
#
# SAFETY:
#   * Belt-moving layers (L1/L2/L4) run ONLY with --belt-clear (operator
#     asserts nobody is on the belt and it is physically clear).
#   * --no-belt runs only the non-moving checks (L0/L2-read/L3) — use when
#     no treadmill is attached or the belt cannot be cleared.
#   * Low fixed test speed (1.0 mph), short bursts.
#   * A trap ALWAYS restores a stopped + proxy safe state on exit, Ctrl-C,
#     failure, or dropped SSH — and the verdict is NOT READY unless the
#     belt is confirmed at zero.
#
# Usage:
#   deploy/ship-check.sh --belt-clear          # full L0-L4 (treadmill attached, belt clear)
#   deploy/ship-check.sh --no-belt             # L0 + FTMS-read + L3 only
#   PI_HOST=rpi deploy/ship-check.sh --belt-clear
#
# Honors the same env as deploy.sh: PI_HOST (default rpi-zero), PI_DIR,
# SERVER_PORT.

cd "$(dirname "$0")/.."

PI_HOST="${PI_HOST:-rpi-zero}"
PI_DIR="${PI_DIR:-treadmill}"
SERVER_PORT="${SERVER_PORT:-8000}"

BELT_CLEAR=0
NO_BELT=0
for arg in "$@"; do
  case "$arg" in
    --belt-clear) BELT_CLEAR=1 ;;
    --no-belt)    NO_BELT=1 ;;
    -h|--help)
      sed -n '3,33p' "$0"; exit 0 ;;
    *) echo "unknown arg: $arg (try --help)" >&2; exit 2 ;;
  esac
done

if [ "$NO_BELT" -eq 0 ] && [ "$BELT_CLEAR" -eq 0 ]; then
  cat >&2 <<'EOF'
REFUSING: belt-safety gate.

This script can drive the belt to verify L1/L2/L4 integration. You must
explicitly choose one:

  --belt-clear   nobody is on the belt, it is physically clear, run the
                 full L0-L4 hardware acceptance (drives belt at 1.0 mph
                 in short bursts; always stops it afterward)

  --no-belt      no treadmill attached / belt cannot be cleared — run
                 only the non-moving checks (services, ports, headroom,
                 FTMS read path, HRM health)
EOF
  exit 2
fi

RUN_BELT=$BELT_CLEAR   # 1 => exercise belt-moving layers

echo "=== ship-check: $PI_HOST (belt-moving layers: $([ $RUN_BELT -eq 1 ] && echo ON || echo OFF)) ==="

# Local backstop: if the SSH payload is killed before its own finally runs
# (dropped link, local Ctrl-C), fire a best-effort remote safe-stop so the
# belt is never left moving.
remote_safe_stop() {
  ssh -o ConnectTimeout=5 "$PI_HOST" 'python3 - <<PYSAFE
import json,urllib.request
def post(p,b):
    try:
        r=urllib.request.Request("http://127.0.0.1:'"$SERVER_PORT"'"+p,
            data=json.dumps(b).encode(),headers={"Content-Type":"application/json"},method="POST")
        urllib.request.urlopen(r,timeout=5).read()
    except Exception: pass
post("/api/speed",{"value":0})
post("/api/program/stop",{})
post("/api/emulate",{"enabled":False})
post("/api/proxy",{"enabled":True})
PYSAFE' >/dev/null 2>&1 || true
}
trap 'remote_safe_stop' EXIT INT TERM

# All probing runs ON the Pi (treadmill_io is a Pi-local unix socket; the
# server may be plain HTTP here with no tailscale cert). One heredoc,
# structured output, own try/finally safe-stop, own exit code.
set +e
ssh "$PI_HOST" "SHIP_BELT=$RUN_BELT SERVER_PORT=$SERVER_PORT python3 - " <<'PYEOF'
import json, os, socket, sys, time, urllib.request

BELT = os.environ.get("SHIP_BELT") == "1"
PORT = os.environ.get("SERVER_PORT", "8000")
BASE = "http://127.0.0.1:%s" % PORT
SOCK = "/tmp/treadmill_io.sock"

fails, warns = [], []
def ok(tag, msg=""):   print("  PASS  %-26s %s" % (tag, msg))
def bad(tag, msg=""):  print("  FAIL  %-26s %s" % (tag, msg)); fails.append(tag)
def warn(tag, msg=""): print("  WARN  %-26s %s" % (tag, msg)); warns.append(tag)

def http(method, path, body=None, timeout=8):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data,
        headers={"Content-Type": "application/json"}, method=method)
    return urllib.request.urlopen(r, timeout=timeout).read().decode()

def cpp_status():
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(SOCK); s.settimeout(3)
    s.sendall(b'{"cmd":"status"}\n')
    buf = b""; t = time.time()
    while time.time() - t < 3:
        try: c = s.recv(4096)
        except socket.timeout: break
        if not c: break
        buf += c
        if b"\n" in buf: break
    s.close()
    for ln in buf.split(b"\n"):
        ln = ln.strip()
        if ln:
            try: return json.loads(ln)
            except Exception: pass
    return {}

def ftms(cmds, port=8826):
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    s.settimeout(2); out = b""
    def drain():
        nonlocal out
        try:
            while True:
                c = s.recv(4096)
                if not c: break
                out += c
        except socket.timeout: pass
    drain()
    for c in cmds:
        s.sendall(c.encode() + b"\n"); time.sleep(0.5); drain()
    s.close()
    return out.decode(errors="replace")

def port_open(p):
    try:
        socket.create_connection(("127.0.0.1", p), timeout=3).close()
        return True
    except Exception:
        return False

def wait_moving(timeout=14):
    """True once emulate is driving ~1.0 mph and the motor bus confirms it."""
    t = time.time()
    while time.time() - t < timeout:
        d = cpp_status()
        if d.get("emulate") and d.get("emu_speed", 0) in (9, 10, 11) \
           and d.get("bus_speed", 0) >= 8:
            return True, d
        time.sleep(2)
    return False, cpp_status()

def safe_stop(timeout=22):
    """Idempotent: zero speed, exit emulate, proxy on, then PROVE bus==0
    via a live re-read (proxy keeps the C++ bus decode active)."""
    for p, b in (("/api/speed", {"value": 0}),
                 ("/api/program/stop", {}),
                 ("/api/emulate", {"enabled": False}),
                 ("/api/proxy", {"enabled": True})):
        try: http("POST", p, b)
        except Exception: pass
    t = time.time()
    while time.time() - t < timeout:
        d = cpp_status()
        if d.get("emu_speed", 1) == 0 and d.get("bus_speed", 1) == 0:
            return True, d
        time.sleep(2)
    return False, cpp_status()

# ---------------------------------------------------------------- L0
print("L0  services / ports / headroom")
import subprocess
for svc in ("treadmill-io", "treadmill-server", "ftms", "hrm"):
    act = subprocess.run(["systemctl", "is-active", svc],
                          capture_output=True, text=True).stdout.strip()
    nr = subprocess.run(["systemctl", "show", svc, "-p", "NRestarts", "--value"],
                         capture_output=True, text=True).stdout.strip() or "?"
    if act != "active":
        bad(svc, "is-active=%s" % act)
    elif svc == "treadmill-io" and nr not in ("0",):
        bad(svc, "active but NRestarts=%s (safety daemon must never restart)" % nr)
    elif nr.isdigit() and int(nr) > 5:
        warn(svc, "active but NRestarts=%s (crash-looping?)" % nr)
    else:
        ok(svc, "active NRestarts=%s" % nr)

try:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); s.connect(SOCK); s.close()
    ok("treadmill_io.sock", "connectable")
except Exception as e:
    bad("treadmill_io.sock", str(e))
for name, p in (("server :%s" % PORT, int(PORT)),
                ("ftms-debug :8826", 8826), ("hrm-debug :8827", 8827)):
    (ok if port_open(p) else bad)(name, "listening" if port_open(p) else "not listening")

try:
    mem = {}
    for ln in open("/proc/meminfo"):
        k, _, v = ln.partition(":")
        mem[k.strip()] = int(v.strip().split()[0])  # kB
    avail_mb = mem.get("MemAvailable", 0) // 1024
    (ok if avail_mb >= 40 else bad)("headroom", "MemAvailable=%dMB (need >=40)" % avail_mb)
except Exception as e:
    warn("headroom", "could not read /proc/meminfo: %s" % e)
try:
    j = subprocess.run(["journalctl", "-b", "--no-pager", "-q"],
                        capture_output=True, text=True, timeout=20).stdout
    n = j.lower().count("oom-kill") + j.lower().count("out of memory")
    (ok if n == 0 else bad)("oom-kills", "%d since boot" % n)
except Exception:
    warn("oom-kills", "journal unreadable (non-fatal)")

# ---------------------------------------------------------------- L2 read (passive)
print("L2  FTMS read path (passive)")
try:
    st = ftms(["state"])
    if "connected: true" in st:
        spd = "?"
        for l in st.splitlines():
            i = l.find("speed:")
            if i != -1:
                spd = l[i:].strip(); break
        ok("ftms.state", "connected=true (%s)" % spd)
    else:
        bad("ftms.state", "not connected to treadmill_io: %r" % st[-120:])
except Exception as e:
    bad("ftms.state", str(e))

# ---------------------------------------------------------------- L3
print("L3  HRM daemon")
try:
    hr = ftms(["status"], port=8827)
    ok("hrm.debug", "responsive (no strap expected: graceful)")
except Exception as e:
    warn("hrm.debug", "debug port quiet: %s (non-fatal — daemon may still be scanning)" % e)

# ---------------------------------------------------------------- belt layers
if BELT:
    try:
        # L1 — C++ via REST emulate
        print("L1  C++ treadmill_io (REST emulate -> belt)")
        b0 = cpp_status()
        http("POST", "/api/speed", {"value": 1.0})
        mv, d = wait_moving()
        if mv: ok("L1.drive", "emu_speed=%s bus_speed=%s (1.0 mph)" % (d["emu_speed"], d["bus_speed"]))
        else:  bad("L1.drive", "belt did not reach 1.0 mph: %s" % json.dumps(
                   {k: d.get(k) for k in ("emulate","emu_speed","bus_speed")}))
        st, d = safe_stop()
        (ok if st else bad)("L1.stop", "bus_speed=%s emu_speed=%s" % (d.get("bus_speed"), d.get("emu_speed")))

        # L2 — FTMS Control Point (BLE-equivalent): request/set 1.0mph/start
        print("L2  FTMS Control Point (write -> belt)")
        ftms(["cp 00", "cp 02a100", "cp 07"])
        mv, d = wait_moving()
        if mv: ok("L2.drive", "emu_speed=%s bus_speed=%s (cp set 1.0 mph)" % (d["emu_speed"], d["bus_speed"]))
        else:  bad("L2.drive", "FTMS cp did not drive belt: %s" % json.dumps(
                   {k: d.get(k) for k in ("emulate","emu_speed","bus_speed")}))
        ftms(["cp 0801"])
        st, d = safe_stop()
        (ok if st else bad)("L2.stop", "bus_speed=%s emu_speed=%s" % (d.get("bus_speed"), d.get("emu_speed")))

        # L4 — program engine (the UI's path)
        print("L4  program engine (application path -> belt)")
        http("POST", "/api/program/start", {})
        time.sleep(2)
        http("POST", "/api/speed", {"value": 1.0})
        mv, d = wait_moving()
        if mv: ok("L4.drive", "emu_speed=%s bus_speed=%s (program running)" % (d["emu_speed"], d["bus_speed"]))
        else:  bad("L4.drive", "program did not drive belt: %s" % json.dumps(
                   {k: d.get(k) for k in ("emulate","emu_speed","bus_speed")}))
        http("POST", "/api/program/stop", {})
        st, d = safe_stop()
        (ok if st else bad)("L4.stop", "bus_speed=%s emu_speed=%s" % (d.get("bus_speed"), d.get("emu_speed")))
    finally:
        # ALWAYS prove a safe stop, whatever happened above.
        confirmed, d = safe_stop()
        if confirmed:
            ok("SAFE-STOP", "belt confirmed stopped (bus_speed=0, emulate off, proxy on)")
        else:
            bad("SAFE-STOP", "COULD NOT CONFIRM BELT STOPPED: %s" % json.dumps(
                {k: d.get(k) for k in ("emulate","emu_speed","bus_speed","proxy")}))
else:
    print("L1/L2-write/L4  SKIPPED (--no-belt)")

print("")
if fails:
    print("VERDICT: NOT READY  (%d failed: %s%s)" % (
        len(fails), ", ".join(fails),
        "; warns: " + ", ".join(warns) if warns else ""))
    sys.exit(1)
print("VERDICT: READY TO SHIP%s" % (
    "  (warnings: " + ", ".join(warns) + ")" if warns else ""))
sys.exit(0)
PYEOF
rc=$?
set -e

trap - EXIT INT TERM
remote_safe_stop   # final belt-safe assertion regardless of verdict

echo ""
if [ $rc -eq 0 ]; then
  echo ">>> $PI_HOST: READY TO SHIP"
else
  echo ">>> $PI_HOST: NOT READY (see failures above)"
fi
exit $rc
