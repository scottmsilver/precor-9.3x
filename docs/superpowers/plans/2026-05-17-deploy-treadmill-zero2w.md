# Deploy the Treadmill Software Family to the Pi Zero 2 W — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One unified pipeline that puts the full treadmill software family onto the Pi Zero 2 W — bake a stable appliance image and rsync-iterate, both installing identical artifacts from one shared manifest, with all compiled code cross-built off-Pi.

**Architecture:** A single aarch64 Docker cross toolchain emits `treadmill_io`, `ftms-daemon`, `hrm-daemon`, and the Vite web build. `deploy/manifest.txt` declares every artifact's source/dest/mode/owner; it is parsed as **data** (never sourced) by `deploy/lib-artifacts.sh`. Two consumers read it: the provisioning baker (`provisioning/dietpi/`) and the live deployer (`deploy/deploy.sh`). `treadmill_io` is wired into the committed `treadmill-critical.target` (Path A, network-independent). Memory pressure on 512 MB is handled by an ordered trim ladder with a 40 MB headroom gate.

**Tech Stack:** Bash (dependency-free test harnesses matching `provisioning/dietpi/tests/` style), Docker (`cross`-pattern aarch64 toolchain), Python/pytest (lazy-import change), systemd, mtools (existing audited image surgery).

**CRITICAL — no git commits.** The owner's CLAUDE.md gates all commits on a typed password. Every task ends with a **Checkpoint** step that stages changes and runs verification but **does NOT run `git commit`**. Accumulate all work for one owner-authorized commit at the very end.

---

## File Structure

| File | Responsibility |
|---|---|
| `deploy/manifest.txt` (new) | Declarative artifact table — single source of truth for what installs where |
| `deploy/lib-artifacts.sh` (new) | Parse/validate the manifest as data; shared by deployer + baker |
| `deploy/cross/Dockerfile.cpp` (new) | aarch64 g++ + libpigpio-dev + headers (mirrors `rust/hrm/Dockerfile.cross`) |
| `deploy/tests/test_manifest.sh` (new) | Dependency-free parser/validation tests |
| `deploy/tests/test_deploy_dryrun.sh` (new) | Golden `deploy.sh --dry-run` + belt-refusal logic tests |
| `deploy/tests/mem-headroom.sh` (new) | 512 MB headroom gate (Pi-run; has `--selftest`) |
| `deploy/deploy.sh` (rewrite) | Cross-build + manifest rsync + ordered atomic restart + belt-moving refusal |
| `deploy/setup.sh` (rewrite) | Manifest-driven install on the Pi; Path A wiring; trim ladder |
| `deploy/treadmill-io.service.in` (modify) | Path A drop-in wiring |
| `Makefile` (modify) | `make cross`, `make image`; retire on-Pi C++ build |
| `provisioning/dietpi/prepare-sd.sh` (modify) | Stage the full family via `manifest_stage` |
| `provisioning/dietpi/Automation_Custom_Script.sh` (modify) | First-boot family install + venv + enable 4 units |
| ~~`python/program_engine.py`~~ | **Task 3 fully reverted 2026-05-18 — file unchanged vs baseline (net-zero)** |
| ~~`python/server.py`~~ | **Task 3 fully reverted — file unchanged vs baseline; single-worker guard is test-only (test_service_units.sh)** |

---

## Task 1: Shared manifest + data-parsed library

The DRY core. Everything else consumes this. Parser mirrors `lib.sh`'s `load_secrets` fail-closed, parse-not-source posture.

**Files:**
- Create: `deploy/manifest.txt`
- Create: `deploy/lib-artifacts.sh`
- Test: `deploy/tests/test_manifest.sh`

- [ ] **Step 1: Write the failing test**

Create `deploy/tests/test_manifest.sh`:

```bash
#!/usr/bin/env bash
# Dependency-free unit tests for lib-artifacts.sh. Exit non-zero on first failure.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=/dev/null
source "$HERE/../lib-artifacts.sh"

fail() { echo "FAIL: $1" >&2; exit 1; }
pass() { echo "ok: $1"; }

# --- valid manifest: comments + blanks skipped, rows normalized ---
TD=$(mktemp -d)
cat > "$TD/m.txt" <<'EOF'
# comment line
bin   build/treadmill_io          /usr/local/bin/treadmill_io   0755 root

tree  python/                     ~/treadmill/python/           0644 @USER@
unit  build/treadmill-io.service  /etc/systemd/system/          0644 root
EOF
rows=$(manifest_rows "$TD/m.txt") || fail "valid manifest must parse"
[ "$(printf '%s\n' "$rows" | wc -l)" -eq 3 ] || fail "expected 3 rows, got: [$rows]"
printf '%s\n' "$rows" | grep -q '^bin	build/treadmill_io	/usr/local/bin/treadmill_io	0755	root$' \
  || fail "row not tab-normalized: [$rows]"
pass "valid manifest parses, comments/blanks skipped, tab-normalized"

# --- fail closed: unknown kind ---
printf 'wat src dst 0644 root\n' > "$TD/bad.txt"
manifest_rows "$TD/bad.txt" 2>/dev/null && fail "unknown kind must fail closed"
pass "unknown kind rejected"

# --- fail closed: wrong field count ---
printf 'bin src dst 0644\n' > "$TD/bad.txt"
manifest_rows "$TD/bad.txt" 2>/dev/null && fail "4-field row must fail closed"
pass "wrong field count rejected"

# --- fail closed: path traversal in src ---
printf 'file ../etc/passwd /x 0644 root\n' > "$TD/bad.txt"
manifest_rows "$TD/bad.txt" 2>/dev/null && fail "'..' in src must fail closed"
pass "src path traversal rejected"

# --- fail closed: absolute src ---
printf 'file /etc/passwd /x 0644 root\n' > "$TD/bad.txt"
manifest_rows "$TD/bad.txt" 2>/dev/null && fail "absolute src must fail closed"
pass "absolute src rejected"

# --- fail closed: dest outside allowed roots ---
printf 'file gpio.json /etc/passwd 0644 root\n' > "$TD/bad.txt"
manifest_rows "$TD/bad.txt" 2>/dev/null && fail "dest outside allowed roots must fail closed"
pass "dest outside allowed roots rejected"

# --- the real shipped manifest must itself be valid ---
manifest_rows "$HERE/../manifest.txt" >/dev/null || fail "shipped deploy/manifest.txt is invalid"
pass "shipped manifest valid"

rm -rf "$TD"
echo "ALL TESTS PASSED"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `bash deploy/tests/test_manifest.sh`
Expected: FAIL — `lib-artifacts.sh` does not exist (source error).

- [ ] **Step 3: Create the manifest**

Create `deploy/manifest.txt`:

```
# Treadmill software family install manifest — single source of truth.
# Consumed as DATA by deploy/lib-artifacts.sh (never sourced).
# Columns:  kind  src  dest  mode  owner
#   kind  : bin | tree | file | unit
#   src   : path relative to repo root (no leading '/', no '..')
#   dest  : install path; must be under /usr/local/bin, /etc/systemd/system,
#           or a ~ / /home/ user root.  @USER@ resolved per host.
#   mode  : octal file mode
#   owner : root | @USER@
bin   build/treadmill_io              /usr/local/bin/treadmill_io          0755 root
bin   build/ftms-daemon               /usr/local/bin/ftms-daemon           0755 root
bin   build/hrm-daemon                /usr/local/bin/hrm-daemon            0755 root
tree  build/python/                   ~/treadmill/python/                  0644 @USER@
file  build/gpio.json                 ~/treadmill/gpio.json                0644 @USER@
file  build/pyproject.toml            ~/treadmill/pyproject.toml           0644 @USER@
tree  build/static/                   ~/treadmill/static/                  0644 @USER@
unit  build/services/treadmill-io.service      /etc/systemd/system/        0644 root
unit  build/services/treadmill-server.service  /etc/systemd/system/        0644 root
unit  build/services/ftms.service              /etc/systemd/system/        0644 root
unit  build/services/hrm.service               /etc/systemd/system/        0644 root
```

- [ ] **Step 4: Create the library**

Create `deploy/lib-artifacts.sh`:

```bash
#!/usr/bin/env bash
# Pure, sourceable helpers for parsing deploy/manifest.txt as DATA.
# No side effects at source time. Mirrors lib.sh load_secrets: parse,
# never execute; fail closed on anything unexpected.

# Emit validated, tab-normalized rows: "kind\tsrc\tdest\tmode\towner".
# Skips blank lines and lines whose first non-whitespace char is '#'.
# Returns non-zero (and prints to stderr) on the first invalid row.
manifest_rows() {
  local file=$1 line trimmed kind src dest mode owner rc=0
  [ -f "$file" ] || { echo "manifest not found: $file" >&2; return 1; }
  while IFS= read -r line || [ -n "$line" ]; do
    line=${line%$'\r'}
    trimmed=${line#"${line%%[![:space:]]*}"}
    [ -z "$trimmed" ] && continue
    case $trimmed in '#'*) continue ;; esac
    # Exactly five whitespace-separated fields.
    # shellcheck disable=SC2086
    set -- $trimmed
    if [ "$#" -ne 5 ]; then
      echo "manifest: row must have 5 fields: $line" >&2; return 1
    fi
    kind=$1 src=$2 dest=$3 mode=$4 owner=$5
    case $kind in bin|tree|file|unit) ;; *)
      echo "manifest: unknown kind '$kind': $line" >&2; return 1 ;; esac
    case $src in
      /*) echo "manifest: src must be repo-relative (no leading /): $line" >&2; return 1 ;;
    esac
    case "/$src/" in
      */../*) echo "manifest: src contains '..': $line" >&2; return 1 ;;
    esac
    case $dest in
      /usr/local/bin/*|/etc/systemd/system/*|'~/'*|/home/*) ;;
      *) echo "manifest: dest outside allowed roots: $line" >&2; return 1 ;;
    esac
    case $mode in [0-7][0-7][0-7][0-7]) ;; *)
      echo "manifest: mode must be 4 octal digits: $line" >&2; return 1 ;; esac
    case $owner in root|@USER@) ;; *)
      echo "manifest: owner must be root or @USER@: $line" >&2; return 1 ;; esac
    printf '%s\t%s\t%s\t%s\t%s\n' "$kind" "$src" "$dest" "$mode" "$owner"
  done < "$file"
  return $rc
}

# Resolve @USER@ / leading ~ in a dest path for a concrete user.
manifest_resolve_dest() {
  local dest=$1 user=$2
  dest=${dest//@USER@/$user}
  case $dest in '~/'*) dest="/home/$user/${dest#\~/}" ;; esac
  printf '%s\n' "$dest"
}
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `bash deploy/tests/test_manifest.sh`
Expected: ends with `ALL TESTS PASSED`.

- [ ] **Step 6: Checkpoint (NO COMMIT — password gate)**

Run: `git add deploy/manifest.txt deploy/lib-artifacts.sh deploy/tests/test_manifest.sh && git status --short`
Do **NOT** run `git commit`. Leave staged for the final owner-authorized commit.

---

## Task 2: aarch64 C++ cross toolchain

One containerized toolchain for the C++ binary, matching the existing `cross` Docker pattern used for Rust. `cpp/Makefile` already parameterizes `CXX`, so cross = run `make -C cpp CXX=aarch64-linux-gnu-g++` inside a container that has aarch64 `libpigpio-dev`.

**Files:**
- Create: `deploy/cross/Dockerfile.cpp`
- Modify: `Makefile:1-25`
- Test: `deploy/tests/test_cross.sh`

- [ ] **Step 1: Write the failing test**

Create `deploy/tests/test_cross.sh`:

```bash
#!/usr/bin/env bash
# Verifies the unified cross toolchain produces a reproducible aarch64 ELF.
# Skips (exit 0 with notice) when Docker is unavailable so unit suites stay green.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
fail() { echo "FAIL: $1" >&2; exit 1; }
pass() { echo "ok: $1"; }

command -v docker >/dev/null 2>&1 || { echo "SKIP: docker unavailable"; exit 0; }

( cd "$ROOT" && make cross-cpp ) || fail "make cross-cpp failed"
[ -f "$ROOT/build/treadmill_io" ] || fail "build/treadmill_io not produced"
file "$ROOT/build/treadmill_io" | grep -q 'ARM aarch64' \
  || fail "treadmill_io is not an aarch64 ELF: $(file "$ROOT/build/treadmill_io")"
pass "cross build produced an aarch64 treadmill_io"

h1=$(sha256sum "$ROOT/build/treadmill_io" | awk '{print $1}')
( cd "$ROOT" && make cross-cpp ) || fail "second cross build failed"
h2=$(sha256sum "$ROOT/build/treadmill_io" | awk '{print $1}')
[ "$h1" = "$h2" ] || fail "cross build not reproducible: $h1 != $h2"
pass "cross build is reproducible (identical sha256)"

echo "ALL TESTS PASSED"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `bash deploy/tests/test_cross.sh`
Expected: FAIL — `make cross-cpp` target does not exist (or SKIP if no Docker; on the dev workstation Docker is present per the existing `cross` Rust flow).

- [ ] **Step 3: Create the Dockerfile**

Create `deploy/cross/Dockerfile.cpp`.

**Plan correction (applied during implementation):** the original design assumed `libpigpio-dev:arm64` was installable via Debian multiarch. It is not — `libpigpio-dev` ships only in the Raspberry Pi OS archive, which cannot satisfy `libc6:arm64` inside a Debian multiarch container. The correct, more-reproducible approach is to build pigpio from source, pinned to the Pi's exact installed version (`1.79`, git tag `v79`), and install it into the cross sysroot the Debian `g++-aarch64-linux-gnu` already searches (`/usr/aarch64-linux-gnu/{include,lib}`). No external RPi apt source; one RUN layer; reproducible:

```dockerfile
# aarch64 C++ cross toolchain for treadmill_io. Mirrors the cross/Rust
# pattern: a pinned base, the aarch64 g++ cross compiler, and libpigpio
# built from source for aarch64 (libpigpio-dev is not in Debian main;
# it lives in the Raspberry Pi OS repo which cannot satisfy libc6:arm64
# in a Debian multiarch container). Building from source is the
# correct approach: one RUN layer, pure Debian deps, fully reproducible.
FROM debian:bookworm-slim

# PIGPIO_VERSION matches the Pi's installed version (1.79, git tag v79).
ARG PIGPIO_VERSION=79

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        g++-aarch64-linux-gnu \
        binutils-aarch64-linux-gnu \
        make \
        curl \
        ca-certificates && \
    # Build libpigpio for aarch64 and install into the cross sysroot.
    curl -fsSL "https://github.com/joan2937/pigpio/archive/refs/tags/v${PIGPIO_VERSION}.tar.gz" \
        | tar -xz && \
    cd "pigpio-${PIGPIO_VERSION}" && \
    make CC=aarch64-linux-gnu-gcc \
         STRIP=aarch64-linux-gnu-strip \
         SIZE=true && \
    install -m 0644 pigpio.h /usr/aarch64-linux-gnu/include/ && \
    install -m 0755 libpigpio.so.1 /usr/aarch64-linux-gnu/lib/ && \
    ln -fs libpigpio.so.1 /usr/aarch64-linux-gnu/lib/libpigpio.so && \
    cd / && rm -rf "/pigpio-${PIGPIO_VERSION}" && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /src
# The build is driven by the repo's cpp/Makefile with CXX overridden.
CMD ["make", "-C", "cpp", "CXX=aarch64-linux-gnu-g++"]
```

- [ ] **Step 4: Add Makefile targets**

In `Makefile`, replace lines 1-25 (the `PI_HOST` header through the `stage` target) with:

```makefile
PI_HOST ?= rpi-zero
VENV_DIR ?= .venv
FTMS_TARGET = aarch64-unknown-linux-gnu
FTMS_BIN = rust/ftms/target/$(FTMS_TARGET)/release/ftms-daemon
HRM_TARGET = aarch64-unknown-linux-gnu
HRM_BIN = rust/hrm/target/$(HRM_TARGET)/release/hrm-daemon
CPP_CROSS_IMG = treadmill-cross-cpp

.PHONY: all clean test stage deploy image cross cross-cpp ftms deploy-ftms \
        test-ftms test-ftms-ble hrm deploy-hrm test-hrm test-pi test-all

all:
	$(MAKE) -C cpp

test:
	$(MAKE) -C cpp test

clean:
	$(MAKE) -C cpp clean
	rm -rf build/

# Build the aarch64 treadmill_io inside the pinned cross container.
cross-cpp:
	docker build -t $(CPP_CROSS_IMG) -f deploy/cross/Dockerfile.cpp deploy/cross
	mkdir -p build
	docker run --rm -v "$(CURDIR)":/src -w /src $(CPP_CROSS_IMG) \
		make -C cpp CXX=aarch64-linux-gnu-g++
	test -f build/treadmill_io   # cpp/Makefile writes here (project build-dir convention)

# Build all three aarch64 binaries (C++ + both Rust daemons) off-Pi.
cross: cross-cpp ftms hrm
	mkdir -p build
	cp $(FTMS_BIN) build/ftms-daemon
	cp $(HRM_BIN) build/hrm-daemon

stage: cross
	deploy/deploy.sh --stage-only

# `make deploy` must still work (CLAUDE.md + Task 9 docs rely on it). It now
# depends on `cross` so the manifest's binaries exist before deploy.sh rsyncs.
deploy: cross
	deploy/deploy.sh
```

Note: `make -C cpp` writes the binary to repo-root `build/treadmill_io` (the project's build-artifact convention; `cpp/` stays clean). The container mounts the repo at `/src`, so the `build/` it writes is the host's. The `test -f build/treadmill_io` line is the hard assertion the cross build actually produced the binary.

- [ ] **Step 5: Run the test to verify it passes**

Run: `bash deploy/tests/test_cross.sh`
Expected: `ALL TESTS PASSED` (build produces an `ARM aarch64` ELF, reproducible sha256).

- [ ] **Step 6: Checkpoint (NO COMMIT — password gate)**

Run: `git add deploy/cross/Dockerfile.cpp deploy/tests/test_cross.sh Makefile && git status --short`
Do **NOT** run `git commit`.

---

## Task 3: Lazy `google-genai` import (trim ladder step 3)

> **⚠️ FULLY REVERTED 2026-05-18 — NET-ZERO (historical record below).**
> Implemented as specified, then completely backed out after the live
> `rpi-zero` deploy measured ~354 MB free on the 463 MB Pi (family
> ≈109 MB): the memory pressure this task assumed does not exist, so the
> lazy-import complexity bought nothing. `python/program_engine.py`,
> `python/server.py`, and `python/tests/test_server_integration.py` were
> restored **byte-identical to the pre-plan baseline** via
> `git checkout HEAD -- …` (verified: `git diff --cached` for all three
> is empty); `python/tests/test_lazy_genai.py` removed. **Task 3
> contributes nothing to the final plan diff** (staged file count
> 24 → 20). Regression re-run against the pristine originals: 411 passed,
> only the 2 documented pre-existing `asyncio.get_event_loop()`
> ordering-pollution failures (now proven pre-existing beyond doubt —
> they fail against the untouched original `test_server_integration.py`).
> The eager top-level `from google import genai` / `from google.genai
> import types` is simply the original code. Task text kept as the record
> of what was done and undone.

`from google import genai` currently loads at module import in both `program_engine.py` and `server.py`, pulling in pydantic/google-auth/etc. on a 512 MB box even when the AI coach is never used. Defer it.

**Files:**
- Modify: `python/program_engine.py:1-17,42-67`
- Modify: `python/server.py:24-35,925-935`
- Test: `python/tests/test_lazy_genai.py`

- [ ] **Step 1: Write the failing test**

Create `python/tests/test_lazy_genai.py`:

```python
"""google-genai must not load at server import (512MB trim ladder step 3)."""
import subprocess
import sys


def test_importing_server_does_not_load_genai():
    code = (
        "import sys; "
        "sys.path.insert(0, 'python'); "
        "import server; "
        "assert 'google.genai' not in sys.modules, "
        "'google.genai loaded at server import (should be lazy)'; "
        "print('OK')"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert "OK" in r.stdout


def test_get_client_loads_genai_on_demand():
    code = (
        "import sys; "
        "sys.path.insert(0, 'python'); "
        "import program_engine; "
        "assert 'google.genai' not in sys.modules; "
        "import google.genai as _g; "  # explicit import proves it is importable
        "assert 'google.genai' in sys.modules; "
        "print('OK')"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert "OK" in r.stdout
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m pytest python/tests/test_lazy_genai.py -v`
Expected: `test_importing_server_does_not_load_genai` FAILS — `google.genai` is in `sys.modules` because of the top-level `from google import genai`.

- [ ] **Step 3: Make `program_engine.py` imports lazy**

In `python/program_engine.py`, change the top of the file (lines 1-19). Replace:

```python
import asyncio
import json
import logging
import os
import re
import time

from google import genai
from google.genai import types

log = logging.getLogger("program")
```

with:

```python
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # type-only; never imported at runtime module load
    from google import genai
    from google.genai import types

log = logging.getLogger("program")
```

Then change `build_tts_config` (lines 42-53) so it imports `types` at call time. Replace:

```python
def build_tts_config(voice: str = "Kore") -> types.GenerateContentConfig:
    """Build a GenerateContentConfig for Gemini TTS. Shared by server and tests."""
    return types.GenerateContentConfig(
```

with:

```python
def build_tts_config(voice: str = "Kore") -> "types.GenerateContentConfig":
    """Build a GenerateContentConfig for Gemini TTS. Shared by server and tests."""
    from google.genai import types
    return types.GenerateContentConfig(
```

Then change `get_client` (lines 56-67) so it imports `genai` at call time. Replace:

```python
_client: genai.Client | None = None


def get_client() -> genai.Client:
    """Lazy singleton for the Gemini SDK client."""
    global _client
    if _client is None:
        api_key = read_api_key()
        if not api_key:
            raise ValueError("No Gemini API key. Set GEMINI_API_KEY or create .gemini_key file.")
        _client = genai.Client(api_key=api_key)
    return _client
```

with:

```python
_client: "genai.Client | None" = None


def get_client() -> "genai.Client":
    """Lazy singleton for the Gemini SDK client.

    Imports google-genai on first use only — keeps the SDK (pydantic,
    google-auth, ...) out of RAM on the 512MB Pi Zero 2 W unless the AI
    coach is actually exercised. See the 512MB trim ladder.
    """
    global _client
    if _client is None:
        from google import genai
        api_key = read_api_key()
        if not api_key:
            raise ValueError("No Gemini API key. Set GEMINI_API_KEY or create .gemini_key file.")
        _client = genai.Client(api_key=api_key)
    return _client
```

- [ ] **Step 4: Make `server.py` imports lazy**

In `python/server.py`, remove the top-level genai import. Delete line 30 (`from google import genai`). The only other direct `genai.` use is the auth client around line 930; make it import locally. Replace (around lines 928-932):

```python
        auth_client = genai.Client(
```

with:

```python
        from google import genai  # lazy: keep SDK out of RAM until AI used
        auth_client = genai.Client(
```

Search for any other `genai.` references in `server.py` and apply the same local-import treatment in the enclosing function:

Run: `grep -n '\bgenai\.' python/server.py`
For each hit not already covered, add `from google import genai` at the top of that function body.

- [ ] **Step 5: Run the test to verify it passes**

Run: `python3 -m pytest python/tests/test_lazy_genai.py -v`
Expected: both tests PASS.

- [ ] **Step 6: Verify no regression in existing Python suites**

Run: `python3 -m pytest python/tests -m "not hardware" -q`
Expected: same pass count as before this task (no new failures). If a test imported `genai` transitively via `server`/`program_engine`, fix it to import `google.genai` directly.

- [ ] **Step 7: Checkpoint (NO COMMIT — password gate)**

Run: `git add python/program_engine.py python/server.py python/tests/test_lazy_genai.py && git status --short`
Do **NOT** run `git commit`.

---

## Task 4: Rewrite `deploy.sh` — cross-build, manifest rsync, deploy safety

Drop on-Pi C++ build. Default `PI_HOST=rpi-zero` (`rpi` still valid). Add `--dry-run`. Refuse to deploy while the belt moves. Ordered atomic `treadmill_io`-last restart. Never partial.

**Files:**
- Rewrite: `deploy/deploy.sh`
- Test: `deploy/tests/test_deploy_dryrun.sh`

- [ ] **Step 1: Write the failing test**

Create `deploy/tests/test_deploy_dryrun.sh`:

```bash
#!/usr/bin/env bash
# Dependency-free tests for deploy.sh planning logic. No Pi/ssh required:
# --dry-run prints the deploy plan from the manifest and performs only a
# read-only /api/status probe (no host mutation, no ssh/rsync).
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
fail() { echo "FAIL: $1" >&2; exit 1; }
pass() { echo "ok: $1"; }

out=$(cd "$ROOT" && PI_HOST=examplehost bash deploy/deploy.sh --dry-run 2>&1) \
  || fail "--dry-run must exit 0 (got: $out)"

echo "$out" | grep -q 'examplehost' || fail "--dry-run must show target host"
echo "$out" | grep -q '/usr/local/bin/treadmill_io' \
  || fail "--dry-run must list manifest binary dest"
echo "$out" | grep -q 'treadmill_io .*last' \
  || fail "--dry-run must state treadmill_io restarts last"
# Must NOT attempt ssh/rsync in dry-run. Match BOTH an explicit ssh/rsync
# invocation AND the resolver/connection errors a stray ssh/rsync would emit
# (a real `ssh examplehost` prints "Could not resolve hostname", which the
# literal 'ssh examplehost' pattern alone would miss).
echo "$out" | grep -qiE 'rsync -|ssh examplehost|could not resolve|connection refused|name or service not known' \
  && fail "--dry-run must not execute ssh/rsync (no network mutation)"
pass "--dry-run prints plan, only the read-only status probe"

# Default host is the Zero 2 W (Pi 4 is the spare).
out=$(cd "$ROOT" && bash deploy/deploy.sh --dry-run 2>&1) || fail "default --dry-run failed"
echo "$out" | grep -q 'rpi-zero' || fail "default PI_HOST must be rpi-zero"
pass "default target is rpi-zero"

# Belt-moving refusal: feed a fake status with non-zero speed via the hook.
out=$(cd "$ROOT" && DEPLOY_STATUS_OVERRIDE='{"speed":2.5}' \
      PI_HOST=examplehost bash deploy/deploy.sh --dry-run 2>&1)
echo "$out" | grep -qi 'belt is moving' \
  || fail "non-zero speed must surface a belt-moving abort in the plan"
pass "belt-moving refusal detected from status"

# SAFETY REGRESSION: in emulate mode the server emits "speed": null while the
# belt moves under emu_speed_mph. Probing only "speed" would false-negative a
# moving belt and let the deploy bounce treadmill_io mid-workout.
out=$(cd "$ROOT" && DEPLOY_STATUS_OVERRIDE='{"type":"status","emulate":true,"emu_speed":30,"emu_speed_mph":3.0,"speed":null}' \
      PI_HOST=examplehost bash deploy/deploy.sh --dry-run 2>&1)
echo "$out" | grep -qi 'belt is moving' \
  || fail "emulate-mode moving belt (speed:null, emu_speed_mph>0) must still abort"
pass "emulate-mode moving belt detected (speed:null + emu_speed_mph)"
# And a genuinely stopped belt (both zero/null) must NOT abort.
out=$(cd "$ROOT" && DEPLOY_STATUS_OVERRIDE='{"type":"status","emulate":false,"emu_speed_mph":0.0,"speed":0}' \
      PI_HOST=examplehost bash deploy/deploy.sh --dry-run 2>&1)
echo "$out" | grep -qi 'belt is moving' \
  && fail "stopped belt (speed:0, emu_speed_mph:0.0) must NOT abort"
pass "stopped belt does not false-abort"

# INTEGRATION SEAM (Task 2 cross-build must not be discarded): the rewritten
# deploy.sh must NOT 'rm -rf build' in stage() and must NOT build C++ on the
# Pi (no 'make -C cpp' over ssh). The cross-built build/treadmill_io must
# survive staging and reach the Pi via the manifest. Static guard:
grep -qE 'rm[[:space:]]+-rf[[:space:]]+build([[:space:]/]|$)' "$ROOT/deploy/deploy.sh" \
  && fail "deploy.sh stage() must NOT rm -rf build (would delete cross-built binaries)"
grep -qE 'make[[:space:]]+-C[[:space:]]+cpp' "$ROOT/deploy/deploy.sh" \
  && fail "deploy.sh must NOT build C++ on the Pi (cross-build retired build-on-Pi)"
grep -q 'manifest' "$ROOT/deploy/deploy.sh" \
  || fail "deploy.sh must drive install from the shared manifest"
pass "cross-build integration seam intact (no rm -rf build, no on-Pi C++ build, manifest-driven)"

# PI_HOST default must agree with the Makefile (both rpi-zero) so `make deploy`
# and a bare `deploy.sh` target the same host.
grep -qE 'PI_HOST.*:-rpi-zero' "$ROOT/deploy/deploy.sh" \
  || fail "deploy.sh PI_HOST default must be rpi-zero (matches Makefile)"
pass "deploy.sh PI_HOST default agrees with Makefile (rpi-zero)"

echo "ALL TESTS PASSED"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `bash deploy/tests/test_deploy_dryrun.sh`
Expected: FAIL — current `deploy.sh` has no `--dry-run`, default host is `rpi`, no belt check.

- [ ] **Step 3: Rewrite `deploy/deploy.sh`**

Replace the entire contents of `deploy/deploy.sh` with:

```bash
#!/usr/bin/env bash
set -euo pipefail

# cd to project root (parent of deploy/)
cd "$(dirname "$0")/.."
SCRIPT_DIR="$(pwd)"
LOCK_SCRIPT="$SCRIPT_DIR/scripts/pi-lock.sh"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/deploy/lib-artifacts.sh"

PI_HOST="${PI_HOST:-rpi-zero}"     # Zero 2 W is primary; Pi 4 (rpi) is the spare
PI_DIR="${PI_DIR:-treadmill}"
VENV_DIR="${VENV_DIR:-.venv}"
MANIFEST="$SCRIPT_DIR/deploy/manifest.txt"
SERVER_PORT="${SERVER_PORT:-8000}"

DRY=0
case "${1:-}" in
  --dry-run) DRY=1 ;;
  --stage-only) STAGE_ONLY=1 ;;
  ui) DEPLOY_UI=1 ;;
esac

render_service() {
  # PI_USER only resolved for real runs (needs ssh); dry-run uses a token.
  sed -e "s|@USER@|${PI_USER:-@USER@}|g" \
      -e "s|@DEPLOY_DIR@|$PI_DIR|g" \
      -e "s|@VENV_DIR@|$VENV_DIR|g" "$1"
}

stage() {
  echo "=== Staging build/ (from manifest) ==="
  mkdir -p build/services build/static build/python
  cp python/server.py python/workout_session.py python/program_engine.py \
     python/treadmill_client.py python/hrm_client.py python/workout_db.py \
     python/db.py build/python/
  cp gpio.json pyproject.toml build/
  cp deploy/setup.sh deploy/lib-artifacts.sh deploy/manifest.txt build/
  chmod +x build/setup.sh
  echo "Building UI..."
  rm -rf static/assets && mkdir -p static/assets
  (cd web && npx vite build)
  cp -r static/index.html static/assets build/static/
  for tmpl in deploy/*.service.in; do
    name=$(basename "$tmpl" .in)
    render_service "$tmpl" > "build/services/$name"
  done
  echo "Staged to build/ (binaries come from \`make cross\`)"
}

# Best-effort belt-safety: read-only probe of the live server's /api/status
# (no host mutation). A moving belt aborts unless FORCE=1. Unreachable server
# => warn + proceed (a down server cannot be mid-web-workout; treadmill_io is
# still restarted last+atomically so its safety gap is minimal).
# DEPLOY_STATUS_OVERRIDE lets the test inject a status without a host.
#
# CRITICAL: must check BOTH "speed" AND "emu_speed_mph". In emulate mode the
# server emits "speed": null (no motor reading) while the belt moves under
# emu_speed_mph (server.py build_status). Probing only "speed" would
# false-negative a moving emulate workout and let the deploy bounce
# treadmill_io mid-run. The quote-delimited "speed" token does not collide
# with "emu_speed"/"emu_speed_mph"; the digit-led capture ignores null.
belt_is_moving() {
  local json s key
  if [ -n "${DEPLOY_STATUS_OVERRIDE:-}" ]; then
    json="$DEPLOY_STATUS_OVERRIDE"
  else
    json=$(curl -sk --max-time 5 "https://$PI_HOST:$SERVER_PORT/api/status" 2>/dev/null || true)
  fi
  [ -n "$json" ] || { echo "WARN: could not read /api/status (server down?) — proceeding" >&2; return 1; }
  for key in '"speed"' '"emu_speed_mph"'; do
    s=$(printf '%s' "$json" | sed -n "s/.*$key[[:space:]]*:[[:space:]]*\\([0-9][0-9.]*\\).*/\\1/p")
    [ -n "$s" ] && awk -v v="$s" 'BEGIN{exit (v+0>0)?0:1}' && return 0
  done
  return 1
}

print_plan() {
  echo "=== Deploy plan -> $PI_HOST:~/$PI_DIR (host: $PI_HOST) ==="
  manifest_rows "$MANIFEST" | while IFS=$'\t' read -r kind src dest mode owner; do
    echo "  install $kind  $src  ->  $dest  ($mode $owner)"
  done
  echo "  restart order: treadmill-server, ftms, hrm  THEN  treadmill_io (last, atomic)"
  if belt_is_moving; then
    echo "  ABORT: belt is moving (speed>0) — refusing deploy (use --force to override)"
  fi
}

deploy_full() {
  manifest_rows "$MANIFEST" >/dev/null    # fail closed before any host contact
  stage
  PI_USER="${PI_USER:-$(ssh "$PI_HOST" whoami)}"
  # Re-render now that PI_USER is resolved (stage() rendered with the @USER@
  # token for --stage-only; here we substitute the real deploy user).
  for tmpl in deploy/*.service.in; do
    name=$(basename "$tmpl" .in); render_service "$tmpl" > "build/services/$name"
  done
  if belt_is_moving && [ "${FORCE:-0}" != 1 ]; then
    echo "REFUSING: belt is moving on $PI_HOST. Stop the belt or set FORCE=1." >&2
    exit 1
  fi
  if [ -x "$LOCK_SCRIPT" ]; then
    source "$LOCK_SCRIPT" acquire "deploy from $(basename "$SCRIPT_DIR")"
  fi
  echo "=== Deploying to $PI_HOST:~/$PI_DIR ==="
  ssh "$PI_HOST" "mkdir -p ~/$PI_DIR"
  # Never partial: rsync fully completes before any systemctl.
  rsync -az --delete \
    --exclude='*.o' --exclude='*.d' --exclude='*.test.o' \
    --exclude='.gemini_key' --exclude='*.pem' \
    --exclude='program_history.json' --exclude='saved_workouts.json' \
    --exclude='__pycache__' \
    build/ "$PI_HOST":~/"$PI_DIR"/
  echo "Running setup (manifest install + ordered atomic restart)..."
  ssh "$PI_HOST" "cd ~/$PI_DIR && bash setup.sh"
  echo "Done!  UI: https://$PI_HOST:$SERVER_PORT"
}

deploy_ui() {
  echo "=== Deploying UI to $PI_HOST ==="
  rm -rf static/assets && mkdir -p static/assets build/static
  (cd web && npx vite build)
  cp -r static/index.html static/assets build/static/
  ssh "$PI_HOST" "rm -rf ~/$PI_DIR/static/assets && mkdir -p ~/$PI_DIR/static/assets"
  rsync -az build/static/ "$PI_HOST":~/"$PI_DIR"/static/
  echo "Done! UI deployed."
}

case "${1:-}" in
  --dry-run)    print_plan ;;
  --stage-only) stage ;;
  ui)           deploy_ui ;;
  *)            deploy_full ;;
esac
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `bash deploy/tests/test_deploy_dryrun.sh`
Expected: `ALL TESTS PASSED`.

- [ ] **Step 5: Checkpoint (NO COMMIT — password gate)**

Run: `git add deploy/deploy.sh deploy/tests/test_deploy_dryrun.sh && git status --short`
Do **NOT** run `git commit`.

---

## Task 5: Rewrite `setup.sh` — manifest-driven install, Path A wiring, trim ladder

`setup.sh` runs on the Pi after rsync. It must install strictly from the manifest, wire `treadmill_io` into `treadmill-critical.target` (Path A), enable a zram thin margin, keep the venv minimal, and restart with `treadmill_io` last + atomic.

**Files:**
- Rewrite: `deploy/setup.sh`
- Test: `deploy/tests/test_setup_logic.sh`

- [ ] **Step 1: Write the failing test**

Create `deploy/tests/test_setup_logic.sh`:

```bash
#!/usr/bin/env bash
# Dependency-free checks of setup.sh's static guarantees (no Pi needed):
# it must be manifest-driven, wire Path A, set a zram margin, and restart
# treadmill-io LAST.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
S="$HERE/../setup.sh"
fail() { echo "FAIL: $1" >&2; exit 1; }
pass() { echo "ok: $1"; }

bash -n "$S" || fail "setup.sh has a syntax error"
pass "setup.sh parses"

grep -q 'lib-artifacts.sh' "$S" || fail "setup.sh must source lib-artifacts.sh"
grep -q 'manifest_rows' "$S"    || fail "setup.sh must install from the manifest"
pass "setup.sh is manifest-driven"

# Payload is build/ flattened into setup.sh's dir: src must have the
# staging-root 'build/' prefix stripped, missing src must hard-fail, and
# rows already in place (app tree) must be identity-skipped (not self-copied).
grep -qE '\$\{src#build/\}' "$S" \
  || fail "setup.sh must strip the 'build/' staging-root prefix from manifest src"
grep -qE 'manifest src missing in payload' "$S" \
  || fail "setup.sh must hard-fail when a manifest src is absent from the payload"
grep -qE 'realpath .*-- "\$srcfile"' "$S" && grep -qE 'already in place' "$S" \
  || fail "setup.sh must identity-skip rows already placed by the payload flatten"
pass "setup.sh resolves payload-relative src, fails closed on missing, skips identity"

# OS runtime prereqs: bare DietPi lacks python3 + libpigpio1 (treadmill_io's
# runtime .so); setup.sh must install them (idempotently) before venv/restart.
grep -q 'OS runtime prerequisites' "$S" \
  || fail "setup.sh must install OS runtime prerequisites (python3/libpigpio1)"
grep -q 'libpigpio1' "$S" \
  || fail "setup.sh must ensure libpigpio1 (treadmill_io links libpigpio.so.1)"
grep -qE 'command -v python3 .*\|\| need=' "$S" \
  || fail "setup.sh must install python3 when absent (server venv)"
pass "setup.sh installs OS prereqs (python3 + libpigpio1) idempotently"

grep -q 'add-wants treadmill-critical.target' "$S" \
  || fail "setup.sh must wire treadmill_io into Path A (treadmill-critical.target)"
pass "Path A wiring present"

grep -qE 'zram' "$S" || fail "setup.sh must enable a zram thin margin (trim ladder step 4)"
pass "zram thin margin present"

# treadmill-io restarted LAST: its restart line must come after the others.
awk '
  /systemctl restart treadmill-server|systemctl restart ftms|systemctl restart hrm/ {others=NR}
  /systemctl restart treadmill-io( |$)/ {io=NR}
  END { exit (io>others && others>0)?0:1 }
' "$S" || fail "treadmill-io must be restarted AFTER server/ftms/hrm"
pass "treadmill-io restarts last (atomic, minimal safety-daemon downtime)"

echo "ALL TESTS PASSED"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `bash deploy/tests/test_setup_logic.sh`
Expected: FAIL — current `setup.sh` is not manifest-driven, has no Path A wiring, no zram, and restarts `treadmill-io` first.

- [ ] **Step 3: Rewrite `deploy/setup.sh`**

Replace the entire contents of `deploy/setup.sh` with:

```bash
#!/usr/bin/env bash
# setup.sh — runs on the Pi after deploy rsync. Installs strictly from
# deploy/manifest.txt, wires treadmill_io into Path A, applies the 512MB
# trim ladder (zram thin margin), and restarts with treadmill_io LAST.
set -euo pipefail
cd "$(dirname "$0")"
# shellcheck source=/dev/null
source ./lib-artifacts.sh

USER_NAME="$(whoami)"

# Clean up the legacy underscore-named unit.
sudo systemctl disable --now treadmill_io 2>/dev/null || true
sudo rm -f /etc/systemd/system/treadmill_io.service

# --- OS runtime prerequisites -----------------------------------------------
# A minimal/provisioned DietPi lacks these; the production Raspberry Pi OS box
# has them pre-installed (the plan originally assumed them present — a real
# rpi-zero deploy 2026-05-17 surfaced the gap). Install only what's missing
# (idempotent): python3 + venv/pip for treadmill-server, and libpigpio1 — the
# runtime shared library treadmill_io dynamically links (libpigpio.so.1). The
# Pi's DietPi apt includes archive.raspberrypi.com, so libpigpio1 resolves to
# the same 1.79-1+rpt1 production runs. Must precede the venv step and the
# treadmill_io restart.
need=""
command -v python3 >/dev/null 2>&1 || need="$need python3 python3-venv python3-pip"
if ! { [ -e /usr/lib/libpigpio.so.1 ] || [ -e /lib/libpigpio.so.1 ] \
       || [ -e /usr/lib/aarch64-linux-gnu/libpigpio.so.1 ]; }; then
  need="$need libpigpio1"
fi
command -v rsync >/dev/null 2>&1 || need="$need rsync"
if [ -n "$need" ]; then
  echo "Installing OS prerequisites:$need"
  sudo apt-get update -qq
  # shellcheck disable=SC2086
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends $need
fi

# --- Manifest-driven install ------------------------------------------------
# Deploy payload = the staged build/ dir flattened into THIS directory
# (deploy.sh rsyncs 'build/' -> ~/treadmill/; the bake ACS does
# 'cp -r build/.' -> ~/treadmill/). Manifest 'src' is repo-relative
# (build/<X>), so strip the staging-root prefix to resolve it here. Rows
# whose final dest IS the payload location (the ~/treadmill app tree:
# python/, static/, gpio.json, pyproject.toml) are already in place from
# that flatten — detected as an identity path and skipped. The rows that do
# real work are the binaries (-> /usr/local/bin) and unit files
# (-> /etc/systemd/system). A manifest src missing from the payload is a
# hard failure (never install a half/stale tree).
manifest_rows ./manifest.txt | while IFS=$'\t' read -r kind src dest mode owner; do
  srcfile="${src#build/}"
  [ -e "$srcfile" ] || { echo "setup: manifest src missing in payload: $srcfile (from $src)" >&2; exit 1; }
  rdest=$(manifest_resolve_dest "$dest" "$USER_NAME")
  abs_src=$(realpath -m -- "$srcfile")
  case "$kind" in
    bin|unit|file)
      tgt="$rdest"
      case "$rdest" in */) tgt="$rdest$(basename "$srcfile")" ;; esac
      abs_tgt=$(realpath -m -- "$tgt")
      if [ "$abs_src" = "$abs_tgt" ]; then
        echo "setup: $srcfile already in place ($tgt) — skip"; continue
      fi
      if [ "$owner" = root ]; then
        sudo install -D -m "$mode" "$srcfile" "$tgt"
      else
        install -D -m "$mode" "$srcfile" "$tgt"
      fi
      ;;
    tree)
      abs_tgt=$(realpath -m -- "$rdest")
      if [ "$abs_src" = "$abs_tgt" ]; then
        echo "setup: tree $srcfile already in place ($rdest) — skip"; continue
      fi
      mkdir -p "$rdest"
      rsync -a --delete "$srcfile" "$rdest"
      ;;
  esac
done

# --- Path A wiring: treadmill_io must start network-independently early ------
sudo systemctl daemon-reload
sudo systemctl enable treadmill-io treadmill-server
if systemctl list-unit-files | grep -q '^treadmill-critical.target'; then
  sudo systemctl add-wants treadmill-critical.target treadmill-io.service 2>/dev/null || true
fi
[ -x /usr/local/bin/ftms-daemon ] && sudo systemctl enable ftms || true
[ -x /usr/local/bin/hrm-daemon ]  && sudo systemctl enable hrm  || true

# --- Trim ladder step 4: zram thin margin (compressed RAM swap, no SD wear) --
if ! systemctl is-enabled systemd-zram-setup@zram0.service >/dev/null 2>&1; then
  sudo apt-get update -qq 2>/dev/null || true
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
       systemd-zram-generator 2>/dev/null || \
    logger -t treadmill-setup "zram-generator install failed (no swap margin)"
  printf '[zram0]\nzram-size = ram / 4\ncompression-algorithm = zstd\n' \
    | sudo tee /etc/systemd/zram-generator.conf >/dev/null
  sudo systemctl daemon-reload
  sudo systemctl start systemd-zram-setup@zram0.service 2>/dev/null || true
fi

# --- TLS cert (Tailscale, host-agnostic — derives name from this host) ------
TS_DOMAIN=$(tailscale status --json 2>/dev/null \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['Self']['DNSName'].rstrip('.'))" 2>/dev/null || true)
if [ -n "$TS_DOMAIN" ]; then
  if sudo tailscale cert "$TS_DOMAIN"; then
    sudo cp "$HOME/$TS_DOMAIN.crt" ts-cert.pem
    sudo cp "$HOME/$TS_DOMAIN.key" ts-key.pem
    sudo chown "$USER_NAME:$USER_NAME" ts-cert.pem ts-key.pem
    ln -sf ts-cert.pem cert.pem
    ln -sf ts-key.pem key.pem
  else
    echo "WARNING: TLS cert generation failed — server will run without HTTPS"
  fi
fi

# --- Venv: minimal deps only (trim ladder step 1) ---------------------------
VENV_DIR="$HOME/.venv"
[ -d "$VENV_DIR" ] || python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install -q --upgrade pip
"$VENV_DIR/bin/pip" install -q google-genai fastapi uvicorn python-multipart gpxpy

# --- Ordered atomic restart: server/ftms/hrm FIRST, treadmill_io LAST -------
# treadmill_io owns the safety logic (3h timeout, zero-on-emulate). Restart
# it last and atomically so its downtime is minimal and never overlaps an
# emulating belt.
echo "Restarting services (treadmill_io last)..."
sudo systemctl restart treadmill-server
[ -x /usr/local/bin/ftms-daemon ] && sudo systemctl restart ftms || true
[ -x /usr/local/bin/hrm-daemon ]  && sudo systemctl restart hrm  || true
sudo systemctl restart treadmill-io
echo "Done! Services restarted (treadmill_io restarted last)."
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `bash deploy/tests/test_setup_logic.sh`
Expected: `ALL TESTS PASSED`.

- [ ] **Step 5: Checkpoint (NO COMMIT — password gate)**

Run: `git add deploy/setup.sh deploy/tests/test_setup_logic.sh && git status --short`
Do **NOT** run `git commit`.

---

## Task 6: Path A drop-in for `treadmill-io.service.in` + single-worker guard

`treadmill_io` is already network-independent (`After=local-fs.target`). Wire it into Path A declaratively via the unit's `[Install]` and add a test that `server.py` stays single-uvicorn-worker (trim ladder step 2 — it currently is, this guards it).

**Files:**
- Modify: `deploy/treadmill-io.service.in:12-13`
- Test: `deploy/tests/test_service_units.sh`

- [ ] **Step 1: Write the failing test**

Create `deploy/tests/test_service_units.sh`:

```bash
#!/usr/bin/env bash
# Static guarantees for the rendered service units + the single-worker
# trim-ladder invariant.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
fail() { echo "FAIL: $1" >&2; exit 1; }
pass() { echo "ok: $1"; }

IO="$ROOT/deploy/treadmill-io.service.in"
grep -qE '^After=local-fs.target' "$IO" \
  || fail "treadmill-io must stay network-independent (After=local-fs.target)"
grep -qiE '^(After|Wants|Requires)=.*network' "$IO" \
  && fail "treadmill-io must NOT depend on the network (Path A)"
grep -q 'WantedBy=.*treadmill-critical.target' "$IO" \
  || fail "treadmill-io must declare WantedBy treadmill-critical.target (Path A slot)"
pass "treadmill-io.service.in wired to Path A, network-independent"

# Trim ladder step 2: server must run a SINGLE uvicorn worker.
grep -qE 'uvicorn\.run\(app, host=.*port=port' "$ROOT/python/server.py" \
  || fail "server.py uvicorn.run signature changed — re-verify worker count"
grep -qE 'workers\s*=\s*[2-9]' "$ROOT/python/server.py" \
  && fail "server.py must NOT request multiple uvicorn workers (512MB trim ladder)"
pass "server.py is single-uvicorn-worker (trim ladder step 2)"

echo "ALL TESTS PASSED"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `bash deploy/tests/test_service_units.sh`
Expected: FAIL — `treadmill-io.service.in` has `WantedBy=multi-user.target` only, no `treadmill-critical.target`.

- [ ] **Step 3: Wire the Path A slot**

In `deploy/treadmill-io.service.in`, replace the `[Install]` section (lines 12-13):

```ini
[Install]
WantedBy=multi-user.target
```

with:

```ini
[Install]
# Path A: belt-control transport starts in the network-independent early
# slot (~6.7s) alongside multi-user. treadmill-critical.target is installed
# by the provisioning fold-back; add-wants in setup.sh covers live deploys.
WantedBy=multi-user.target treadmill-critical.target
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `bash deploy/tests/test_service_units.sh`
Expected: `ALL TESTS PASSED`.

- [ ] **Step 5: Checkpoint (NO COMMIT — password gate)**

Run: `git add deploy/treadmill-io.service.in deploy/tests/test_service_units.sh && git status --short`
Do **NOT** run `git commit`.

---

## Task 7: Bake the full family — provisioning stages + first-boot install

The image baker must carry the full family (not just `fastboot.tgz`) and the first-boot script must install it via the manifest. Reuse the audited safe-extract pattern; do not weaken `build-image.sh`'s security boundary.

**Files:**
- Modify: `provisioning/dietpi/prepare-sd.sh:120-133`
- Modify: `provisioning/dietpi/Automation_Custom_Script.sh:90-92`
- Test: `provisioning/dietpi/tests/test_family_bake.sh`

- [ ] **Step 1: Write the failing test**

Create `provisioning/dietpi/tests/test_family_bake.sh`:

```bash
#!/usr/bin/env bash
# The bake path must carry the full software family and install it on first
# boot from the manifest. Dependency-free; no SD/Pi.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../../.." && pwd)
PREP="$ROOT/provisioning/dietpi/prepare-sd.sh"
ACS="$ROOT/provisioning/dietpi/Automation_Custom_Script.sh"
fail() { echo "FAIL: $1" >&2; exit 1; }
pass() { echo "ok: $1"; }

grep -qE 'family\.tgz|build/' "$PREP" \
  || fail "prepare-sd.sh must stage the built family (build/) into the image"
pass "prepare-sd.sh stages the family payload"

# First-boot script installs the family via the shared manifest, idempotently,
# and reuses the audited safe-extract guard (no absolute/.. members).
grep -q 'family.tgz' "$ACS" || fail "Automation_Custom_Script must unpack family.tgz"
grep -q 'setup.sh' "$ACS"   || fail "first-boot install must run the manifest-driven setup.sh"
# Audited unsafe-path guard retained for the family extract (fixed substring,
# same guard the fast-boot fold-back uses): refuse absolute / '..' members.
grep -F "tzf \"\$FW/family.tgz\" 2>/dev/null | grep -qE '^/|(^|/)\\.\\.(/|\$)'" "$ACS" >/dev/null \
  || fail "first-boot family extract must keep the audited unsafe-path guard"
grep -q '.family.applied' "$ACS" \
  || fail "first-boot family install must be idempotent (applied marker)"
grep -q 'refusing family.tgz with symlink members' "$ACS" \
  || fail "first-boot family extract must also reject symlink members (execute-path hardening)"
pass "first-boot install: manifest-driven, idempotent, safe-extract + symlink-reject"

# dash-safe (DietPi runs Automation_Custom_Script.sh under /bin/dash).
command -v dash >/dev/null 2>&1 && { dash -n "$ACS" || fail "ACS not dash-safe"; }
pass "Automation_Custom_Script.sh is dash-safe"

echo "ALL TESTS PASSED"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `bash provisioning/dietpi/tests/test_family_bake.sh`
Expected: FAIL — `prepare-sd.sh` only stages `fastboot.tgz`; `Automation_Custom_Script.sh` has no `family.tgz` / manifest install.

- [ ] **Step 3: Stage the family in `prepare-sd.sh`**

In `provisioning/dietpi/prepare-sd.sh`, after the existing fastboot block (the `if [ -d "$DIETPI_DIR/fastboot" ]` … `fi` ending at line 125), add:

```bash
# Full software family: if the caller cross-built artifacts into ./build,
# ship them as one tarball alongside the manifest so first-boot installs the
# appliance. Bake is opt-in: absent build/ => image is boot-to-SSH only.
FAMILY_ROOT=${FAMILY_ROOT:-"$DIETPI_DIR/../../build"}
if [ -d "$FAMILY_ROOT" ] && [ -f "$DIETPI_DIR/../../deploy/manifest.txt" ]; then
  fam=$(mktemp -d)
  trap 'rm -rf "$stage" "$fam"' EXIT   # extend the existing $stage trap so a
                                       # set -e abort here cannot leak $fam
  cp -r "$FAMILY_ROOT" "$fam/build"
  mkdir -p "$fam/deploy"
  cp "$DIETPI_DIR/../../deploy/manifest.txt" \
     "$DIETPI_DIR/../../deploy/lib-artifacts.sh" \
     "$DIETPI_DIR/../../deploy/setup.sh" "$fam/deploy/"
  tar czf "$stage/family.tgz" -C "$fam" build deploy
  rm -rf "$fam"
fi
```

Then extend the copy-to-boot block (lines 128-132). Replace:

```bash
cp "$stage/dietpi.txt" "$stage/dietpi-wifi.txt" "$stage/Automation_Custom_Script.sh" "$boot/"
extra=""
if [ -f "$stage/fastboot.tgz" ]; then cp "$stage/fastboot.tgz" "$boot/"; extra=", fastboot.tgz"; fi
sync
```

with:

```bash
cp "$stage/dietpi.txt" "$stage/dietpi-wifi.txt" "$stage/Automation_Custom_Script.sh" "$boot/"
extra=""
if [ -f "$stage/fastboot.tgz" ]; then cp "$stage/fastboot.tgz" "$boot/"; extra=", fastboot.tgz"; fi
if [ -f "$stage/family.tgz" ];   then cp "$stage/family.tgz"   "$boot/"; extra="$extra, family.tgz"; fi
sync
```

Also add a matching `mcopy` line in `provisioning/dietpi/build-image.sh` so the byte-level image carries it. After the existing fastboot mcopy block (build-image.sh lines 202-205), add:

```bash
  if [ -f "$stage/family.tgz" ]; then
    mcopy -o -i "$img@@$off" "$stage/family.tgz" :: \
      || { echo "writing family.tgz into image failed" >&2; return 1; }
  fi
```

- [ ] **Step 4: Add the first-boot family install (dash-safe, idempotent, safe-extract)**

In `provisioning/dietpi/Automation_Custom_Script.sh`, replace the final lines (the existing `fi` closing the fast-boot block through `exit 0`, lines 90-92):

```bash
fi

exit 0
```

with:

```bash
fi

# --- Full software family install (manifest-driven, idempotent) -------------
# Reuses the audited safe-extract posture from the fast-boot fold-back:
# refuse absolute / ".." members, extract to a temp dir with hardening
# flags, then install strictly via the shared manifest. dash/POSIX only.
if [ ! -f /boot/fastboot/.family.applied ] && [ -n "$FW" ] && [ -f "$FW/family.tgz" ]; then
  fok=1
  ftx=$(mktemp -d)
  if tar tzf "$FW/family.tgz" 2>/dev/null | grep -qE '^/|(^|/)\.\.(/|$)'; then
    logger -t fastboot "family: refusing unsafe family.tgz (absolute/.. paths)"; fok=0
  else
    tar xzf "$FW/family.tgz" -C "$ftx" --no-same-owner --no-same-permissions --no-overwrite-dir 2>/dev/null || fok=0
  fi
  # Defense-in-depth for the EXECUTE path (this block runs setup.sh, unlike
  # the fold-back which only copies a fixed allowlist): the listed-name guard
  # rejects absolute/.. names; additionally refuse ANY symlink member so a
  # clean-named symlink cannot redirect the cp/exec to outside $ftx. The real
  # payload (binaries, *.py, static, *.service, deploy/*) has no symlinks.
  if [ "$fok" = 1 ] && find "$ftx" -type l 2>/dev/null | grep -q .; then
    logger -t fastboot "family: refusing family.tgz with symlink members"; fok=0
  fi
  if [ "$fok" = 1 ] && [ -f "$ftx/deploy/setup.sh" ] && [ -f "$ftx/deploy/manifest.txt" ]; then
    # setup.sh is the single install path (manifest-driven) shared with the
    # live deployer; run it from the unpacked tree as the DietPi user.
    SETUP_USER=$(getent passwd 1000 | cut -d: -f1)
    [ -n "$SETUP_USER" ] || SETUP_USER=dietpi
    chmod +x "$ftx/deploy/setup.sh"
    mkdir -p "/home/$SETUP_USER/treadmill"
    cp -r "$ftx/build/." "/home/$SETUP_USER/treadmill/" 2>/dev/null || fok=0
    cp "$ftx/deploy/setup.sh" "$ftx/deploy/lib-artifacts.sh" \
       "$ftx/deploy/manifest.txt" "/home/$SETUP_USER/treadmill/" 2>/dev/null || fok=0
    chown -R "$SETUP_USER:$SETUP_USER" "/home/$SETUP_USER/treadmill" 2>/dev/null || true
    if [ "$fok" = 1 ]; then
      su - "$SETUP_USER" -c "cd ~/treadmill && bash setup.sh" 2>/dev/null || fok=0
    fi
  else
    fok=0
  fi
  rm -rf "$ftx"
  if [ "$fok" = 1 ]; then
    mkdir -p /boot/fastboot
    touch /boot/fastboot/.family.applied
    echo "Automation_Custom_Script: treadmill software family installed"
  else
    logger -t fastboot "family install incomplete — NOT marking applied; will retry next boot"
    echo "Automation_Custom_Script: family install incomplete (will retry)" >&2
  fi
fi

exit 0
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `bash provisioning/dietpi/tests/test_family_bake.sh`
Expected: `ALL TESTS PASSED`.

- [ ] **Step 6: Verify the existing provisioning suites still pass**

Run: `bash provisioning/dietpi/tests/test_lib.sh && bash provisioning/dietpi/tests/test_build_image.sh && bash provisioning/dietpi/tests/test_fastboot.sh`
Expected: each ends `ALL TESTS PASSED` (no regression to the audited toolkit).

- [ ] **Step 7: Checkpoint (NO COMMIT — password gate)**

Run: `git add provisioning/dietpi/prepare-sd.sh provisioning/dietpi/build-image.sh provisioning/dietpi/Automation_Custom_Script.sh provisioning/dietpi/tests/test_family_bake.sh && git status --short`
Do **NOT** run `git commit`.

---

## Task 8: 512 MB memory headroom gate

The objective fit pass/fail. Pi-run (operator-triggered, like the existing flash test) but the parsing/threshold logic has a dependency-free `--selftest` matching `measure-ttssh.sh`'s pattern.

**Files:**
- Create: `deploy/tests/mem-headroom.sh`
- Test: the `--selftest` path within the same file + `deploy/tests/test_mem_headroom_selftest.sh`

- [ ] **Step 1: Write the failing test**

Create `deploy/tests/test_mem_headroom_selftest.sh`:

```bash
#!/usr/bin/env bash
# Exercises mem-headroom.sh's pure logic with fixtures (no Pi).
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
M="$HERE/mem-headroom.sh"
fail() { echo "FAIL: $1" >&2; exit 1; }
pass() { echo "ok: $1"; }

bash "$M" --selftest >/dev/null 2>&1 || fail "mem-headroom --selftest must pass"
pass "mem-headroom selftest"

# 50MB available, no oom => PASS (threshold 40MB)
out=$(MEM_AVAIL_KB=51200 OOM_COUNT=0 bash "$M" --eval 2>&1)
echo "$out" | grep -q 'HEADROOM PASS' || fail "50MB/no-oom must PASS (got: $out)"
pass "50MB free, no oom => PASS"

# 30MB available => FAIL
out=$(MEM_AVAIL_KB=30720 OOM_COUNT=0 bash "$M" --eval 2>&1)
echo "$out" | grep -q 'HEADROOM FAIL' || fail "30MB must FAIL (<40MB)"
pass "30MB free => FAIL"

# oom-kill present => FAIL even with memory free
out=$(MEM_AVAIL_KB=80000 OOM_COUNT=1 bash "$M" --eval 2>&1)
echo "$out" | grep -q 'HEADROOM FAIL' || fail "any oom-kill must FAIL"
pass "oom-kill => FAIL regardless of free memory"

# non-numeric measurement must FAIL-CLOSED (a fit gate must never declare
# "fits" when it could not actually measure — no fail-open).
out=$(MEM_AVAIL_KB=garbage OOM_COUNT=0 bash "$M" --eval 2>&1)
echo "$out" | grep -q 'HEADROOM FAIL' || fail "non-numeric MemAvailable must FAIL-CLOSED (got: $out)"
pass "non-numeric MemAvailable => FAIL (no fail-open)"
out=$(MEM_AVAIL_KB=51200 OOM_COUNT=xx bash "$M" --eval 2>&1)
echo "$out" | grep -q 'HEADROOM FAIL' || fail "non-numeric oom-count must FAIL-CLOSED (got: $out)"
pass "non-numeric oom-count => FAIL (no fail-open)"

echo "ALL TESTS PASSED"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `bash deploy/tests/test_mem_headroom_selftest.sh`
Expected: FAIL — `mem-headroom.sh` does not exist.

- [ ] **Step 3: Create `deploy/tests/mem-headroom.sh`**

```bash
#!/usr/bin/env bash
# 512MB headroom gate. On the Pi (default), drives synthetic load across the
# full family then asserts steady-state MemAvailable >= 40MB and zero
# oom-kill in the journal. --eval evaluates injected MEM_AVAIL_KB/OOM_COUNT
# (pure logic, for tests). --selftest runs internal assertions, no Pi.
set -u
THRESHOLD_KB=$(( 40 * 1024 ))   # 40 MB
PI_HOST="${PI_HOST:-rpi-zero}"
SERVER_PORT="${SERVER_PORT:-8000}"

evaluate() {
  local avail=$1 ooms=$2
  # Fail CLOSED on a non-numeric/empty measurement: a fit gate must never
  # declare "fits" when it could not actually measure (a bad `[ -lt ]` on a
  # non-integer exits 2 → if-false → would silently fall through to PASS).
  case "$avail" in ''|*[!0-9]*) echo "HEADROOM FAIL: MemAvailable not a valid integer (${avail:-empty})"; return 1 ;; esac
  case "$ooms"  in ''|*[!0-9]*) echo "HEADROOM FAIL: oom-count not a valid integer (${ooms:-empty})"; return 1 ;; esac
  if [ "$ooms" -gt 0 ]; then
    echo "HEADROOM FAIL: $ooms oom-kill event(s) in journal"; return 1
  fi
  if [ "$avail" -lt "$THRESHOLD_KB" ]; then
    echo "HEADROOM FAIL: MemAvailable ${avail}kB < ${THRESHOLD_KB}kB (40MB)"; return 1
  fi
  echo "HEADROOM PASS: MemAvailable ${avail}kB, 0 oom-kill"; return 0
}

case "${1:-}" in
  --selftest)
    evaluate 51200 0  >/dev/null || { echo "selftest: 50MB should pass" >&2; exit 1; }
    evaluate 30720 0  >/dev/null && { echo "selftest: 30MB should fail" >&2; exit 1; }
    evaluate 80000 1  >/dev/null && { echo "selftest: oom should fail" >&2; exit 1; }
    echo "selftest OK"; exit 0 ;;
  --eval)
    evaluate "${MEM_AVAIL_KB:-0}" "${OOM_COUNT:-0}"; exit $? ;;
esac

# --- Real run on the Pi ------------------------------------------------------
echo "== mem-headroom: $PI_HOST, full family + synthetic load =="
ssh "$PI_HOST" 'sudo systemctl restart treadmill-io treadmill-server ftms hrm' || {
  echo "could not restart family on $PI_HOST" >&2; exit 1; }
sleep 20
# Synthetic load: one AI chat round-trip + an active run program. FTMS/HRM
# notify on their own once up.
curl -sk --max-time 30 -X POST "https://$PI_HOST:$SERVER_PORT/api/chat" \
  -H 'content-type: application/json' \
  -d '{"message":"start an easy 20 minute run"}' >/dev/null 2>&1 || true
curl -sk --max-time 10 -X POST "https://$PI_HOST:$SERVER_PORT/api/program/start" \
  -H 'content-type: application/json' -d '{}' >/dev/null 2>&1 || true
sleep 30
avail=$(ssh "$PI_HOST" "awk '/MemAvailable/{print \$2}' /proc/meminfo")
ooms=$(ssh "$PI_HOST" "journalctl -k --since '-5 min' 2>/dev/null | grep -c -i 'oom-kill\|Out of memory' || true")
ssh "$PI_HOST" 'curl -sk --max-time 10 -X POST https://localhost:'"$SERVER_PORT"'/api/program/stop -H "content-type: application/json" -d "{}"' >/dev/null 2>&1 || true
echo "MemAvailable=${avail}kB  oom-kill=${ooms}"
evaluate "${avail:-0}" "${ooms:-0}"
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `bash deploy/tests/test_mem_headroom_selftest.sh`
Expected: `ALL TESTS PASSED`.

- [ ] **Step 5: Checkpoint (NO COMMIT — password gate)**

Run: `git add deploy/tests/mem-headroom.sh deploy/tests/test_mem_headroom_selftest.sh && git status --short`
Do **NOT** run `git commit`.

---

## Task 9: `make image`, docs, and full-suite integration

Wire the bake entrypoint, update the docs to reality (CLAUDE.md "Docs Stay Current" rule), and prove every dependency-free suite is green.

**Files:**
- Modify: `Makefile` (add `image` target body)
- Modify: `CLAUDE.md:7-30` (Deployment section)
- Test: `deploy/tests/test_all_suites.sh`

- [ ] **Step 1: Write the failing test**

Create `deploy/tests/test_all_suites.sh`:

```bash
#!/usr/bin/env bash
# Meta-suite: every dependency-free harness must be green and `make image`
# must exist as a real target.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
fail() { echo "FAIL: $1" >&2; exit 1; }
pass() { echo "ok: $1"; }

for t in test_manifest.sh test_deploy_dryrun.sh test_setup_logic.sh \
         test_service_units.sh test_mem_headroom_selftest.sh; do
  bash "$HERE/$t" >/dev/null 2>&1 || fail "$t not green"
  pass "$t green"
done
for t in test_lib.sh test_build_image.sh test_fastboot.sh test_family_bake.sh; do
  bash "$ROOT/provisioning/dietpi/tests/$t" >/dev/null 2>&1 || fail "provisioning/$t not green"
  pass "provisioning/$t green"
done

grep -qE '^image:' "$ROOT/Makefile" || fail "Makefile must define an 'image' target"
pass "make image target exists"

echo "ALL TESTS PASSED"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `bash deploy/tests/test_all_suites.sh`
Expected: FAIL — no `image:` target in the Makefile yet.

- [ ] **Step 3: Add the `image` target**

In `Makefile`, after the `stage:` target add:

```makefile
# Bake a flashable full-appliance image: cross-build everything, stage it,
# then run the audited userspace image builder which carries build/ +
# manifest into the .img via the provisioning toolkit.
image: cross
	deploy/deploy.sh --stage-only
	provisioning/dietpi/build-image.sh
	@echo "Image built. Flash with provisioning/dietpi/build-image.sh --flash /dev/sdX (operator)."
```

- [ ] **Step 4: Update `CLAUDE.md` Deployment section**

In `CLAUDE.md`, replace the ENTIRE current Deployment section body — the prose line PLUS the whole following ```` ```bash ```` fenced block (do NOT replace only the prose line: that would orphan the code fence and leave the stale "builds on Pi" command, a self-contradiction the "Docs Stay Current" rule forbids). Replace this exact block:

````
The Raspberry Pi connected to the treadmill is at host `rpi`. All four services are managed via systemd and deployed with `make deploy`.

```bash
# Deploy everything to Pi (stages build/, rsyncs, builds on Pi, restarts all services):
make deploy                    # or: deploy/deploy.sh

# Stage build/ directory without deploying:
make stage

# Services on Pi (managed by systemd, auto-start on boot):
sudo systemctl status treadmill-io      # C++ GPIO daemon
sudo systemctl status treadmill-server  # FastAPI web server
sudo systemctl status ftms              # FTMS Bluetooth daemon
sudo systemctl status hrm               # HRM Bluetooth daemon

# Service dependency chain:
#   treadmill-io  ←  treadmill-server (After+Wants)
#   treadmill-io  ←  ftms (After+Wants)
#   bluetooth     ←  ftms (After+Requires)
#   bluetooth     ←  hrm (After+Requires)

# Service templates in deploy/*.service.in (rendered during stage)

# Manual tools (for debugging):
python3 python/tools/dual_monitor.py        # Primary TUI (curses, side-by-side panes)
python3 python/tools/listen.py              # Simple KV listener (--changes, --unique flags)
```
````

with this exact block (single coherent fenced block; corrected deploy reality; still-accurate systemctl/dependency/templates/tools content preserved + a Path A line added):

````
The treadmill controller is the Pi Zero 2 W at host `rpi-zero` (primary); the Pi 4 `rpi` is a hot spare. `PI_HOST` selects the target (default `rpi-zero`). All four services are systemd-managed.

Compiled code (C++ `treadmill_io`, Rust `ftms-daemon`/`hrm-daemon`) is **cross-built off-Pi** in one aarch64 Docker toolchain; build-on-Pi is retired. The Python venv is still `pip`-installed on the Pi. Install is driven by the single source of truth `deploy/manifest.txt` (parsed as data), shared by the live deployer and the image baker so a flashed Pi and an rsync'd Pi are byte-identical. Deploy refuses to run while the belt is moving (queries /api/status; `FORCE=1` overrides). `treadmill_io` is wired into the network-independent Path A slot (`treadmill-critical.target`) so belt control starts early. On the 512MB Zero, run the headroom gate after deploy: `bash deploy/tests/mem-headroom.sh` (asserts ≥40MB MemAvailable, 0 oom-kill).

```bash
# Build all 3 aarch64 binaries in containers -> build/
make cross

# Cross-build + manifest rsync + ordered atomic restart (treadmill_io last):
make deploy                    # or: deploy/deploy.sh

# Bake a flashable full-appliance .img (provisioning toolkit):
make image

# Assemble build/ without deploying:
make stage

# Target the Pi 4 spare instead of the Zero:
PI_HOST=rpi make deploy

# Services on Pi (managed by systemd, auto-start on boot):
sudo systemctl status treadmill-io      # C++ GPIO daemon
sudo systemctl status treadmill-server  # FastAPI web server
sudo systemctl status ftms              # FTMS Bluetooth daemon
sudo systemctl status hrm               # HRM Bluetooth daemon

# Service dependency chain:
#   treadmill-io  ←  treadmill-server (After+Wants)
#   treadmill-io  ←  ftms (After+Wants)
#   bluetooth     ←  ftms (After+Requires)
#   bluetooth     ←  hrm (After+Requires)
#   treadmill-critical.target  ←  treadmill-io (Path A, network-independent early start)

# Service templates in deploy/*.service.in (rendered during stage)

# Manual tools (for debugging):
python3 python/tools/dual_monitor.py        # Primary TUI (curses, side-by-side panes)
python3 python/tools/listen.py              # Simple KV listener (--changes, --unique flags)
```
````

- [ ] **Step 5: Run the test to verify it passes**

Run: `bash deploy/tests/test_all_suites.sh`
Expected: `ALL TESTS PASSED` (every harness green, `image` target present).

- [ ] **Step 6: Final verification — full dependency-free gate**

Run:
```
bash deploy/tests/test_all_suites.sh && \
python3 -m pytest python/tests -m "not hardware" -q --ignore=python/tests/test_voice_commands.py
# (test_lazy_genai.py removed 2026-05-18 with the Task-3 revert; the live
# voice suite is excluded — it hangs ~15 min on a real GEMINI key, pre-existing)
```
Expected: all green; the broad pytest run shows no regressions vs. the pre-plan baseline.

- [ ] **Step 7: Checkpoint (NO COMMIT — password gate)**

Run: `git add Makefile CLAUDE.md deploy/tests/test_all_suites.sh && git status --short`
Do **NOT** run `git commit`. Report to the owner: all tasks complete, all suites green, work staged and awaiting the commit password.

---

## Post-implementation (owner-gated, do not skip)

1. **Security audit (mandatory per CLAUDE.md) — DISPOSITION (2026-05-17, owner-decided):**
   - Track 1 — dependency CVE scan: **DONE, N/A with evidence.** No new pip/npm/cargo deps (`git diff --cached -- pyproject.toml` empty; no Cargo.toml/package.json/requirements changes; venv set unchanged: `google-genai fastapi uvicorn python-multipart gpxpy`). New OS package `systemd-zram-generator` is a Debian package, not a code dep — not a CVE-scan target.
   - Track 2 — `codex exec --sandbox read-only`: **EXPLICITLY PUNTED (owner-authorized), justification recorded.** `codex` (v0.130.0) is installed but `codex exec` hung with zero output and was SIGTERM'd at the timeout on two attempts (full prompt, then a tightly-scoped 3-file prompt) — an environmental block in the non-interactive background context (likely an auth/interactive gate), NOT a finding. Per the owner's explicit decision, the **substitute security pass of record** is the per-task two-stage review: every task received a dedicated code-quality review (most on the most-capable model) that performed *adversarial* security scrutiny of each trust boundary, with several reviewers running concrete exploit attempts — the manifest parser (glob/word-split escape attempts, `..`/absolute/dest-root bypass, parse-as-data confirmation), the first-boot root `su` family install + tar extraction (symlink-target redirect simulated, marker-gating traced for partial-install), `deploy.sh` belt-safety (14 adversarial `/api/status` inputs incl. emulate `speed:null`), the headroom gate (fail-open empirically found & fixed to fail-closed), and the whole-implementation integration review (6 cross-task seams traced end-to-end). This is an explicit punt-with-justification of the codex *tool* (CLAUDE.md permits fix-or-explicitly-punt), not a silent defer; the codex run remains available to re-run interactively (`! codex exec --sandbox read-only ...`) if desired before/after commit.
     - **Residual dispositions (recorded):** (a) the listed-name safe-extract guard (shared, identical, by both `fastboot.tgz` fold-back and `family.tgz`) rejects absolute/`..` member *names* but not a clean-named symlink member whose *target* is absolute. **Dispositioned:** the family **execute** path (it runs `setup.sh`, the higher blast radius) got an additional `find "$ftx" -type l | grep -q .` symlink-member reject (Task 7), adversarially verified by the re-review (catches nested + clean-named-top-level symlinks, no false-reject of the real all-regular-file payload). The `fastboot.tgz` fold-back's copy-only path keeps the original guard unchanged (lower blast radius — fixed allowlist copy, no execute; same trusted-producer threat model; touching the audited block was deliberately avoided). Accepted residual. (b) The "will retry next boot" log wording in `Automation_Custom_Script.sh` (both blocks) is technically inaccurate — DietPi runs the script once at first-run setup, so a failed install is not auto-retried on a normal reboot. **Dispositioned:** pre-existing parity with the audited fold-back, left unchanged to avoid mutating audited code; accepted (cosmetic log wording, fail-closed behavior is correct — a failed install simply does not mark `.family.applied`). Both are accepted residuals, not blockers; re-run codex interactively later if an independent confirmation is wanted.
2. **Operator-only hardware proof** (an agent cannot self-run): `make image` → flash → the Zero 2 W boots straight into a working treadmill (all 4 services up, UI reachable), then `bash deploy/tests/mem-headroom.sh` passes. **Trim ladder step 5 is intentionally NOT built now (YAGNI — the spec gates it on "the headroom gate still failing with steps 1–4 applied").** Only if the gate fails on real hardware: implement the concrete last-resort mechanism — a `TREADMILL_DISABLE_AI=1` env var, set in `treadmill-server.service`, that makes `server.py`'s AI endpoints (`/api/chat`, `/api/tts`, `/api/tool`, voice) return HTTP 503 `{"error":"AI coach not available on this device"}` *before* any `get_client()` call, so the SDK never loads. Core control + FTMS + HRM are untouched. Add it as a follow-up task with its own TDD test, then re-measure the gate.
3. **Owner commit.** Only after the owner types the password, make one commit of all staged work.
