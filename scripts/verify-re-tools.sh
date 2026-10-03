#!/usr/bin/env bash
#
# Verify the reverse-engineering + Godot/Android toolchain is installed and working.
#
#   bash scripts/verify-re-tools.sh
#
# This does more than check binaries exist. It:
#   1. compiles a C program and runs a real headless Ghidra decompilation on it
#   2. builds a tiny Godot project and exports a real Android APK
# and asserts on the produced artifacts rather than on exit codes.
#
# That matters: analyzeHeadless returns 0 even when a -postScript fails to
# compile, so an exit code alone does not prove anything works.

set -uo pipefail

GHIDRA_DIR="${GHIDRA_INSTALL_DIR:-/opt/ghidra}"
VENV="${RE_TOOLS_VENV:-/opt/re-tools/venv}"
SDK_DIR="${ANDROID_SDK_ROOT:-/opt/android-sdk}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
GHIDRA_SCRIPTS="$REPO_DIR/tools/ghidra-scripts"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

PASS=0
FAIL=0
SKIP=0

ok()    { printf '  \033[1;32mPASS\033[0m  %s\n' "$*"; PASS=$((PASS + 1)); }
bad()   { printf '  \033[1;31mFAIL\033[0m  %s\n' "$*"; FAIL=$((FAIL + 1)); }
skip()  { printf '  \033[1;33mSKIP\033[0m  %s\n' "$*"; SKIP=$((SKIP + 1)); }
section() { printf '\n\033[1;34m%s\033[0m\n' "$*"; }

printf '\033[1mRE + Godot/Android toolchain verification\033[0m\n'

check_cmd() {
  local cmd="$1" args="${2:---version}" line
  # Some tools print a blank line before their banner, so take the first
  # non-empty line rather than literally line 1.
  line="$("$cmd" $args 2>&1 | grep -m1 . || true)"
  if command -v "$cmd" >/dev/null 2>&1; then
    ok "$cmd (${line:-version unavailable})"
  else
    bad "$cmd (not found)"
  fi
}

check_dir() {
  [ -e "$1" ] && ok "$2 ($1)" || bad "$2 (missing: $1)"
}

# ---------------------------------------------------------------------------
section "Baseline RE tools"
# ---------------------------------------------------------------------------
for c in file xxd binwalk upx patchelf gdb-multiarch objdump readelf nm strings llvm-objdump llvm-readobj monodis ffmpeg convert bsdtar cmake ninja; do
  case $c in
    llvm-objdump) check_cmd "$c" "--version" ;;
    ffmpeg) check_cmd "$c" "-version" ;;
    *) check_cmd "$c" ;;
  esac
done
# rizin uses -v; --version prints its help text instead.
check_cmd rizin "-v"

# ---------------------------------------------------------------------------
section "macOS / DMG extraction"
# ---------------------------------------------------------------------------
check_cmd dmg2img "--help"
# The hfsplus package ships hp*-prefixed binaries (hpmount, hpcopy, ...);
# there is no bare `hfsplus` command and hpmount has no --version flag.
if command -v hpmount >/dev/null 2>&1; then
  ok "hpmount ($(command -v hpmount))"
else
  bad "hpmount (not found — needed to read HFS+ DMG images)"
fi

# ---------------------------------------------------------------------------
section "Java + Ghidra"
# ---------------------------------------------------------------------------
check_cmd java "-version"
[ -x "$GHIDRA_DIR/support/analyzeHeadless" ] \
  && ok "analyzeHeadless ($GHIDRA_DIR)" \
  || bad "analyzeHeadless (missing at $GHIDRA_DIR/support/analyzeHeadless)"

# ---------------------------------------------------------------------------
section "Python libraries"
# ---------------------------------------------------------------------------
if [ -x "$VENV/bin/python" ]; then
  if "$VENV/bin/python" - <<'PY' >/dev/null 2>&1
import capstone, pefile, lief, r2pipe, macholib, construct, z3, lzfse  # noqa: F401
PY
  then
    ok "capstone, pefile, lief, r2pipe, macholib, construct, z3, lzfse"
  else
    bad "one or more Python RE libraries failed to import"
  fi
  if "$VENV/bin/python" - <<'PY' >/dev/null 2>&1
import uncompyle6, decompyle3  # noqa: F401
PY
  then
    ok "uncompyle6 + decompyle3"
  else
    skip "uncompyle6/decompyle3 (optional bytecode decompilers)"
  fi
else
  bad "python venv (missing at $VENV)"
fi

# ---------------------------------------------------------------------------
section "Godot 4"
# ---------------------------------------------------------------------------
if command -v godot >/dev/null 2>&1; then
  ok "godot ($("$(command -v godot)" --headless --version 2>/dev/null | tail -1))"
  TEMPLATES="$HOME/.local/share/godot/export_templates"
  if ls -d "$TEMPLATES"/*/android_release.apk >/dev/null 2>&1; then
    ok "export templates ($(ls -d "$TEMPLATES"/*/ | head -1))"
  else
    bad "export templates (no Android templates under $TEMPLATES)"
  fi
else
  bad "godot (not found)"
fi

# ---------------------------------------------------------------------------
section "Android SDK"
# ---------------------------------------------------------------------------
for c in adb apksigner; do
  if [ -x "$SDK_DIR/platform-tools/$c" ] || [ -x "$SDK_DIR/build-tools"/*/"$c" ]; then
    ok "$c"
  else
    bad "$c (not found under $SDK_DIR)"
  fi
done
check_dir "$SDK_DIR/cmdline-tools/latest/bin/sdkmanager" "sdkmanager"
check_dir "$SDK_DIR/platforms" "platforms"
check_dir "$SDK_DIR/ndk" "ndk"
[ -f "$HOME/.android/debug.keystore" ] \
  && ok "debug keystore" \
  || bad "debug keystore (missing at $HOME/.android/debug.keystore)"

check_cmd dotnet "--version"

# ---------------------------------------------------------------------------
section "End-to-end Ghidra decompilation"
# ---------------------------------------------------------------------------
if ! command -v gcc >/dev/null 2>&1; then
  bad "gcc (needed to build the decompilation test binary)"
elif [ ! -x "$GHIDRA_DIR/support/analyzeHeadless" ]; then
  skip "decompilation test: analyzeHeadless unavailable"
else
  cat > "$WORK/target.c" <<'EOF'
#include <string.h>
static const char SECRET[] = "verify_marker_string";
int xor_cipher(const unsigned char *in, int len, unsigned char key) {
    unsigned char out[256]; int i;
    for (i = 0; i < len && i < 256; i++) out[i] = in[i] ^ key;
    return len;
}
int check_password(const char *candidate) {
    int ok = 0, i;
    if (strlen(candidate) != sizeof(SECRET) - 1) return 0;
    for (i = 0; i < (int)sizeof(SECRET) - 1; i++)
        if ((unsigned char)candidate[i] ^ 0x5a == (unsigned char)SECRET[i]) ok++;
    return ok == (int)sizeof(SECRET) - 1;
}
int main(int argc, char **argv) {
    return argc > 1 && check_password(argv[1]) ? 0 : 1;
}
EOF

  if gcc -O0 -g -o "$WORK/target" "$WORK/target.c" 2>"$WORK/gcc.err"; then
    ok "compiled test binary"
  else
    bad "failed to compile test binary: $(head -3 "$WORK/gcc.err")"
  fi

  mkdir -p "$WORK/proj"
  # analyzeHeadless can exit 0 even when the post-script fails, so its exit code
  # is not the signal here; we judge success by the decompiled output below.
  timeout 300 "$GHIDRA_DIR/support/analyzeHeadless" \
    "$WORK/proj" verify -import "$WORK/target" \
    -scriptPath "$GHIDRA_SCRIPTS" \
    -postScript DecompileAll.java "$WORK/out.c" nolibs >"$WORK/ghidra.log" 2>&1

  if [ -s "$WORK/out.c" ]; then
    ok "DecompileAll.java produced output"
    for sym in xor_cipher check_password main; do
      grep -q "$sym" "$WORK/out.c" && ok "recovered function: $sym" \
                                     || bad "missing function: $sym"
    done
    grep -q "verify_marker_string" "$WORK/out.c" \
      && ok "recovered inlined string" \
      || bad "string literal not recovered"
  else
    bad "no decompiled output produced"
    grep -iE 'error|SCRIPT ERROR' "$WORK/ghidra.log" | head -5
  fi
fi

# ---------------------------------------------------------------------------
section "End-to-end Godot Android export"
# ---------------------------------------------------------------------------
if ! command -v godot >/dev/null 2>&1; then
  skip "android export: godot unavailable"
elif [ ! -d "$SDK_DIR/cmdline-tools" ]; then
  skip "android export: SDK unavailable"
else
  PJ="$WORK/godot-proj"
  mkdir -p "$PJ/build"
  cat > "$PJ/project.godot" <<EOF
config_version=5

[application]
config/name="VerifyExport"
run/main_scene="res://main.tscn"
config/features=PackedStringArray("4.7")

[rendering]
textures/vram_compression/import_etc2_astc=true
EOF
  cat > "$PJ/main.tscn" <<'EOF'
[gd_scene load_steps=2 format=3]
[ext_resource type="Script" path="res://main.gd" id="1"]
[node name="Main" type="Node2D"]
script = ExtResource("1")
EOF
  echo 'extends Node2D
func _ready() -> void:
	print("verify")' > "$PJ/main.gd"
  cat > "$PJ/export_presets.cfg" <<'EOF'
[preset.0]
name="Android"
platform="Android"
runnable=true
export_filter="all_resources"
export_path="build/game.apk"

[preset.0.options]
architectures/armeabi-v7a=false
architectures/arm64-v8a=true
architectures/x86=false
architectures/x86_64=true
package/unique_name="com.example.verifyexport"
EOF

  godot --headless --path "$PJ" --import >"$WORK/import.log" 2>&1
  timeout 400 godot --headless --path "$PJ" --export-debug "Android" build/game.apk \
    >"$WORK/export.log" 2>&1
  if [ -s "$PJ/build/game.apk" ]; then
    ok "exported APK ($(du -h "$PJ/build/game.apk" | cut -f1))"
    AAPT="$(ls "$SDK_DIR"/build-tools/*/aapt2 2>/dev/null | tail -1)"
    APKSIGNER="$(ls "$SDK_DIR"/build-tools/*/apksigner 2>/dev/null | tail -1)"
    "$APKSIGNER" verify "$PJ/build/game.apk" >/dev/null 2>&1 \
      && ok "APK signature verifies" || bad "APK signature does not verify"
    "$AAPT" dump badging "$PJ/build/game.apk" 2>/dev/null | grep -q "arm64-v8a" \
      && ok "APK targets arm64-v8a" || bad "APK missing arm64-v8a native code"
  else
    bad "no APK produced"
    grep -iE 'error|configuration errors' "$WORK/export.log" | head -5
  fi
fi

# ---------------------------------------------------------------------------
printf '\n\033[1mResult: %d passed, %d failed, %d skipped\033[0m\n' "$PASS" "$FAIL" "$SKIP"
[ "$FAIL" -eq 0 ] || exit 1