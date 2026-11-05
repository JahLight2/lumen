#!/bin/bash
# Lumen — local host. macOS/Linux: double-click (macOS) or run ./start-lumen.command
cd "$(dirname "$0")" || exit 1
PY=python3
command -v $PY >/dev/null 2>&1 || { echo "Python 3.10+ is required. Install from python.org, then run this again."; read -r; exit 1; }
$PY -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' || {
  echo "Python 3.10 or newer is required (found $($PY -V 2>&1))."; read -r; exit 1; }
echo "Lumen — local-drive-first media collaboration"
echo "  working folder: $(pwd)"
if [ ! -f data/catalogue.db ]; then
  echo "  first run: creating a synthetic sample drive so you have something to click"
  $PY -m lfmc.cli seed-demo --dir data/sample_drive || exit 1
  echo "  adding sample comments, selects and a client review link"
  $PY -m lfmc.cli seed-reviews || true
fi
echo "  opening http://127.0.0.1:8765  (Ctrl+C here to stop)"
( sleep 3; (open http://127.0.0.1:8765/ 2>/dev/null || xdg-open http://127.0.0.1:8765/ 2>/dev/null) ) &
exec $PY -u -m lfmc.cli serve --host 127.0.0.1 --port 8765 --demo
