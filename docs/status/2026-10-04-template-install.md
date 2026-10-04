# Android template installation repair — 2026-10-04

Base: `cae87459a452c9baba9f1cad2d2984b9337674e6`, single `main`.

## Audit and reproduced failure

Inspected the current branch, recent commits, workflow runs, root and port
READMEs, RE findings/setup, export presets, project scripts, tests, recovered
data and LFS policy. Runtime run `37165639534` passes; Android run
`37165639643` fails in job `111327848991` because the Android build template
was not installed. The SDK/toolchain installation step itself succeeded.

Godot 4.7.2 accepts `--install-android-build-template`, but only processes it
with `--export-debug` or `--export-release`. Running it separately with
`--editor --quit` exits zero without installing a template. The pinned
engine's `--help` and official command-line reference document this pairing:
https://docs.godotengine.org/en/stable/tutorials/editor/command_line_tutorial.html

The CI source snapshot and engine were downloaded from artifact `11293897861`.
The artifact digest and both SHA256SUMS entries were verified, and its
source-commit file matches the base above. No DMG/media or keys were fetched.

## Implemented and tested

`scripts/android_build.py` now pairs template installation with the actual
export for debug APK, emulator APK, test AAB and owner-signed release AAB.
The redundant standalone editor invocation is removed. Signature, manifest,
ABI, alignment, output and signing-identity gates are unchanged.

Two regressions were added to `tests/test_android_build.py`. The first runs
the build orchestrator through all four targets and checks the actual export
invocation. The second uses the real pinned engine and a deliberately
non-buildable ZIP fixture, proving template extraction, version marker,
ignore marker and executable-bit preservation before SDK validation. The
fixture export must fail, must not create an APK, and must not emit a verified
sidecar. This component test does not pretend to build a usable Android app.
Both regressions failed on the old code (five failing target/test cases), then
passed after repair. CI runs the real-engine regression after toolchain setup.

Local evidence: **28 Python tests pass with GODOT_TEST_BIN set**; all **338
Godot checks in 12 suites pass**; import and a 360-frame headless boot pass.
Six karts complete three Arlen Speedway laps without combat and with combat.
The combat run records 177 activations and 51 hits; all six finish after
326.617 simulated seconds. Fixed-rate headless simulation is not a phone FPS
benchmark. One outer tool invocation timed out during the combat suite; the
complete isolated rerun passed under its existing 180-second test limit.

```sh
GODOT_TEST_BIN=/path/to/Godot_v4.7.2-stable_linux.x86_64 \
  python3 -m unittest discover -s tests -p 'test_*.py' -v
python3 scripts/checked_process.py --timeout 240 -- godot --headless --path port --import
python3 scripts/checked_process.py --timeout 120 -- godot --headless --path port --quit-after 360
# Each port/tests/test_*.gd was run with the README checked-process command.
```

Files: `scripts/android_build.py`, `tests/test_android_build.py`,
`.github/workflows/android.yml`, and this report.

## Remaining gates

The local container still lacks the real Android SDK and export templates.
No usable APK/AAB or release signature is claimed by this increment; the
next actual package proof must come from the corrected CI or an equipped
host. No emulator or physical device was tested locally. Original art/audio,
complete menu/save flow, battle modes and native behavior parity remain
incomplete. Next independent increment: pause/restart/menu navigation and
crash-safe local progress, followed by their runtime regressions.
