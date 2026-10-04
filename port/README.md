# Godot 4 / Android reconstruction

This is an **incomplete engineering reconstruction**, not the original source
code or a finished Android game. The [root README](../README.md) and
[current session evidence](../docs/status/2026-10-04-session-flow.md) supersede historical
prototype APK and completion claims.

## Verified core race

Godot 4.7.2 headless tests drive six karts through three Arlen Speedway laps,
188 ordered finite gates per lap, with actual CharacterBody3D motion rather
than edited lap counters. Tests exercise countdown locking, forward crossing,
ranking, finish times, automatic fall recovery, results and stopped finishers.
The current increment passes 483 Godot checks across 16 suites and 74 Python tests locally.

The source snapshot still uses placeholder kart/road visuals and has no
original audio. Pickup contacts, per-kart inventory, boosts, shields, projectiles, hazards and
AI item use now have behavioral tests. A separate physical one-lap test completes
the session, saves the real result, rematches and restores progress in a fresh
application instance. Original-content import, battle rules and full native
behavior parity remain incomplete. Other maps have
data/geometry checks, not this full-race proof. AI, interpolation, gates,
recovery and physics include explicitly labelled port-side implementations;
native behavior equivalence is not established.

## Reproduce from the repository root

```sh
GODOT_TEST_BIN="$(command -v godot)" python3 -m unittest discover -s tests -p 'test_*.py' -v
python3 scripts/checked_process.py --timeout 240 -- godot --headless --path port --import
# Linux: install xvfb, xauth and a Mesa OpenGL driver for the graphical UI suite.
GODOT="$(command -v godot)" bash scripts/test_godot.sh verification
```

Fixed physics time accelerates simulation; it is **not** an Android 60 FPS
benchmark. No physical Android device or emulator result is claimed here.

Use [the isolated Android build pipeline](../docs/ANDROID-BUILD.md).
The local Android export of this revision failed because SDK, export templates
and editor paths were missing. Historical APKs do not prove a build of this
source. Release credentials must stay out of Git; public distribution of
original game art, music, voices and trademarks requires authorization.

## Local session flow

The project boots `scenes/frontend.tscn`; `scenes/main.tscn` remains the isolated
race used by the physics regressions. Title -> main menu -> race setup -> loading
-> countdown/race -> results -> rematch/menu is connected. Pause offers resume,
restart and menu. Back pauses racing, resumes an explicitly focused paused race,
returns from setup/settings/results, or requests exit confirmation at the title.
An application focus-loss notification pauses; focus gain never auto-resumes.

Touch uses a steering pad plus GO, BRAKE, DRIFT, USE and RESET. Desktop mappings
are W/S, A/D, Space, E and R; Escape follows the back path. Presentation toggles
can hide touch drawings without disabling touch input. Held keyboard actions
must return to neutral after cancellation; AI never writes global input.

Settings route Master, Music, SFX, Voice, Ambience and Engine independently;
these are **audio buses, not imported audio events**. Mute, bus volumes, shadows
and touch visibility can be saved or cancelled. A save failure is visible.

`user://profile.0.json` and `.1.json` retain two checksummed generations.
Settings, selection, completed participation/wins and per-configuration best
times survive restart. These are port-side local equivalents, not recovered
campaign/unlock/reward logic. Interrupted writes fall back to a verified previous
generation; two corrupt generations or a newer schema are preserved read-only.
This is single-process persistence with flush/read-back, not an fsync/power-loss
or concurrent-writer guarantee. Only the latest 64 event IDs are deduplicated.
Unfinished races do not award completion and are not resumed after process death.

Character/kart art, arenas and campaign choices remain unavailable rather than
pretending to load missing content. Other recovered race routes are labelled
experimental; full race evidence currently covers Arlen Speedway only.

## Recovered data and traceability

`port/scripts/data/` contains generated definitions and tracks; do not edit
those modules by hand. All seven reproduce byte-for-byte from the committed
`work/assets/definitions/definitions.json` and `work/assets/tracks.json` using:

```sh
python3 tools/game_defs_to_gdscript.py work/assets/definitions/definitions.json
python3 tools/tracks_to_gdscript.py work/assets/tracks.json port/scripts/data/tracks.gd
```

The data includes 814 definitions across 55 classes and 16 decoded maps.
`game_db.gd` provides ID lookup; `handling.gd` holds handling profiles;
`tracks.gd` holds route knots/widths, spawn and pickup locations;
`catalog.gd`, `pickups.gd`, `modes.gd` and `misc.gd` hold their respective
recovered definitions. Case-insensitive ID resolution is covered by tests.

Recovery history and commands remain in [RE-FINDINGS](../docs/RE-FINDINGS.md)
and [RE-SETUP](../docs/RE-SETUP.md). The original DMG remains Git LFS tracked
and is not modified or bundled into runtime-evidence artifacts.

## Production-content gate

The [content-recovery tooling](../docs/CONTENT-RECOVERY.md) now has an authored
GLB fixture and actual Godot skin/animation import checks. This is not imported
Warped Kart Racers art: the scene still uses engineering geometry, and
`port/recovered/` stays ignored. Apply the explicit coordinate/material and
unsupported-feature gates before replacing any live race resource.
