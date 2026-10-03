#!/usr/bin/env bash
#
# Verify the reverse-engineering toolchain is installed and working.
#
#   bash scripts/verify-re-tools.sh
#
# This does more than check binaries exist: it compiles a small C program and
# runs a real headless Ghidra decompilation against it, then asserts that the
# expected functions and the secret string were actually recovered.
#
# That last part matters: analyzeHeadless exits 0 even when a -postScript fails
# to compile, so an exit code alone does not prove Ghidra works. We assert on the
# decompiled output instead.

set -uo pipefail

GHIDRA_DIR="${GHIDRA_INSTALL_DIR:-/opt/ghidra}"
VENV="${RE_TOOLS_VENV:-/opt/re-tools/venv}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
GHIDRA_SCRIPTS="$REPO_DIR/tools/ghidra-scripts"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

PASS=0
FAIL=0

ok()   { printf '  \033[1;32mPASS\033[0m  %s\n' "$*"; PASS=$((PASS + 1)); }
bad()  { printf '  \033[1;31mFAIL\033[0m  %s\n' "$*"; FAIL=$((FAIL + 1)); }
section() { printf '\n\033[1;34m%s\033[0m\n' "$*"; }

printf '\033[1mRE toolchain verification\033[0m\n'

# ---------------------------------------------------------------------------
section "Command-line tools"
# ---------------------------------------------------------------------------
check_cmd() {
  local cmd="$1" args="${2:---version}"
  # Some tools (binwalk) print a blank line before their version banner,
  # so take the first non-empty line rather than literally line 1.
  local line
  line="$("$cmd" $args 2>&1 | grep -m1 . || true)"
  if command -v "$cmd" >/dev/null 2>&1; then
    ok "$cmd (${line:-version unavailable})"
  else
    bad "$cmd (not found)"
  fi
}

check_cmd file
check_cmd xxd
check_cmd binwalk "--help"
check_cmd upx "--version"
check_cmd patchelf "--version"
check_cmd gdb-multiarch "--version"
check_cmd qemu-aarch64-static "--version"
check_cmd objdump "--version"
check_cmd readelf "--version"
check_cmd nm "--version"
check_cmd strings "--version"
check_cmd rizin "-v"

# ---------------------------------------------------------------------------
section "Java + Ghidra"
# ---------------------------------------------------------------------------
if command -v java >/dev/null 2>&1; then
  ok "java ($(java -version 2>&1 | head -1))"
else
  bad "java (not found)"
fi

if [ -x "$GHIDRA_DIR/support/analyzeHeadless" ]; then
  ok "analyzeHeadless ($GHIDRA_DIR)"
else
  bad "analyzeHeadless (missing at $GHIDRA_DIR/support/analyzeHeadless)"
fi

# ---------------------------------------------------------------------------
section "Python libraries"
# ---------------------------------------------------------------------------
if [ -x "$VENV/bin/python" ]; then
  if "$VENV/bin/python" - <<'PY' >/dev/null 2>&1
import capstone, pefile, lief, r2pipe  # noqa: F401
PY
  then
    ok "python RE libs in $VENV (capstone, pefile, lief, r2pipe)"
  else
    bad "python RE libs in $VENV (import failed)"
  fi
else
  bad "python venv (missing at $VENV)"
fi

# ---------------------------------------------------------------------------
section "End-to-end Ghidra decompilation"
# ---------------------------------------------------------------------------
if ! command -v gcc >/dev/null 2>&1; then
  bad "gcc (needed to build the decompilation test binary)"
elif [ ! -x "$GHIDRA_DIR/support/analyzeHeadless" ]; then
  bad "skipped: analyzeHeadless unavailable"
else
  cat > "$WORK/target.c" <<'EOF'
#include <string.h>
#include <stdio.h>

static const char SECRET[] = "verify_marker_string";

int xor_cipher(const unsigned char *in, int len, unsigned char key) {
    unsigned char out[256];
    int i;
    for (i = 0; i < len && i < 256; i++) {
        out[i] = in[i] ^ key;
    }
    return len;
}

int check_password(const char *candidate) {
    int ok = 0;
    int i;
    if (strlen(candidate) != sizeof(SECRET) - 1) {
        return 0;
    }
    for (i = 0; i < (int)sizeof(SECRET) - 1; i++) {
        if ((unsigned char)candidate[i] ^ 0x5a == (unsigned char)SECRET[i]) {
            ok++;
        }
    }
    return ok == (int)sizeof(SECRET) - 1;
}

int main(int argc, char **argv) {
    if (argc > 1 && check_password(argv[1])) {
        printf("accepted\n");
        return 0;
    }
    printf("usage\n");
    return 1;
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
    "$WORK/proj" verify \
    -import "$WORK/target" \
    -scriptPath "$GHIDRA_SCRIPTS" \
    -postScript DecompileAll.java "$WORK/out.c" nolibs \
    >"$WORK/ghidra.log" 2>&1

  if [ -s "$WORK/out.c" ]; then
    ok "DecompileAll.java produced $WORK/out.c"
    for sym in xor_cipher check_password main; do
      if grep -q "$sym" "$WORK/out.c"; then
        ok "recovered function: $sym"
      else
        bad "missing function: $sym"
      fi
    done
    if grep -q "verify_marker_string" "$WORK/out.c"; then
      ok "recovered inlined string: verify_marker_string"
    else
      bad "string literal verify_marker_string not recovered"
    fi
    if grep -q "0x5a\|'Z'" "$WORK/out.c"; then
      ok "recovered XOR key constant (0x5a)"
    else
      bad "XOR key constant 0x5a not visible in decompiled output"
    fi
  else
    bad "no decompiled output produced"
    grep -iE 'error|SCRIPT ERROR' "$WORK/ghidra.log" | head -5
  fi
fi

# ---------------------------------------------------------------------------
printf '\n\033[1mResult: %d passed, %d failed\033[0m\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ] || exit 1