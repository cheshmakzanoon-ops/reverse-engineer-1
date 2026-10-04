# Android export pipeline increment — 2026-10-03

Base: `435bb95d1cf868985a1162f7b2fb8f9ce736ba78`, single `main`.

Implemented: isolated Linux CLI exports for arm64 debug APK, test-signed AAB,
owner-signed release AAB and an explicit x86_64 emulator APK; SDK/JDK/template
preflight; signing-variable validation and log redaction; artifact structure,
manifest/API/ABI/signature/alignment gates and hash-bearing sidecar reports.
Original Kart Lab SVG icon/adaptive layers replace original-game branding in
packaging. The default renderer is Compatibility. Target API 36/minimum 24
and no unnecessary network/storage permission are explicit in all presets.
A build/install/smoke CI workflow now has the commands needed to obtain missing
SDK/templates, build packages, and test emulator launch/background/relaunch.

Local evidence: **26 Python tests pass**, including 13 new Android build
regressions. **338 Godot checks still pass in all 12 suites** after project
configuration changes. Headless import and 360-frame boot pass. A 60-frame
Xvfb/llvmpipe graphical launch passes with `--audio-driver Dummy`. The preceding
attempt without that flag correctly failed the error gate because no ALSA
audio device exists here; no audible playback is claimed.

The actual local build invocation fails dependency preflight: SDK build tools,
platform/adb and matching Android templates are absent. It produces no APK,
AAB or verified sidecar. These scripts/tests are **not evidence of a successful
Android build** until the workflow or an equipped host executes them. No local
emulator or physical-device test was performed. Owner release credentials are
not available and were not generated or stored in Git. Original assets/audio
remain absent from the engineering runtime.

Commands executed:

```sh
python3 -m unittest discover -s tests -p 'test_*.py' -v
bash -n scripts/install_android_toolchain.sh
python3 -m py_compile scripts/android_build.py scripts/android_smoke.py
python3 scripts/android_build.py --output /mnt/data/android-build/guard-test.apk --java /usr/lib/jvm/java-21-openjdk-amd64
# Expected nonzero: missing dependencies, no artifact generated.
# Godot suites use the checked runner, --headless --fixed-fps 60, as in README.
xvfb-run -a python3 scripts/checked_process.py --timeout 30 -- godot --path port --audio-driver Dummy --quit-after 60
```

Files: `scripts/android_build.py`, `scripts/android_smoke.py`,
`scripts/install_android_toolchain.sh`, `tests/test_android_build.py`,
`.github/workflows/android.yml`, `.gitignore`, `port/export_presets.cfg`,
`port/project.godot`, `port/branding/{icon,foreground,background}.svg`
and their import metadata,
`docs/ANDROID-BUILD.md`, this report, and README status links.
Next: actual CI export evidence, then tested pause/restart/menu/save flow.
