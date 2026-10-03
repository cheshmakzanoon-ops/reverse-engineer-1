#!/usr/bin/env bash
#
# One-shot reverse-engineering + Godot/Android toolchain installer.
#
# Covers the whole pipeline this repo works on:
#   macOS .dmg  ->  extract  ->  Mach-O / asset analysis  ->  Godot 4  ->  Android APK
#
#   bash scripts/install-re-tools.sh
#
# Requires: Debian/Ubuntu, x86_64, network access, ~12 GB free disk, root/sudo.
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
GODOT_VERSION="${GODOT_VERSION:-4.7.2-stable}"
ANDROID_CMDLINE_TOOLS="${ANDROID_CMDLINE_TOOLS:-13114758}"
ANDROID_PLATFORM="${ANDROID_PLATFORM:-android-35}"
ANDROID_BUILD_TOOLS="${ANDROID_BUILD_TOOLS:-35.0.0}"
ANDROID_NDK="${ANDROID_NDK:-29.0.14206865}"
ANDROID_CMAKE="${ANDROID_CMAKE:-3.22.1}"
DOTNET_CHANNEL="${DOTNET_CHANNEL:-8.0}"

GHIDRA_DIR="/opt/ghidra"
RIZIN_DIR="/opt/rizin"
TOOLS_DIR="/opt/re-tools"
VENV="$TOOLS_DIR/venv"
GODOT_DIR="/opt/godot"
GODOT_BIN="$GODOT_DIR/Godot_v${GODOT_VERSION}_linux.x86_64"
SDK_DIR="/opt/android-sdk"
DOTNET_DIR="/opt/dotnet"
KEYSTORE="$HOME/.android/debug.keystore"

PYTHON_RE_PACKAGES=(
  capstone pefile lief r2pipe pwntools   # generic RE
  macholib construct                     # Mach-O + binary parsing
  z3-solver uncompyle6 decompyle3        # constraint solving, Python bytecode
)

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
  file xxd unzip p7zip-full libarchive-tools \
  binutils binutils-multiarch llvm \
  patchelf upx-ucl \
  gdb gdb-multiarch \
  qemu-user-static \
  ltrace strace \
  python3 python3-venv python3-pip \
  ffmpeg imagemagick \
  mono-complete \
  cmake ninja-build scons pkg-config python3-dev \
  libssl-dev zlib1g-dev libxml2-dev \
  autoconf automake libtool \
  git-lfs sqlite3 \
  curl ca-certificates

# ---------------------------------------------------------------------------
# 2. Ghidra
# ---------------------------------------------------------------------------
if [ -x "$GHIDRA_DIR/support/analyzeHeadless" ]; then
  log "Ghidra already present at $GHIDRA_DIR"
else
  log "Downloading Ghidra ${GHIDRA_VERSION}"
  ZIP="ghidra_${GHIDRA_VERSION}_PUBLIC_${GHIDRA_BUILD}.zip"
  URL="https://github.com/NationalSecurityAgency/ghidra/releases/download/Ghidra_${GHIDRA_VERSION}_build/${ZIP}"
  tmp="$(mktemp -d)"
  curl -fsSL -C - -o "$tmp/$ZIP" "$URL" || die "failed to download Ghidra"
  unzip -q -t "$tmp/$ZIP" >/dev/null || die "Ghidra zip is corrupt"
  unzip -q -o "$tmp/$ZIP" -d /opt
  ln -sfn "/opt/ghidra_${GHIDRA_VERSION}_PUBLIC" "$GHIDRA_DIR"
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
  curl -fsSL -o "$tmp/rizin.tar.xz" "$URL" || die "failed to download rizin"
  mkdir -p "$RIZIN_DIR"
  tar -xJf "$tmp/rizin.tar.xz" -C "$RIZIN_DIR"
  rm -rf "$tmp"
  for b in "$RIZIN_DIR"/bin/*; do ln -sfn "$b" "/usr/local/bin/$(basename "$b")"; done
  log "rizin installed -> $RIZIN_DIR/bin"
fi

# ---------------------------------------------------------------------------
# 4. Python RE libraries (isolated venv, never touches system python)
# ---------------------------------------------------------------------------
[ -x "$VENV/bin/python" ] || { log "Creating Python venv at $VENV"; mkdir -p "$TOOLS_DIR"; python3 -m venv "$VENV"; }
log "Installing Python RE libraries: ${PYTHON_RE_PACKAGES[*]}"
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q "${PYTHON_RE_PACKAGES[@]}"

# ---------------------------------------------------------------------------
# 5. Godot 4 editor + export templates
# ---------------------------------------------------------------------------
if [ -x "$GODOT_BIN" ]; then
  log "Godot editor already present at $GODOT_BIN"
else
  log "Downloading Godot ${GODOT_VERSION}"
  ZIP="Godot_v${GODOT_VERSION}_linux.x86_64.zip"
  URL="https://github.com/godotengine/godot/releases/download/${GODOT_VERSION}/${ZIP}"
  tmp="$(mktemp -d)"
  curl -fsSL -C - -o "$tmp/$ZIP" "$URL" || die "failed to download Godot"
  mkdir -p "$GODOT_DIR"
  unzip -q -o "$tmp/$ZIP" -d "$GODOT_DIR"
  chmod +x "$GODOT_BIN"
  ln -sfn "$GODOT_BIN" /usr/local/bin/godot
  rm -rf "$tmp"
  log "Godot editor installed -> $GODOT_BIN"
fi

# Templates dir is <major.minor.patch>.stable — NOT the full --version string,
# which includes a build hash that Godot does not use for this path.
GODOT_TEMPLATE_VER="$(printf '%s' "$GODOT_VERSION" | sed 's/^\([0-9.]*\)-stable$/\1.stable/')"
TEMPLATE_DIR="$HOME/.local/share/godot/export_templates/$GODOT_TEMPLATE_VER"
if [ -f "$TEMPLATE_DIR/android_release.apk" ]; then
  log "Godot export templates already present at $TEMPLATE_DIR"
else
  log "Downloading Godot export templates (~1.2 GB)"
  TPZ="Godot_v${GODOT_VERSION}_export_templates.tpz"
  URL="https://github.com/godotengine/godot/releases/download/${GODOT_VERSION}/${TPZ}"
  tmp="$(mktemp -d)"
  curl -fsSL -C - -o "$tmp/$TPZ" "$URL" || die "failed to download export templates"
  unzip -q -o "$tmp/$TPZ" -d "$tmp"
  mkdir -p "$TEMPLATE_DIR"
  cp -a "$tmp/templates/." "$TEMPLATE_DIR/"
  rm -rf "$tmp"
  log "Export templates installed -> $TEMPLATE_DIR"
fi

# ---------------------------------------------------------------------------
# 6. Android SDK (cmdline-tools, platform, build-tools, NDK, CMake)
# ---------------------------------------------------------------------------
if [ -x "$SDK_DIR/cmdline-tools/latest/bin/sdkmanager" ]; then
  log "Android cmdline-tools already present"
else
  log "Downloading Android command-line tools"
  ZIP="commandlinetools-linux-${ANDROID_CMDLINE_TOOLS}_latest.zip"
  URL="https://dl.google.com/android/repository/${ZIP}"
  tmp="$(mktemp -d)"
  curl -fsSL -C - -o "$tmp/clt.zip" "$URL" || die "failed to download Android cmdline-tools"
  mkdir -p "$SDK_DIR/cmdline-tools"
  unzip -q -o "$tmp/clt.zip" -d "$tmp"
  mv "$tmp/cmdline-tools" "$SDK_DIR/cmdline-tools/latest"
  rm -rf "$tmp"
  log "Android cmdline-tools installed"
fi

SDKMANAGER="$SDK_DIR/cmdline-tools/latest/bin/sdkmanager"
export ANDROID_HOME="$SDK_DIR"
export ANDROID_SDK_ROOT="$SDK_DIR"
export JAVA_HOME="${JAVA_HOME:-/usr/lib/jvm/java-21-openjdk-amd64}"

log "Accepting Android SDK licenses"
yes 2>/dev/null | "$SDKMANAGER" --licenses >/dev/null 2>&1 || warn "some licenses may not have been accepted"

log "Installing SDK packages (this downloads several GB)"
"$SDKMANAGER" --install \
  "platform-tools" \
  "platforms;${ANDROID_PLATFORM}" \
  "build-tools;${ANDROID_BUILD_TOOLS}" \
  "ndk;${ANDROID_NDK}" \
  "cmake;${ANDROID_CMAKE}" >/dev/null

# ---------------------------------------------------------------------------
# 7. .NET SDK (runs AssetRipper / Il2CppDumper / ILSpy on Unity & .NET games)
# ---------------------------------------------------------------------------
if [ -x "$DOTNET_DIR/dotnet" ]; then
  log ".NET SDK already present at $DOTNET_DIR"
else
  log "Installing .NET SDK (channel ${DOTNET_CHANNEL})"
  tmp="$(mktemp -d)"
  curl -fsSL -o "$tmp/dotnet-install.sh" https://dot.net/v1/dotnet-install.sh || die "failed to fetch dotnet installer"
  chmod +x "$tmp/dotnet-install.sh"
  "$tmp/dotnet-install.sh" --channel "$DOTNET_CHANNEL" --install-dir "$DOTNET_DIR" --no-path >/dev/null
  rm -rf "$tmp"
  ln -sfn "$DOTNET_DIR/dotnet" /usr/local/bin/dotnet
  log ".NET SDK installed -> $DOTNET_DIR"
fi

# ---------------------------------------------------------------------------
# 8. Debug keystore + Godot editor settings
# ---------------------------------------------------------------------------
if [ ! -f "$KEYSTORE" ]; then
  log "Generating Android debug keystore"
  mkdir -p "$HOME/.android"
  keytool -keyalg RSA -genkeypair -alias androiddebugkey -keypass android \
    -keystore "$KEYSTORE" -storepass android \
    -dname "CN=Android Debug,O=Android,C=US" -validity 10000 -deststoretype pkcs12
else
  log "Debug keystore already present at $KEYSTORE"
fi

log "Configuring Godot editor settings for Android export"
# Godot names this file editor_settings-<major>.<minor>.tres (e.g. 4.7.tres) --
# NOT the full patch version, and not the export-templates dir name.
GODOT_EDITOR_VER="$(printf '%s' "$GODOT_VERSION" | sed 's/-stable$//' | cut -d. -f1,2)"
SETTINGS_DIR="$HOME/.config/godot"
SETTINGS="$SETTINGS_DIR/editor_settings-${GODOT_EDITOR_VER}.tres"
mkdir -p "$SETTINGS_DIR"
[ -f "$SETTINGS" ] || printf '[gd_resource type="EditorSettings" format=3]\n\n[resource]\n' > "$SETTINGS"
python3 - "$SETTINGS" "$JAVA_HOME" "$SDK_DIR" "$KEYSTORE" <<'PY'
import sys
path, java_home, sdk_dir, keystore = sys.argv[1:5]
wanted = {
    'export/android/java_sdk_path': java_home,
    'export/android/android_sdk_path': sdk_dir,
    'export/android/debug_keystore': keystore,
    'export/android/debug_keystore_user': 'androiddebugkey',
    'export/android/debug_keystore_pass': 'android',
}
lines = open(path).read().splitlines()
lines = [l for l in lines if l.split(' = ')[0] not in wanted]
idx = max((i for i, l in enumerate(lines) if l.startswith('export/android/')), default=len(lines) - 1)
for k, v in wanted.items():
    lines.insert(idx + 1, f'{k} = "{v}"')
open(path, 'w').write('\n'.join(lines) + '\n')
PY

# ---------------------------------------------------------------------------
# 9. Shell profile so a login shell can find everything
# ---------------------------------------------------------------------------
log "Writing /etc/profile.d/re-tools.sh"
cat > /etc/profile.d/re-tools.sh <<EOF
export GHIDRA_INSTALL_DIR=$GHIDRA_DIR
export RE_TOOLS_VENV=$VENV
export GODOT_BIN=$GODOT_BIN
export ANDROID_HOME=$SDK_DIR
export ANDROID_SDK_ROOT=$SDK_DIR
export ANDROID_NDK_HOME=$SDK_DIR/ndk/$ANDROID_NDK
export DOTNET_ROOT=$DOTNET_DIR
export PATH="\$PATH:$VENV/bin:$GODOT_BIN:$SDK_DIR/platform-tools:$SDK_DIR/cmdline-tools/latest/bin:$SDK_DIR/build-tools/$ANDROID_BUILD_TOOLS:$DOTNET_DIR"
EOF
chmod +x /etc/profile.d/re-tools.sh

log "Done. Next: bash scripts/verify-re-tools.sh"