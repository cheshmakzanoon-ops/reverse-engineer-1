# reverse-engineer-1

An **incomplete Godot 4 / Android reconstruction** of the legally obtained
macOS Warped Kart Racers v2.02 build. This is not a complete Android game,
original source-code recovery, emulator, or wrapper around the macOS binary.

## Verified status — 2026-10-04

| Layer | Evidence / limitation |
|---|---|
| Recovered definitions | 814 ScriptableObjects represented in the committed data; generated GDScript validated by tests |
| Track structure | 16 decoded maps, including 12 race routes; generated roads are not imported original track art |
| Kart/input | Per-kart commands, isolated AI/player input, selected profiles, forward grid heading, HUD binding, touch cancellation and reset state have runtime regressions |
| Test baseline | 463 Godot checks across 15 suites and 28 Python tests pass locally; import and 360-frame headless boot pass |
| Full race correctness | Six karts physically complete three Arlen Speedway laps through 188 ordered gates, with and without combat. Pickup contacts, held inventory and item effects have behavioral tests; the additional one-lap session integration saves the actual result and restores it in a fresh application instance |
| Front end / local progress | Title, setup, settings, loading, pause, restart, results, rematch and menus are connected; 70 graphical checks include actual synthetic screen touches, focus loss, two-finger pause and safe-area layout. Two-generation local saves have 32 checks |
| Android | Template installation is now paired with export. Local attempts stop on missing SDK/templates; the repaired CI job was still queued when checked. No APK/AAB, emulator or physical-device success is claimed for this revision |
| Original art and audio | Not integrated; placeholder geometry and silence are not an acceptable completed vertical slice |

[Session and persistence evidence](docs/status/2026-10-04-session-flow.md),
[Android template repair](docs/status/2026-10-04-template-install.md),
[Android packaging commands](docs/ANDROID-BUILD.md),
[pipeline evidence](docs/status/2026-10-03-android-pipeline.md),
[item increment evidence](docs/status/2026-10-03-items.md),
[race increment evidence and commands](docs/status/2026-10-03-race.md),
[input evidence](docs/status/2026-10-03-input.md) and
[the preceding audit](docs/status/2026-10-03-audit.md) supersede historical
completion claims in older documentation. A green parser, scene boot or APK
export is not evidence of a complete playable race.

## What recovery means

Unity IL2CPP metadata yields class/field layouts, signatures and attributes;
recovered stub method bodies are not the original C# implementations. Targeted
native analysis and recovered tuning guide the GDScript reconstruction.
Port-side algorithms and approximations must be labelled explicitly.

Recovered JSON: `work/assets/definitions/definitions.json` and
`work/assets/tracks.json`. Generated data: `port/scripts/data/`. Never edit
those generated modules to invent recovered values. Original DMG content is
tracked through Git LFS; do not overwrite it or commit generated intermediates.

## Development and validation

Work on the single `main` branch. Add meaningful behavioral regressions for
each increment and distinguish implemented, approximated, runtime-tested,
Android-built, emulator-tested and physical-device-tested states.

```sh
# Use the pinned Godot 4.7.2 engine; Linux UI tests also need xvfb and xauth.
GODOT_TEST_BIN="$(command -v godot)" python3 -m unittest discover -s tests -v
python3 scripts/checked_process.py --timeout 240 -- godot --headless --path port --import
GODOT="$(command -v godot)" bash scripts/test_godot.sh verification
```

The checked runner rejects silent Godot errors even when the engine exits zero.
The UI suite runs in an Xvfb window because headless execution has no window-input
callback; the other suites remain headless. Graphical screenshots are retained.
CI retains exact source/engine inputs and test logs as short-lived artifacts;
no DMG, extracted media or signing credentials belong in those artifacts.

Toolchain setup and actual smoke tests: [docs/RE-SETUP.md](docs/RE-SETUP.md).
Recovery findings: [docs/RE-FINDINGS.md](docs/RE-FINDINGS.md).
Historical port details: [port/README.md](port/README.md).
Only distribute original game content when authorized; technical reconstruction
and public-release licensing/signing are separate gates.
