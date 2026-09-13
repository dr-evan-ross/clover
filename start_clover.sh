#!/usr/bin/env bash
# Double-clickable launcher (Linux/macOS). Windows: double-click start_clover.py instead.
cd "$(dirname "$0")"
exec python3 start_clover.py "$@"
