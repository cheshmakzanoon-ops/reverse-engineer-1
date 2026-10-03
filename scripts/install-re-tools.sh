#!/usr/bin/env bash
#
# One-shot reverse-engineering toolchain installer.
#
# Installs Ghidra (headless decompiler), rizin, RE-oriented binutils extras and
# the Python RE libraries needed to continue work in this repository.
#
#   bash scripts/install-re-tools.sh
#
# Requires: Debian/Ubuntu, x86_64, network access, ~2 GB free disk, root or sudo.
# The script is idempotent: re-running it skips work that is already done.
#
# NOTE: this is a DEVELOPER machine installer. It uses apt, which is fine here.
# It is NOT part of any production/hosting build command.

set -euo pipefail

# ---------------------------------------------------------------------------
# Pinned versions. Bump these to upgrade the toolchain.
# ---------------------------------------------------------------------------
GHIDRA_VERSION="${GHIDRA_VERSION:-12.1.4}"
GHIDRA_BUILD="${GHIDRA_BUILD:-20260921}"
RIZIN_VERSION="${RIZIN_VERSION:-0.9.1}"

GHIDRA_DIR="/opt/ghidra"
RIZIN_DIR="/opt/rizin"
TOOLS_DIR="/opt/re-tools"
VENV="$TOOLS_DIR/venv"

PYTHON_RE_PACKAGES=(capstone pefile lief r2pipe pwntools)

log()  { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[error]\033[0m %s\n' "$*" >&2; exit 1; }

SUDO=""
if [ "$(id -u)" -ne 0 ]; then
  command -v sudo >/dev/null 2>&1 || die "need root or sudo to install system packages"
  SUDO="sudo"
fi

if [ "$(uname -m)" != "x86_64" ]; then
  warn "This script was validated on x86_64 only; proceeding on $(uname -m)."
fi

# ---------------------------------------------------------------------------
# 1. System packages
# ---------------------------------------------------------------------------
log "Installing system packages via apt"
export DEBIAN_FRONTEND=noninteractive
$SUDO apt-get update -qq
$SUDO apt-get install -y -qq \
  openjdk-21-jdk-headless \
  file xxd unzip p7zip-full \
  binutils binutils-multiarch \
  patchelf upx-ucl \
  gdb gdb-multiarch \
  qemu-user-static \
  ltrace strace \
  python3 python3-venv python3-pip \
  curl ca-certificates

# ---------------------------------------------------------------------------
# 2. Ghidra
# ---------------------------------------------------------------------------
if [ -x "$GHIDRA_DIR/support/analyzeHeadless" ]; then
  log "Ghidra already present at $GHIDRA_DIR (version $(cat "$GHIDRA_DIR/Ghidra/application.properties" 2>/dev/null | sed -n 's/^application.version=//p'))"
else
  log "Downloading Ghidra ${GHIDRA_VERSION}"
  ZIP="ghidra_${GHIDRA_VERSION}_PUBLIC_${GHIDRA_BUILD}.zip"
  URL="https://github.com/NationalSecurityAgency/ghidra/releases/download/Ghidra_${GHIDRA_VERSION}_build/${ZIP}"
  tmp="$(mktemp -d)"
  # -C - makes the download resumable if a previous attempt was interrupted.
  curl -fsSL -C - -o "$tmp/$ZIP" "$URL" || die "failed to download Ghidra from $URL"
  unzip -q -t "$tmp/$ZIP" >/dev/null || die "Ghidra zip is corrupt"
  unzip -q -o "$tmp/$ZIP" -d /opt
  extracted="/opt/ghidra_${GHIDRA_VERSION}_PUBLIC"
  [ -d "$extracted" ] || die "expected $extracted after unzip"
  ln -sfn "$extracted" "$GHIDRA_DIR"
  rm -rf "$tmp"
  log "Ghidra installed -> $GHIDRA_DIR"
fi

# ---------------------------------------------------------------------------
# 3. rizin (static build; not packaged for Debian/Ubuntu)
# ---------------------------------------------------------------------------
if [ -x "$RIZIN_DIR/bin/rizin" ]; then
  log "rizin already present at $RIZIN_DIR"
else
  log "Downloading rizin ${RIZIN_VERSION}"
  tmp="$(mktemp -d)"
  URL="https://github.com/rizinorg/rizin/releases/download/v${RIZIN_VERSION}/rizin-v${RIZIN_VERSION}-static-x86_64.tar.xz"
  curl -fsSL -o "$tmp/rizin.tar.xz" "$URL" || die "failed to download rizin from $URL"
  mkdir -p "$RIZIN_DIR"
  tar -xJf "$tmp/rizin.tar.xz" -C "$RIZIN_DIR"
  rm -rf "$tmp"
  for b in "$RIZIN_DIR"/bin/*; do ln -sfn "$b" "/usr/local/bin/$(basename "$b")"; done
  log "rizin installed -> $RIZIN_DIR/bin (symlinked into /usr/local/bin)"
fi

# ---------------------------------------------------------------------------
# 4. Python RE libraries (isolated venv, never touches system python)
# ---------------------------------------------------------------------------
if [ ! -x "$VENV/bin/python" ]; then
  log "Creating Python venv at $VENV"
  mkdir -p "$TOOLS_DIR"
  python3 -m venv "$VENV"
fi

log "Installing Python RE libraries: ${PYTHON_RE_PACKAGES[*]}"
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q "${PYTHON_RE_PACKAGES[@]}"

# ---------------------------------------------------------------------------
# 5. Profile so a login shell can find Ghidra and the venv
# ---------------------------------------------------------------------------
log "Adding shell profile entries"
PROFILE="/etc/profile.d/re-tools.sh"
if [ ! -f "$PROFILE" ]; then
  cat > "$PROFILE" <<EOF
export GHIDRA_INSTALL_DIR=$GHIDRA_DIR
export RE_TOOLS_VENV=$VENV
export PATH="\$PATH:$VENV/bin"
EOF
  chmod +x "$PROFILE"
  log "Wrote $PROFILE (re-login or 'source $PROFILE' to pick it up)"
else
  log "Shell profile already present"
fi

log "Done. Next: bash scripts/verify-re-tools.sh"