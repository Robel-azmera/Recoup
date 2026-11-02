#!/usr/bin/env bash
# Runs Firestore emulator + API + web together (see Procfile).
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8   # honcho crashes on Vite's unicode output under cp1252
exec uvx honcho start "$@"
