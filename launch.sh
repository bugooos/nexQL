#!/usr/bin/env bash
# nexql/launch.sh
# ────────────────
# Launch the Piql Workbench (Electron IDE + Python runtime).
#
# Usage:
#   ./launch.sh            — start the full workbench
#   ./launch.sh --runtime  — start the runtime HTTP server only (port 7890)
#   ./launch.sh --test     — run the full test suite
#   ./launch.sh --parse "? user { id }"

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PYTHON:-python3}"

# ── Dependency check ──────────────────────────────────────────────────────────

check_python() {
    if ! command -v "$PYTHON" &>/dev/null; then
        echo "ERROR: python3 not found. Please install Python 3.10+." >&2
        exit 1
    fi
    PY_VERSION=$("$PYTHON" -c "import sys; print(sys.version_info[:2])")
    if "$PYTHON" -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)"; then
        echo "✓ Python $("$PYTHON" --version)"
    else
        echo "ERROR: Python 3.10+ required." >&2
        exit 1
    fi
}

check_node() {
    if ! command -v node &>/dev/null; then
        echo "ERROR: Node.js not found. Please install Node.js 18+." >&2
        exit 1
    fi
    echo "✓ Node $( node --version)"
}

check_electron() {
    if [ ! -f "$SCRIPT_DIR/node_modules/.bin/electron" ]; then
        echo "Installing npm dependencies…"
        cd "$SCRIPT_DIR" && npm install --silent
    fi
    echo "✓ Electron $(./node_modules/.bin/electron --version 2>/dev/null || echo '?')"
}

# ── Modes ─────────────────────────────────────────────────────────────────────

MODE="${1:-}"

if [ "$MODE" = "--runtime" ]; then
    check_python
    echo "Starting Piql runtime on http://127.0.0.1:7890 …"
    cd "$SCRIPT_DIR"
    exec "$PYTHON" -m nexql.runtime.server_entry --port 7890

elif [ "$MODE" = "--test" ]; then
    check_python
    cd "$SCRIPT_DIR"
    exec "$PYTHON" -m pytest nexql/tests/ -v "${@:2}"

elif [ "$MODE" = "--parse" ] || [ "$MODE" = "--tokens" ] || [ "$MODE" = "--validate" ]; then
    check_python
    cd "$SCRIPT_DIR"
    exec "$PYTHON" -m nexql.runtime.server_entry "$@"

else
    check_python
    check_node
    check_electron

    echo ""
    echo "  ╔════════════════════════════════╗"
    echo "  ║   Piql Workbench v0.2.0        ║"
    echo "  ╚════════════════════════════════╝"
    echo ""

    cd "$SCRIPT_DIR"
    exec ./node_modules/.bin/electron . "$@"
fi
