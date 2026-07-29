#!/usr/bin/env python3
"""check_unsafe_budget.py — make the `unsafe` containment REAL.

Why this exists
---------------
The firmware crate root carries ``#![deny(unsafe_code)]`` and grants
``#[allow(unsafe_code)]`` to three modules. A reviewer disproved the claim that
this makes the containment compiler-enforced, by counterexample: ``deny`` is a
LINT LEVEL, and any module may lift it for itself with an inner
``#[allow(unsafe_code)]``. A new module could therefore start using ``unsafe``
and nothing would fail. (``forbid`` cannot be lifted — an inner ``allow`` under
a ``forbid`` is a hard compile error — which is why ``safety_core`` and the
unsafe-free firmware modules now use ``forbid``. The crate root cannot: it has
to grant ``allow`` to the three modules that legitimately need FFI.)

This script closes the remaining hole. It is a REQUIRED gate in
``tools/build.sh``; a failure fails the build.

Enforced
--------
1. Every crate in ``PURE_FORBID_CRATES`` carries ``#![forbid(unsafe_code)]``
   and contains no ``unsafe`` token at all.
2. The firmware modules listed in ``FORBID_MODULES`` each carry their own
   module-level ``#![forbid(unsafe_code)]``.
3. The set of firmware files containing an ``unsafe`` BLOCK/``impl``/``fn`` is
   exactly ``UNSAFE_ALLOWLIST``.
4. Every ``allow(unsafe_code)`` attribute in the firmware sits at one of the
   sites in ``ALLOW_SITES``.
5. Every ``unsafe`` block is preceded (within 12 lines, in its own function)
   by a ``// SAFETY:`` comment.
6. The PRODUCTION unsafe line count equals ``PRODUCTION_UNSAFE_LINES`` and the
   TEST-IMAGE-ONLY count equals ``QEMU_UNSAFE_LINES``. Changing either is a
   deliberate act that has to update this file.

Exit status 0 = the budget holds.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ESP32_RS = HERE.parent
FW_SRC = ESP32_RS / "esp32tap" / "src"
CORE_SRC = ESP32_RS / "safety_core" / "src"

# Crates that are `#![forbid(unsafe_code)]` and contain no `unsafe` token at
# all. `reqbudget` and `recstore` are deliberately absent: reqbudget owns the
# static request pool and needs FFI-free `unsafe` for it.
PURE_FORBID_CRATES = ("safety_core", "program_core", "ble_core")

# Firmware modules whose unsafe-freedom is COMPILER-enforced.
FORBID_MODULES = ("tasks/mod.rs", "context.rs", "pins.rs", "control.rs", "hr.rs")

# Production (flashed) unsafe-bearing files.
PRODUCTION_UNSAFE = {
    "hal/clock.rs",
    "hal/delay.rs",
    "hal/gpio.rs",
    "hal/uart.rs",
    "hal/wdt.rs",
    "log.rs",
}
# Test-image-only (feature = "qemu-test"), never flashed to a treadmill.
QEMU_UNSAFE = {
    "qemu_test/mod.rs",
    "qemu_test/motor_tap.rs",
    # One call: `esp_restart` behind the `QT reboot` verb. It exists so the
    # NVS-persistence claim can be PROVEN across a real SoC reset rather than
    # asserted from a return code. Test-image only — `feature = "qemu-test"`
    # is never enabled in the flashed build, so nothing can reboot a treadmill.
    "qemu_test/shim_task.rs",
    # Slice 1 network foundation. Behind `feature = "net"`, which the
    # production image does NOT enable, so none of this is flashed to a
    # treadmill yet. It moves to PRODUCTION_UNSAFE when the network tier
    # ships — deliberately, with the budget re-counted at that point.
    "net/mod.rs",
    "net/http.rs",
    "net/api.rs",
    "net/store.rs",
    "net/tls.rs",
    "net/mdns.rs",
    "net/program.rs",
    # Slice 5 persistence tier. `net/session.rs` is deliberately ABSENT: the
    # session recorder reaches flash through `net::store` and NVS through
    # `net::profile`, so the task that runs every second on its own contains no
    # FFI at all.
    "net/records.rs",
    "net/profile.rs",
    # The `/ws` push. See net/ws.rs — the FFI is socket enumeration plus one
    # frame send; `net/session.rs` still contains no FFI at all.
    "net/ws.rs",
    # Slice 6 BLE tier, behind `feature = "ble"` (which, like `net`, the
    # PRODUCTION image does not enable). `ble/mod.rs` is the NimBLE port
    # lifecycle, `ble/ftms.rs` the GATT peripheral and `ble/central.rs` the
    # HRM client. All three are pure FFI: the bytes they move are produced and
    # consumed by `ble_core`, which is forbid+unsafe-free and host-tested.
    #
    # `hr.rs` and `net/hrm.rs` are DELIBERATELY ABSENT from this list.
    # `net/hrm.rs` contains the IDF handler callbacks and so does appear below;
    # `hr.rs` — the shared state a peer's bytes actually land in — contains no
    # FFI at all and is `#![forbid(unsafe_code)]`, which is why it is in
    # FORBID_MODULES instead.
    "ble/mod.rs",
    "ble/ftms.rs",
    "ble/central.rs",
    "net/hrm.rs",
}
UNSAFE_ALLOWLIST = PRODUCTION_UNSAFE | QEMU_UNSAFE

# Exactly where `allow(unsafe_code)` may appear: file -> the modules it grants.
ALLOW_SITES = {
    "main.rs": {"hal", "log", "qemu_test", "net", "ble"},
}

# The documented budget.
#
# COUNTING RULE (this script IS the definition, so the number is reproducible):
# for each `unsafe { ... }` block, every physical source line from the line
# carrying the `unsafe` keyword through the line carrying its closing brace,
# INCLUSIVE, after comments and string literals have been blanked out; each
# `unsafe fn` / `unsafe impl` / `unsafe trait` declaration counts as 1.
#
# The earlier hand-written figure of "66 production lines" was produced by an
# unstated rule and is superseded: 69 is what the rule above measures on the
# same code, and it is now a build gate rather than a claim.
PRODUCTION_UNSAFE_LINES = 69
# 22 shim + 25 net (eth) + 49 net (http: banner + ws handler). The ws
# handler is one `unsafe extern "C"` fn, so the counting rule attributes its
# whole body; the FFI surface inside it is 4 calls. The net delta is Slice 1's esp_netif/esp_eth bring-up:
# each FFI call is its own one-expression block (struct setup stays in
# safe code), so this counts the C boundary and nothing else. When the
# network tier ships, net/mod.rs moves to PRODUCTION_UNSAFE and BOTH
# numbers get re-counted deliberately.
#
# 268 -> 309 for Slice 3 (TLS + mDNS), all of it new C boundary and none of it
# new *style*: net/mdns.rs is 6 one-expression FFI wrappers, net/tls.rs gains 6
# more for the NVS load/store round-trip, and qemu_test/shim_task.rs adds
# exactly 1 (`esp_restart`). The single large block is the mbedtls keygen,
# which was already counted; it stays one block because its contexts must be
# freed on every exit path and splitting it would multiply the cleanup, not the
# safety.
#
# 309 -> 346 for Slice 4 (the interval executor), +37, ALL of it the ten new
# program endpoints in net/program.rs — and the real C boundary in that file is
# 3 calls. The rest is the counting rule attributing the body of an
# `unsafe extern "C" fn`, which is why both IDF callbacks there are THIN
# wrappers: each reads the one raw field a callback must and delegates to a
# safe fn, so the logic that decides what the belt is told sits outside any
# unsafe region.
#
# net/api.rs did not grow despite being rewritten: its lease/clamp/auto-emulate
# logic moved into control.rs, which is `#![forbid(unsafe_code)]`. That is a
# net reduction in unsafe-attributed SAFETY-CRITICAL code and the reason the
# increase here is smaller than ten endpoints would suggest.
#
# program_core, like safety_core, is forbid + unsafe-free and adds nothing.
#
# 346 -> 355, +9: the `QT heap` probe in qemu_test/shim_task.rs. Three
# argument-free IDF accessors in ONE block (esp_get_free_heap_size,
# esp_get_minimum_free_heap_size, heap_caps_get_largest_free_block). It reads
# and returns integers and mutates nothing. It is TEST-IMAGE ONLY — `feature =
# "qemu-test"` is never enabled in the flashed build — and it exists so the
# adversarial memory scenarios can plot a real heap curve instead of asserting
# convergence from the absence of a reboot.
# 381 -> 470 with NO code change, when `strip_comments_and_strings` learned
# what a raw string is. The old lexer treated the inner quotes of
# `br#"{"ok":false}"#` as delimiters, which blanked the rest of `net/api.rs`
# from `status_handler` to the profile block — so two `unsafe extern "C"`
# functions were never counted at all and the published figure was 89 lines
# short of what the stated rule measures. The number below is the first one
# that is reproducible from the rule.
#
# 470 -> 528 for Slice 5 (the persistence tier), +58, and the parts do not all
# have the same sign:
#   + net/records.rs   49  two IDF callbacks, the chunked-response sink and one
#                         body reader. The real C boundary is 7 calls; the rest
#                         is the counting rule attributing the bodies of the
#                         `unsafe extern "C"` callbacks, which is why both are
#                         thin wrappers that read one scalar and delegate.
#   + net/profile.rs   34  four IDF callbacks and the registration table. The
#                         NVS boundary is REUSED from net/tls.rs rather than
#                         reopened, so persistence itself adds no unsafe here.
#   - net/api.rs      -25  the profile handlers left it for net/profile.rs.
# net/session.rs adds ZERO: the task that runs every second reaches flash
# through net::store and NVS through net::profile and contains no FFI.
#
# 528 -> 537, +9, all of it review fixes that made two FFI boundaries stricter
# rather than wider: `uri_of` now borrows the request (`&sys::httpd_req_t`) so
# the URI's lifetime is bounded by the compiler instead of chosen by the
# caller, and `net/profile.rs`'s update callback reads and CHECKS the id in the
# path — a wildcard route hands it everything under `/api/profiles/`, and
# without the check any id rewrote the local profile.
# 537 -> 543, +6: `net/api.rs::abandon_body`. ONE block, two calls
# (`httpd_req_to_sockfd`, `lwip_shutdown`), and it is the whole reason the
# request-duration bound WORKS rather than defers: `httpd_req_delete` purges an
# unread body after the handler returns, through the same per-recv timeout, so
# answering 408 and returning would have handed the dribbling client the single
# worker straight back through IDF's own code.
# 543 -> 552, +9: `net/ws.rs`. Three FFI calls in three one-expression blocks
# (`httpd_get_client_list`, `httpd_ws_get_fd_info`) plus one block that fills a
# `httpd_ws_frame_t` and sends it. It is the whole C boundary of the live push,
# and `net/session.rs` — the WDT-supervised task that drives it — STILL contains
# no FFI, which is why the pusher is a module rather than three lines in the
# task.
# 552 -> 580, +28: `net/profile.rs` gains the routes it was missing. Three more
# IDF callbacks (`/api/user` GET/PUT, the unsupported-feature answer and the
# DELETE that picks its message from the path) plus a wider registration table.
# The counting rule attributes the whole body of an `unsafe extern "C" fn`,
# which is why each is a thin wrapper that reads one raw field and delegates —
# the actual C boundary added is zero calls. Leaving these routes UNREGISTERED
# was cheaper in this number and cost the user a raw `HTTP 404 Not Found` on the
# app's first screen and a weight the calorie maths could never be told.
# 580 -> 589, +9: `net/ws.rs` moved its sends onto the httpd task through
# `httpd_queue_work`, which adds the queue call and the queued `unsafe extern
# "C"` callback and drops nothing (the send is still one block, and the
# close-on-failure is one more). Nine lines for the removal of a data race
# between the recorder and the server's own session teardown.
# 589 -> 597, +8: `net::api::respond_and_close` split out of `abandon_body` so
# the routes that DECLINE a body can use it. A handler that answers without
# reading is not finished with the connection — IDF purges the rest through the
# per-recv timeout with no deadline near it — so the four profile routes added
# for the app's picker had reopened the dribbling-writer hole on a body that
# could be a megabyte. The FFI is unchanged (one `httpd_req_to_sockfd`, one
# `lwip_shutdown`); the growth is the counting rule attributing the second
# function's body.
# 597 -> 1094, +497, ALL of it Slice 6 (the BLE tier) and none of it in the
# production image — `feature = "ble"`, like `net`, is carried only by the
# QEMU-test build. The parts:
#   + ble/central.rs  223  the HRM client. Five NimBLE callbacks (GAP events,
#                          service/characteristic/descriptor discovery) plus
#                          scan/connect/terminate. The real C boundary is 11
#                          calls; the rest is the counting rule attributing the
#                          whole body of every `unsafe extern "C" fn`.
#   + ble/ftms.rs     163  the GATT peripheral: the access callback, the GAP
#                          callback, registration, advertising, notify and
#                          indicate. 14 C calls.
#   + net/hrm.rs       77  four IDF request handlers. The C boundary added is
#                          ZERO calls — every one of them reads a scalar,
#                          delegates to `crate::hr` (which is
#                          `#![forbid(unsafe_code)]`), and answers through
#                          `net::api`'s existing helpers.
#   + net/api.rs      +25  `parse_key_str` sits inside a file the rule
#                          attributes generously; the parser itself is safe
#                          code and the handlers around it did not change.
#   + ble/mod.rs       34  the NimBLE port lifecycle: `nimble_port_init`, the
#                          `ble_hs_cfg` callback wiring, the two service
#                          constructors and the host task.
#
# THE NUMBER IS LARGE AND THE SHAPE IS THE POINT. Every byte these files move
# is produced or consumed by `ble_core`, which is `#![forbid(unsafe_code)]`,
# zero-dependency and host-tested against the Pi daemons' own vectors — and
# `crate::hr`, where an untrusted peer's bytes actually land, is
# `forbid(unsafe_code)` too (FORBID_MODULES above). What is inside the unsafe
# regions is transport: mbufs in, mbufs out, handles stored in atomics.
QEMU_UNSAFE_LINES = 1094

_UNSAFE_TOKEN = re.compile(r"(?<![A-Za-z0-9_])unsafe(?![A-Za-z0-9_])")
_ALLOW_UNSAFE = re.compile(r"#!?\[allow\(([^)]*)\)\]")


def rel(p: Path, root: Path) -> str:
    return str(p.relative_to(root)).replace("\\", "/")


def rs_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.rs") if "target" not in p.parts)


def strip_comments_and_strings(text: str) -> str:
    """Blank out // comments, /* */ comments and string/char literals.

    Keeps line structure so line numbers stay meaningful.

    RAW STRINGS AND CHAR LITERALS ARE HANDLED, and that is not a refinement —
    it is the difference between this gate measuring something and measuring
    noise. The block counter below balances braces on the OUTPUT of this
    function, so a `{` or `}` this misses is counted as code:

      * `br#"{"ok":false}"#` — the earlier lexer took the inner `"` characters
        as string delimiters, so the literal's braces and the parity of every
        quote after it leaked into "code". In `net/api.rs` that swallowed
        `status_handler` and `motion_handler` whole: both are
        `unsafe extern "C"` functions and NEITHER was counted, so the file's
        published figure was ~64 lines short of what the rule says it is.
      * `write_char('}')` — a brace char literal closed an enclosing block
        early, ending an `unsafe` block's attribution at the wrong line.

    Both defects moved the total when unrelated code changed, which makes a
    budget nobody can reproduce. The numbers below were re-derived after this
    fix; see the note on QEMU_UNSAFE_LINES.
    """
    out = []
    i = 0
    n = len(text)

    def blank(ch: str) -> str:
        return "\n" if ch == "\n" else " "

    while i < n:
        c = text[i]
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                out.append(" ")
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            while i < n and not (text[i] == "*" and i + 1 < n and text[i + 1] == "/"):
                out.append(blank(text[i]))
                i += 1
            out.append("  ")
            i += 2
            continue

        # Raw string: r"…", r#"…"#, br##"…"##. Detected at the token start so
        # the trailing `r` of an identifier (`for`, `str`) cannot open one.
        j = i
        if text[j] == "b" and j + 1 < n and text[j + 1] == "r":
            j += 1
        if text[j] == "r":
            k = j + 1
            hashes = 0
            while k < n and text[k] == "#":
                hashes += 1
                k += 1
            if k < n and text[k] == '"':
                close = '"' + "#" * hashes
                end = text.find(close, k + 1)
                end = n if end < 0 else end + len(close)
                out.extend(blank(ch) for ch in text[i:end])
                i = end
                continue

        if c == '"':
            out.append(" ")
            i += 1
            while i < n and text[i] != '"':
                if text[i] == "\\":
                    out.append(" ")
                    i += 1
                    if i < n:
                        out.append(blank(text[i]))
                        i += 1
                    continue
                out.append(blank(text[i]))
                i += 1
            if i < n:
                out.append(" ")
                i += 1
            continue

        if c == "'":
            # A char literal, or a lifetime. `'\n'` and `'x'` are literals;
            # `'a>` and `'static` are lifetimes and must stay as code.
            if i + 1 < n and text[i + 1] == "\\":
                k = i + 2
                while k < n and text[k] != "'":
                    k += 1
                k = min(k + 1, n)
                out.extend(blank(ch) for ch in text[i:k])
                i = k
                continue
            if i + 2 < n and text[i + 2] == "'":
                out.append("   ")
                i += 3
                continue

        out.append(c)
        i += 1
    return "".join(out)


def unsafe_line_count(text: str) -> int:
    """Physical lines of code governed by an `unsafe` block or item."""
    code = strip_comments_and_strings(text)
    lines = code.split("\n")
    total = 0
    for idx, line in enumerate(lines):
        if not _UNSAFE_TOKEN.search(line):
            continue
        # `unsafe impl` / `unsafe fn` / `unsafe trait` declarations count as 1.
        if re.search(r"unsafe\s+(impl|fn|trait)\b", line):
            total += 1
            continue
        # An `unsafe { ... }` block: count until braces balance.
        depth = 0
        started = False
        j = idx
        body = 0
        while j < len(lines):
            for ch in lines[j]:
                if ch == "{":
                    depth += 1
                    started = True
                elif ch == "}":
                    depth -= 1
            if started and j > idx:
                body += 1
            if started and depth == 0:
                break
            j += 1
        # Count the whole block INCLUSIVE of the `unsafe {` line and the
        # closing brace — "how many source lines are inside an unsafe region"
        # is the number the README publishes.
        total += body + 1
    return total


def check() -> list[str]:
    failures: list[str] = []

    # --- 1. the PURE crates are forbid + unsafe-free ----------------------
    #
    # `safety_core` was the only one checked here, and `program_core` and
    # `ble_core` were in the same hole `test_store_persistence.py` was in:
    # both carry `#![forbid(unsafe_code)]` and NOTHING verified it was still
    # there. `forbid` cannot be lifted by an inner `allow`, so the line itself
    # is the whole guarantee — deleting it is a one-character act that no
    # other gate would notice.
    for crate in PURE_FORBID_CRATES:
        src = ESP32_RS / crate / "src"
        if not src.is_dir():
            failures.append(f"PURE_FORBID_CRATES names a missing crate: {crate}")
            continue
        lib = src / "lib.rs"
        if "#![forbid(unsafe_code)]" not in lib.read_text(encoding="utf-8"):
            failures.append(f"{crate}/src/lib.rs lost `#![forbid(unsafe_code)]`")
        for p in rs_files(src):
            code = strip_comments_and_strings(p.read_text(encoding="utf-8"))
            if _UNSAFE_TOKEN.search(code):
                failures.append(f"{crate}/{rel(p, src)} contains `unsafe`")

    # --- 2. module-level forbid in the unsafe-free firmware modules -------
    for m in FORBID_MODULES:
        p = FW_SRC / m
        if not p.exists():
            failures.append(f"FORBID_MODULES names a missing file: {m}")
            continue
        if "#![forbid(unsafe_code)]" not in p.read_text(encoding="utf-8"):
            failures.append(
                f"esp32tap/src/{m} lost its module-level `#![forbid(unsafe_code)]` "
                "— the containment for that subtree is no longer compiler-enforced"
            )

    # --- 3/4/5/6. firmware allowlists, SAFETY comments, budget ------------
    found_unsafe: set[str] = set()
    prod_lines = 0
    qemu_lines = 0
    for p in rs_files(FW_SRC):
        name = rel(p, FW_SRC)
        raw = p.read_text(encoding="utf-8")
        code = strip_comments_and_strings(raw)

        # allow(unsafe_code) sites
        for m in _ALLOW_UNSAFE.finditer(code):
            if "unsafe_code" not in m.group(1):
                continue
            if name not in ALLOW_SITES:
                failures.append(
                    f"esp32tap/src/{name} carries `allow(unsafe_code)`, which is "
                    "only permitted in " + ", ".join(sorted(ALLOW_SITES))
                )
        if name in ALLOW_SITES:
            granted = set(re.findall(r"#\[allow\(unsafe_code\)\][^\n]*\n\s*mod\s+(\w+)", raw))
            if granted != ALLOW_SITES[name]:
                failures.append(
                    f"esp32tap/src/{name}: `allow(unsafe_code)` is granted to "
                    f"{sorted(granted)}, expected {sorted(ALLOW_SITES[name])}"
                )

        if not _UNSAFE_TOKEN.search(code):
            continue
        found_unsafe.add(name)
        if name not in UNSAFE_ALLOWLIST:
            failures.append(
                f"esp32tap/src/{name} contains `unsafe` but is not in the "
                "allowlist — every unsafe site must be a deliberate, budgeted one"
            )
            continue

        # SAFETY comment on every unsafe block
        lines = raw.split("\n")
        code_lines = code.split("\n")
        for i, cl in enumerate(code_lines):
            if not _UNSAFE_TOKEN.search(cl):
                continue
            if re.search(r"unsafe\s+(impl|fn|trait)\b", cl):
                continue
            window = "\n".join(lines[max(0, i - 12) : i + 1])
            if "// SAFETY:" not in window and "//! SAFETY" not in window:
                failures.append(
                    f"esp32tap/src/{name}:{i + 1}: unsafe block with no "
                    "`// SAFETY:` comment within the preceding 12 lines"
                )

        n = unsafe_line_count(raw)
        if name in PRODUCTION_UNSAFE:
            prod_lines += n
        else:
            qemu_lines += n

    missing = UNSAFE_ALLOWLIST - found_unsafe
    if missing:
        failures.append(
            f"allowlisted files no longer contain `unsafe`: {sorted(missing)} — "
            "shrink the allowlist so it keeps meaning something"
        )

    if prod_lines != PRODUCTION_UNSAFE_LINES:
        failures.append(
            f"production unsafe budget is {prod_lines} lines, documented as "
            f"{PRODUCTION_UNSAFE_LINES}. Update PRODUCTION_UNSAFE_LINES here and "
            "in README.md deliberately, with review."
        )
    if qemu_lines != QEMU_UNSAFE_LINES:
        failures.append(f"qemu-test unsafe budget is {qemu_lines} lines, documented as " f"{QEMU_UNSAFE_LINES}.")
    return failures


def main() -> int:
    failures = check()
    if failures:
        print("check_unsafe_budget: FAIL")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(
        f"check_unsafe_budget: OK — {len(PURE_FORBID_CRATES)} pure crates "
        f"({', '.join(PURE_FORBID_CRATES)}) forbid+unsafe-free; "
        f"{len(FORBID_MODULES)} firmware modules compiler-forbid; unsafe confined to "
        f"{len(PRODUCTION_UNSAFE)} production files ({PRODUCTION_UNSAFE_LINES} lines) "
        f"+ {len(QEMU_UNSAFE)} test-image files ({QEMU_UNSAFE_LINES} lines); "
        "every unsafe block carries a SAFETY comment."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
