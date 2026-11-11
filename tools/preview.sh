#!/usr/bin/env bash
# One command to see Lumen running. Dev-only convenience; the product does not
# depend on it.
#
#   ./tools/preview.sh              # demo mode, synthetic data, port 8765
#   ./tools/preview.sh 8800         # pick a port
#   LFMC_TOKEN=... ./tools/preview.sh real   # real catalogue, token required
#
# Demo mode disables ALL authentication. It is for previewing the UI with
# synthetic sample footage only. Never point it at a real drive and never leave
# it running on a network you do not control.

set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${1:-8765}"
MODE="${2:-demo}"
case "$PORT" in
  ''|*[!0-9]*) MODE="$PORT"; PORT=8765 ;;
esac

PY_BIN="${PYTHON:-python3}"
if ! command -v "$PY_BIN" >/dev/null 2>&1; then
  echo "python3 is required but was not found on PATH." >&2
  exit 1
fi
"$PY_BIN" - <<'EOF' || { echo "Python 3.10+ is required (checked 3.10 syntax features)." >&2; exit 1; }
import sys
sys.exit(0 if sys.version_info >= (3, 10) else 1)
EOF

# First run only: create the synthetic sample drive and index it.
if [ ! -f data/catalogue.db ]; then
  echo "[preview] no catalogue yet — seeding synthetic sample drive"
  "$PY_BIN" -u -m lfmc.cli seed-demo --dir data/sample_drive
  "$PY_BIN" -u -m lfmc.cli seed-reviews
fi

echo
echo "  Lumen will be at:  http://127.0.0.1:${PORT}/"
if [ "$MODE" = "real" ]; then
  echo "  Token:  data/catalogue.token  (also printed below)"
else
  echo "  Demo mode: no token required, synthetic data only."
fi
echo

if [ "$MODE" = "real" ]; then
  exec "$PY_BIN" -u -m lfmc.cli serve --host 0.0.0.0 --port "$PORT"
fi
exec "$PY_BIN" -u -m lfmc.cli serve --host 0.0.0.0 --port "$PORT" --demo
