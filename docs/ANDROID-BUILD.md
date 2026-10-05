# Android packaging and evidence gates

This pipeline packages the **incomplete engineering reconstruction**, not a
complete original-content game. Test signing is not a Play release identity.
No original DMG, extracted media, private signing key or user save belongs in
CI artifacts. The source uses original Kart Lab launcher branding and the
owner-specific namespace `io.github.cheshmakzanoonops.kartlab` (`.test` for tests).

## Linux x86_64 setup

Use JDK 17 (21 is also supported by Godot), Android command-line tools with
`sdkmanager` on PATH, and a writable SDK directory. Review/accept the Android
SDK licenses with `sdkmanager --licenses`. Set `JAVA_HOME` and
`ANDROID_SDK_ROOT` or `ANDROID_HOME`, then run:

```sh
bash scripts/install_android_toolchain.sh /absolute/path/kart-toolchain
export PATH=/absolute/path/kart-toolchain/bin:$PATH
```

The script installs API 36, build-tools 36.0.0, platform tools, NDK r28b and
CMake 3.10.2. It downloads the pinned Godot 4.7.2 engine/templates and verifies
their official release SHA-256 values before extraction. Bundletool 1.18.1 is
version-checked; its release has no published asset digest, so the observed
hash is recorded but is **not** described as a verified upstream hash.
The installer requires network access to Google and GitHub.

Policy: minimum API 24, target API 36, landscape, immersive mode, arm64-v8a,
local-only storage and no requested internet/storage permissions. The extra
x86_64 build exists only for emulator testing, not as the default shipped ABI.
Compatibility rendering is selected for the engineering mobile build; this
does not establish a 60 FPS target or original visual quality.

## Build and verify

```sh
TOOLCHAIN=/absolute/path/kart-toolchain
python3 scripts/android_build.py --target debug-apk \
  --output "$PWD/export/kart-test.apk" --godot "$TOOLCHAIN/bin/godot" \
  --templates "$TOOLCHAIN/templates/4.7.2.stable"
python3 scripts/android_build.py --target test-aab \
  --output "$PWD/export/kart-test.aab" --godot "$TOOLCHAIN/bin/godot" \
  --templates "$TOOLCHAIN/templates/4.7.2.stable" \
  --bundletool "$TOOLCHAIN/bundletool.jar"
```

Each invocation copies source into an isolated temporary project, installs
matching Android build templates, sets SDK/JDK editor paths without modifying
user settings, imports, and performs a Gradle export. A new output path is
required; stale files cannot pass as a new build. The working project, DMG
and recovered JSON are never edited by the export command.

APK checks: nonempty manifest/DEX/engine/project, exact ABI, ZIP integrity,
`apksigner verify`, 16 KB ZIP alignment and `aapt dump badging` (identifier,
launcher, minimum and target API). AAB checks: bundle structure, RSA signing
records, `jarsigner -verify`, `bundletool validate` and the decoded manifest.
ZIP/alignment checks alone do not prove native ELF compatibility or launch.
Only a successful verified export emits a sidecar `.apk.json`/`.aab.json`
with source/artifact hashes, package policy and explicit untested-device flags.
Failure output may include a partial package; absence of the sidecar means
it is **not verified** and must not be described as an installable result.

## Signing

Tests generate an ephemeral RSA debug key in a temporary private directory;
it is destroyed after the command. Thus separately generated test APKs may
require uninstalling an earlier test build (which loses its app data). For
stable local test updates, provide all three documented Godot variables:
`GODOT_ANDROID_KEYSTORE_DEBUG_PATH`, `GODOT_ANDROID_KEYSTORE_DEBUG_USER`,
`GODOT_ANDROID_KEYSTORE_DEBUG_PASSWORD`. Keep that key outside the repository.

Release never generates or substitutes an identity. Supply the owner's
existing RSA upload/release keystore via `GODOT_ANDROID_KEYSTORE_RELEASE_PATH`,
`GODOT_ANDROID_KEYSTORE_RELEASE_USER`, `GODOT_ANDROID_KEYSTORE_RELEASE_PASSWORD`
from the shell or protected CI secrets; store and key passwords must match.
Then use `--target release-aab` with a new `.aab` output and bundletool path.
Missing credentials fail before export. Passwords are redacted in command logs.
No release workflow publishes anything automatically. A successful signed
bundle still needs licensing, complete content, store-policy and device QA
gates; **test-signed bundles are not release-ready**.

## Emulator smoke

The `android-packaging` workflow builds the arm64 test APK/AAB and an explicit
x86_64 emulator APK. Its dependent emulator job installs and launches that
APK three times, checks background/resume process survival, saves screenshots
and rejects Android/Godot errors in logcat. It never automatically targets a
physical device. The same check can run against an already started emulator:

```sh
python3 scripts/android_smoke.py --serial emulator-5554 \
  --apk export/emulator.apk --output verification/emulator-smoke
```

Launch smoke is not full touch navigation, a completed race, audio playback,
physical-device acceptance or a performance measurement. Report each separately.
A queued job, YAML file or build command is not a successful CI result.

## Sources checked 2026-10-03

- Godot Android export and signing variables: https://docs.godotengine.org/en/stable/tutorials/export/exporting_for_android.html
- Godot Android export options: https://docs.godotengine.org/en/stable/classes/class_editorexportplatformandroid.html
- Official pinned engine release/digests: https://github.com/godotengine/godot/releases/tag/4.7.2-stable
- Play API 36 requirement from August 31, 2026: https://developer.android.com/google/play/requirements/target-sdk
- Bundletool: https://developer.android.com/tools/bundletool

The Godot tutorial still lists API 35 in its generic SDK example; this
project's target follows the newer explicit Play requirement, not that example.

## AAB project asset packs and explicit signing

A Godot AAB can place `project.binary` in a module such as
`assetPackInstallTime/assets/`, not `base/assets/`. Structural validation now
requires exactly one project location, nonempty pack metadata, and the base
manifest/DEX/native engine. An asset-only project pack may not contain executable
code. After `bundletool validate`, the actual pack manifest is decoded with
`bundletool dump manifest --module=<project_module>` and must declare an
unconditional, fused, install-time asset pack with the expected package and split.
On-demand and fast-follow placement is not accepted for the startup project.

An unsigned AAB is signed explicitly with the already selected debug or
owner-controlled release identity. Passwords are passed to JDK `jarsigner` through
environment variable names, not command-line literals. Existing signatures are
not silently replaced. Partial or unsupported signing records fail; signature
presence alone does not pass the cryptographic verification that follows. No
release identity is generated and release signing never falls back to debug.
The success sidecar is written only after signing, bundletool and manifest gates.

The historical test AAB from workflow `37308162586` exposed both issues: the
project existed in `assetPackInstallTime`, and the export had no JAR-signing
records. A local copy was test-signed and its 188 original entries were verified
unchanged. That recheck is not a new project export, bundletool validation, Play
acceptance or device test. See the current increment report for exact evidence.

Primary references (consulted 2026-10-05):
- Android asset-pack layout and install-time access:
  https://developer.android.com/guide/playcore/asset-delivery/integrate-java
- Command-line Android bundle building and signing:
  https://developer.android.com/build/building-cmdline
- Bundletool validation and APK generation:
  https://developer.android.com/tools/bundletool
