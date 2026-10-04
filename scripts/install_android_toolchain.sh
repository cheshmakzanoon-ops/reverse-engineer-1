#!/usr/bin/env bash
# Linux x86_64. Run after JDK17+ and sdkmanager setup; never edits the DMG.
set -euo pipefail
DEST="${1:?usage: bash scripts/install_android_toolchain.sh /toolchain/destination}"
mkdir -p "$DEST"
DEST="$(cd "$DEST" && pwd)"
command -v sdkmanager >/dev/null
: "${JAVA_HOME:?Set JAVA_HOME to JDK17 or newer}"
SDK="${ANDROID_SDK_ROOT:-${ANDROID_HOME:?Set ANDROID_HOME or ANDROID_SDK_ROOT}}"
sdkmanager --sdk_root="$SDK" 'platform-tools' 'build-tools;36.0.0' 'platforms;android-36' 'cmake;3.10.2.4988404' 'ndk;28.1.13356709'
VERSION=4.7.2-stable
BASE="https://github.com/godotengine/godot/releases/download/$VERSION"
ENGINE="Godot_v${VERSION}_linux.x86_64.zip"
TEMPLATES="Godot_v${VERSION}_export_templates.tpz"
curl --fail --location --retry 3 "$BASE/$ENGINE" -o "$DEST/$ENGINE"
curl --fail --location --retry 3 "$BASE/$TEMPLATES" -o "$DEST/$TEMPLATES"
# Digests from the official GitHub release asset metadata, checked 2026-10-03.
printf '%s  %s\n' \
  cadd3204e728a35d3f13adb7fd0d7902636b79f6b95c40c265eb73b6c35329e4 "$DEST/$ENGINE" \
  f298490b8d44d934be425a5a65a51bf15f422428b229a06a6e11d9ffea248011 "$DEST/$TEMPLATES" | sha256sum --check
unzip -o -q "$DEST/$ENGINE" -d "$DEST/bin"
chmod +x "$DEST/bin/Godot_v${VERSION}_linux.x86_64"
ln -sfn "Godot_v${VERSION}_linux.x86_64" "$DEST/bin/godot"
mkdir -p "$DEST/templates/4.7.2.stable"
unzip -o -j -q "$DEST/$TEMPLATES" templates/android_debug.apk templates/android_release.apk templates/android_source.zip -d "$DEST/templates/4.7.2.stable"
curl --fail --location --retry 3 'https://github.com/google/bundletool/releases/download/1.18.1/bundletool-all-1.18.1.jar' -o "$DEST/bundletool.jar"
# This release supplies no asset digest. Record it; do not claim hash pinning.
sha256sum "$DEST/bundletool.jar" > "$DEST/bundletool.sha256"
"$JAVA_HOME/bin/java" -jar "$DEST/bundletool.jar" version | grep -Fx '1.18.1'
"$DEST/bin/godot" --version
printf 'GODOT=%s\nTEMPLATES=%s\nBUNDLETOOL=%s\n' "$DEST/bin/godot" "$DEST/templates/4.7.2.stable" "$DEST/bundletool.jar"
