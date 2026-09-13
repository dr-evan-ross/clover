#!/usr/bin/env bash
# CLOVER demo launcher. Double-click "CLOVER Demo.desktop", or run this from a terminal.
# If started without a terminal (file manager double-click), re-open in one so messages are visible.
cd "$(dirname "$(readlink -f "$0")")" || exit 1
if [ ! -t 1 ] && [ -z "$CLOVER_IN_TERMINAL" ]; then
  export CLOVER_IN_TERMINAL=1
  for term in konsole gnome-terminal ptyxis kgx xfce4-terminal x-terminal-emulator xterm; do
    if command -v "$term" >/dev/null 2>&1; then
      case "$term" in
        konsole)         exec konsole --hold -e bash -c "'$PWD/start_clover.sh' $*" ;;
        gnome-terminal)  exec gnome-terminal -- bash -c "'$PWD/start_clover.sh' $*; read -p 'Press Enter to close'" ;;
        ptyxis|kgx)      exec "$term" -- bash -c "'$PWD/start_clover.sh' $*; read -p 'Press Enter to close'" ;;
        *)               exec "$term" -e bash -c "'$PWD/start_clover.sh' $*; read -p 'Press Enter to close'" ;;
      esac
    fi
  done
  # no terminal found: run headless, log to file
  exec python3 start_clover.py "$@" >> clover_launcher.log 2>&1
fi
echo "CLOVER launcher · $(date)  (log: $PWD/clover_launcher.log)"
python3 start_clover.py "$@" 2>&1 | grep --line-buffered -v "RuntimeWarning\|frozen site\|^\[clc" | tee -a clover_launcher.log
