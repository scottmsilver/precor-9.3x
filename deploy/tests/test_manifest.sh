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

# --- manifest_resolve_dest: @USER@ and leading ~ resolution ---
[ "$(manifest_resolve_dest '~/treadmill/python/' pi)" = '/home/pi/treadmill/python/' ] \
  || fail "resolve_dest must rewrite leading ~/ to /home/<user>/"
[ "$(manifest_resolve_dest '/etc/systemd/system/' root)" = '/etc/systemd/system/' ] \
  || fail "resolve_dest must leave absolute dest unchanged"
[ "$(manifest_resolve_dest '~/x/@USER@.conf' bob)" = '/home/bob/x/bob.conf' ] \
  || fail "resolve_dest must substitute @USER@ and ~ together"
pass "manifest_resolve_dest resolves @USER@ and ~"
# --- empty / all-comment manifest: 0 rows, rc 0 (consumers must handle 0 rows) ---
printf '# only comments\n\n' > "$TD/empty.txt"
out=$(manifest_rows "$TD/empty.txt"); rc=$?
[ "$rc" -eq 0 ] && [ -z "$out" ] || fail "all-comment manifest must yield rc 0 + empty output"
pass "empty/all-comment manifest => 0 rows, rc 0"
# --- arity guards (set -u contract) ---
( set -u; manifest_rows 2>/dev/null ) && fail "manifest_rows with no arg must fail closed"
( set -u; manifest_resolve_dest x 2>/dev/null ) && fail "manifest_resolve_dest missing user must fail closed"
pass "arity guards fail closed under set -u"

rm -rf "$TD"
echo "ALL TESTS PASSED"
