#!/usr/bin/env bash
# Run real UI events in a graphical window; headless has no window input callback.
# Dummy audio is explicit: these checks do not prove audio playback on a device.
set -euo pipefail
cd "$(dirname "$0")/.."
godot="${GODOT:-godot}"
logs="${1:-verification}"
mkdir -p "$logs"
logs="$(cd "$logs" && pwd)"
export GODOT_SILENCE_ROOT_WARNING=1
for test in port/tests/test_*.gd; do
  name="$(basename "$test" .gd)"
  engine=("$godot" --headless)
  if [[ "$name" == test_session_flow ]]; then
    if ! command -v xvfb-run >/dev/null; then
      echo 'Graphical UI regression requires xvfb-run and xauth.' >&2
      exit 1
    fi
    engine=(xvfb-run -a "$godot" --audio-driver Dummy)
    export KART_SCREENSHOTS="$logs/ui-screens"
  fi
  python3 scripts/checked_process.py --timeout 180 --require 'PASS:' \
    --log "$logs/$name.log" -- "${engine[@]}" \
    --fixed-fps 60 --path port --script "res://tests/$name.gd"
done
