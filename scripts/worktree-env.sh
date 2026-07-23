#!/bin/bash
# worktree-env.sh — Source worktree.env if it exists, otherwise export legacy defaults.
#
# All dev scripts should source this file instead of worktree.env directly.
# This provides backward compatibility with the main repo.
#
# Usage: source scripts/worktree-env.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [ -f "$PROJECT_ROOT/worktree.env" ]; then
    source "$PROJECT_ROOT/worktree.env"
else
    # Legacy defaults for main repo (no worktree.env)
    export TREADMILL_SERVER_PORT=8000
    export EMU_DISPLAY=:98
    export EMU_ADB_PORT=5554
    export EMU_VNC_PORT=5900
    export EMU_NOVNC_PORT=6080
fi

export PROJECT_ROOT
