#!/usr/bin/env bash
#
# Report free disk space and what can be reclaimed in this workspace.
#
#   bash scripts/check-disk.sh            # threshold 5 GB
#   bash scripts/check-disk.sh 10         # require 10 GB free
#
# Exits non-zero when free space is under the threshold, so it can gate a long
# step (Ghidra analysis, AssetRipper, an Android export) instead of failing
# halfway through.

set -uo pipefail

MIN_FREE="${1:-${MIN_FREE_GB:-5}}"
TARGET="${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

red()   { printf '\033[1;31m%s\033[0m\n' "$*"; }
green() { printf '\033[1;32m%s\033[0m\n' "$*"; }
yellow(){ printf '\033[1;33m%s\033[0m\n' "$*"; }
bold()  { printf '\033[1m%s\033[0m\n' "$*"; }

avail_gb() { df -BG --output=avail "$1" 2>/dev/null | tail -1 | tr -dc '0-9'; }

TOTAL_AVAIL="$(avail_gb /)"
bold "Free space on /: ${TOTAL_AVAIL} GB (threshold ${MIN_FREE} GB)"

# Reclaimable items. The Godot/Android toolchain is the single biggest win and
# is reinstallable with scripts/install-re-tools.sh, so it is safe to drop.
echo
bold "Reclaimable:"
found=0
report() {
  local path="$1" note="$2" size
  [ -e "$path" ] || return 0
  size="$(du -sh "$path" 2>/dev/null | cut -f1)"
  printf '  %-34s %6s  %s\n' "$path" "$size" "$note"
  found=1
}
report /opt/android-sdk             "Android SDK — restore: install-re-tools.sh"
report /opt/godot                   "Godot editor — restore: install-re-tools.sh"
report "$HOME/.local/share/godot"   "Godot export templates (~2 GB)"
report "$TARGET/.git/lfs"           "Git LFS cache (prunable, not deletable)"
report "$TARGET/work"               "scratch: DMG/extracted app (gitignored)"

if [ "$found" -eq 0 ]; then
  echo "  (nothing reclaimable — the big items are already removed)"
fi

echo
if [ "$TOTAL_AVAIL" -ge "$MIN_FREE" ]; then
  green "OK: ${TOTAL_AVAIL} GB free >= ${MIN_FREE} GB required"
  exit 0
elif [ "$TOTAL_AVAIL" -ge 2 ]; then
  yellow "LOW: ${TOTAL_AVAIL} GB free < ${MIN_FREE} GB required — reclaim space above"
  exit 1
else
  red "CRITICAL: only ${TOTAL_AVAIL} GB free. Extraction and Ghidra analysis will fail."
  exit 1
fi