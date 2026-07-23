#!/bin/bash
# dev.sh — Launch the local dev server (python/server.py) with worktree isolation.
#
# Usage:
#   ./scripts/dev.sh                    # Connect to real Pi
#   TREADMILL_MOCK=1 ./scripts/dev.sh   # Mock mode, no Pi needed
#
# Ctrl-C kills the server.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/worktree-env.sh"

echo "=== Dev Server ==="
echo "  Server: http://localhost:$TREADMILL_SERVER_PORT"
if [ "${TREADMILL_MOCK:-}" = "1" ]; then
    echo "  Mode:   MOCK (no Pi connection)"
fi
echo ""

# Export for child processes
export TREADMILL_SERVER_PORT

# Kill all children on exit
trap 'kill 0 2>/dev/null; exit' EXIT INT TERM

# Start server.py
(cd "$PROJECT_ROOT" && python3 python/server.py) &

wait
