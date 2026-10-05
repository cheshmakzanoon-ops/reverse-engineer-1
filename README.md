# reverse-engineer-1

An **incomplete Godot 4 / Android reconstruction** of the legally obtained
macOS Warped Kart Racers v2.02 build. This is not a complete Android game,
original source-code recovery, emulator, or wrapper around the macOS binary.

## Verified status — 2026-10-05

| Layer | Evidence / limitation |
|---|---|
| Recovered definitions | 814 ScriptableObjects represented in the committed data; generated GDScript validated by tests |
| Track structure | 16 decoded maps, including 12 race routes; generated roads are not imported original track art |
| Kart/input | Per-kart commands, isolated AI/player input, selected profiles, forward grid heading, HUD binding, touch cancellation and reset state have runtime regressions |
| Test baseline | 483 Godot checks across 16 suites and 228 Python tests pass locally; import and 360-frame headless boot pass |
| Full race correctness | Six karts physically complete three Arlen Speedway laps through 188 ordered gates, with and without combat. Pickup contacts, held inventory and item effects have behavioral tests; the additional one-lap session integration saves the actual result and restores it in a fresh application instance |
| Front end / local progress | Title, setup, settings, loading, pause, restart, results, rematch and menus are connected; 70 graphical checks include actual synthetic screen touches, focus loss, two-finger pause and safe-area layout. Two-generation local saves have 32 checks |
| Android | Earlier CI run `37208484685` at `b89801a` verified an engineering ARM64 debug APK, then failed test-AAB validation; emulator was skipped. This decoder increment adds no new Android export or physical-device result |
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

## Content-recovery pipeline

The source-gated inventory, definition requirements, supported static/skinned GLB
conversion and real Godot import validation are implemented and tested with
authored fixtures. **Original Arlen/Hank/kart art is not recovered by that test.**
A later real-source run is documented below; full production catalog completion
and resource conversion remain separate verification gates.
See [content recovery commands and limits](docs/CONTENT-RECOVERY.md),
[the content increment evidence](docs/status/2026-10-04-content-pipeline.md), and
[the exact unresolved Arlen requirements](docs/status/2026-10-04-arlen-content-requirements.json).
The original-content and Android status rows above are not promoted to success.

## Bounded original-content inventory

The recovery runner now checkpoints bundles and bounds decoder/finalizer
processes. A timeout retains completed private work and an object/phase cursor;
resume verifies source, decoder and payload hashes before reuse. The final
catalog still requires the existing full reference/integrity gates. See
[commands and limits](docs/BOUNDED-CONTENT-INVENTORY.md) and
[the increment evidence](docs/status/2026-10-04-bounded-inventory.md).
The 30 new regression tests are authored-fixture/process tests, **not new
original-game extraction or art integration evidence**. Private checkpoint
payloads are not included in public CI artifacts.

## Object-level continuation and encrypted recovery storage

Production workers now reuse hash-verified complete objects within interrupted
bundles, not just complete bundles. Private checkpoints can be encrypted and
restored into a fresh compatible workspace. The hosted workflow can retain the
ciphertext before cleanup and explicitly restore a trusted prior run using an
owner-held key; it does not silently assume that key is configured.

The exact original DMG is now verified and extracted in the development runtime.
A deliberate SIGKILL/resume test on the original Smith's Dreamworld bundle reused
4,575 objects and completed processing of 11,564 records. Its encrypted round trip
verified 23,224 artifact files. That historical run recorded three raw numeric
serialization errors; the later repair below resolves their raw preservation.
Neither result is a production scene import or recovered original C# project.
See [operation and key requirements](docs/OBJECT-CHECKPOINTS.md) and
[production evidence and limits](docs/status/2026-10-04-object-resume.md).

## Lossless raw numeric recovery

The original Smith's Dreamworld bundle now processes all **11,564 objects with
zero decoder errors**. The three previously blocked raw typetrees are retained,
including five positive-infinity fields and their exact parsed binary64 tags.
All original object bytes remain independently hashed; no values are changed to
zero and no curve/timeline semantics are guessed. Runtime scene/mesh/GLB paths
still reject non-finite numbers until an explicit semantic converter exists.

The increment adds 20 regression tests, for **196 Python tests**; all **483 Godot
checks** and seven byte-identical recovered-data generators also pass. This
repairs real input preservation, not imported track/character art or audio.
See [production evidence and remaining gates](docs/status/2026-10-04-numeric-recovery.md)
and [raw-versus-runtime format rules](docs/CONTENT-RECOVERY.md#raw-numeric-preservation-versus-runtime-conversion).

## Serialized-empty font atlas recovery

The next texture gate distinguishes supported serialized-empty font atlases from
missing production images. It preserves raw records, verifies file-scoped native
Font/TMP ownership, and never manufactures pixels. Unowned/malformed empty textures
still fail; runtime material conversion still rejects non-renderable atlases.

The increment adds 32 authored regressions and a separate hash-locked production
check for the six original font-atlas records. This is not font rendering, original
art integration, complete source recovery or Android verification. See
[format rules and source-check commands](docs/EMPTY-FONT-ATLASES.md) and
[current validation evidence](docs/status/2026-10-05-empty-font-atlases.md).

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
