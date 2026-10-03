# reverse-engineer-1

An **incomplete Godot 4 / Android reconstruction** of the legally obtained
macOS Warped Kart Racers v2.02 build. This is not a complete Android game,
original source-code recovery, emulator, or wrapper around the macOS binary.

## Verified status — 2026-10-03

| Layer | Evidence / limitation |
|---|---|
| Recovered definitions | 814 ScriptableObjects represented in the committed data; generated GDScript validated by tests |
| Track structure | 16 decoded maps, including 12 race routes; generated roads are not imported original track art |
| Kart/input | Per-kart commands, isolated AI/player input, selected profiles, forward grid heading, HUD binding, touch cancellation and reset state have runtime regressions |
| Test baseline | 220 Godot checks and 13 Python tests pass in the input increment; import and 360-frame headless boot pass |
| Full race correctness | Not established: proximity-based gates, counter-editing legacy integration tests, unwired pickup collisions and incomplete results/menu flow remain blockers |
| Android | Historical prototype export exists in development history; this input revision is not yet Android-built, emulator-tested or physical-device-tested |
| Original art and audio | Not integrated; placeholder geometry and silence are not an acceptable completed vertical slice |

[Input increment evidence and commands](docs/status/2026-10-03-input.md) and
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
python3 -m unittest discover -s tests -v
godot --headless --path port --import
for test in port/tests/test_*.gd; do
  name="$(basename "$test" .gd)"
  python3 scripts/checked_process.py --timeout 180 --require 'PASS:' \
    --log "verification/$name.log" -- \
    godot --headless --path port --script "res://tests/$name.gd"
done
```

The checked runner rejects silent Godot errors even when the engine exits zero.
CI retains exact source/engine inputs and test logs as short-lived artifacts;
no DMG, extracted media or signing credentials belong in those artifacts.

Toolchain setup and actual smoke tests: [docs/RE-SETUP.md](docs/RE-SETUP.md).
Recovery findings: [docs/RE-FINDINGS.md](docs/RE-FINDINGS.md).
Historical port details: [port/README.md](port/README.md).
Only distribute original game content when authorized; technical reconstruction
and public-release licensing/signing are separate gates.
