#!/usr/bin/env bash
#
# Decompile the recovered IL2CPP stub assemblies into a readable C# source tree.
#
# IL2CPP ships no C# at all: every managed method is compiled to ARM64 in
# GameAssembly.dylib. Il2CppDumper recovers the *metadata* into stub assemblies
# under work/analysis/il2cpp/DummyDll, and ILSpy turns those back into real .cs
# files -- class definitions, inheritance, field names and offsets, method
# signatures, enums, attributes, and the original developers' [Tooltip]/[Header]
# comments. Method bodies stay empty; those are the native code, and getting
# them is a Ghidra job (see tools/ghidra-scripts/DecompileIl2Cpp.java).
#
# Usage:
#   bash tools/il2cpp_to_csharp.sh [DummyDll-dir] [output-dir]
#
# Defaults:
#   DummyDll-dir  work/analysis/il2cpp/DummyDll
#   output-dir    work/analysis/csharp
#
# Re-runs are incremental-ish and overwrite; delete the output dir first if you
# want a guaranteed-clean tree.

set -euo pipefail

DUMMY_DIR="${1:-work/analysis/il2cpp/DummyDll}"
OUT_DIR="${2:-work/analysis/csharp}"

# --- environment -----------------------------------------------------------
# DOTNET_ROOT must point at the SDK: the tool installs an apphost that looks
# for libhostfxr.so relative to DOTNET_ROOT, and without it you get
# "Failed to resolve libhostfxr.so" rather than a useful error.
DOTNET_ROOT="${DOTNET_ROOT:-/opt/dotnet}"
export DOTNET_ROOT
export PATH="$DOTNET_ROOT:$HOME/.dotnet/tools:$PATH"
# The tool targets net6.0; only a .NET 8 runtime is installed here, so allow
# the major-version roll-forward or the app refuses to start.
export DOTNET_ROLL_FORWARD="${DOTNET_ROLL_FORWARD:-LatestMajor}"

ILSPYCMD="$HOME/.dotnet/tools/ilspycmd"

# --- tool ------------------------------------------------------------------
# Pinned deliberately. The current latest (11.x) requires a newer .NET than
# this box has and fails to install; 8.2.0.7535 targets net6.0 and runs fine
# on the installed 8.0 runtime. Bump ILSPY_VERSION together with DOTNET_CHANNEL
# in scripts/install-re-tools.sh if you move the SDK.
ILSPY_VERSION="8.2.0.7535"

if ! command -v dotnet >/dev/null 2>&1; then
  echo "error: dotnet not found. Install it with: bash scripts/install-re-tools.sh" >&2
  exit 1
fi

if [ ! -x "$ILSPYCMD" ]; then
  echo "installing ilspycmd $ILSPY_VERSION ..."
  dotnet tool install -g ilspycmd --version "$ILSPY_VERSION" >&2
fi

if [ ! -d "$DUMMY_DIR" ]; then
  cat >&2 <<EOF
error: no such directory: $DUMMY_DIR

Run Il2CppDumper first (see docs/RE-FINDINGS.md "Reproducing this"):
  DOTNET_ROLL_FORWARD=LatestMajor dotnet work/tools/Il2CppDumper/Il2CppDumper.dll \\
    work/analysis/slices/GameAssembly.arm64.dylib \\
    "<app>/Contents/Resources/Data/il2cpp_data/Metadata/global-metadata.dat" \\
    work/analysis/il2cpp
EOF
  exit 1
fi

# -p writes a whole project tree; passing the directory (with a trailing glob
# resolved by the shell) is not supported, so expand the DLL list explicitly.
shopt -s nullglob
DLLS=("$DUMMY_DIR"/*.dll)
shopt -u nullglob
if [ ${#DLLS[@]} -eq 0 ]; then
  echo "error: no .dll files in $DUMMY_DIR" >&2
  exit 1
fi

mkdir -p "$OUT_DIR"
echo "decompiling ${#DLLS[@]} assemblies -> $OUT_DIR"
"$ILSPYCMD" -p -o "$OUT_DIR" "${DLLS[@]}" || true   # ilspycmd exits 0 on partial failures

CS_COUNT=$(find "$OUT_DIR" -name '*.cs' | wc -l)
LINE_COUNT=$(find "$OUT_DIR" -name '*.cs' -exec cat {} + | wc -l)

if [ "$CS_COUNT" -eq 0 ]; then
  echo "error: produced no .cs files -- ilspycmd failed silently" >&2
  exit 1
fi

echo "done: $CS_COUNT .cs files, $LINE_COUNT lines"
du -sh "$OUT_DIR"