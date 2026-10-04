# Godot 4 / Android reconstruction

This is an **incomplete engineering reconstruction**, not the original source
code or a finished Android game. The [root README](../README.md) and
[current item evidence](../docs/status/2026-10-03-items.md) supersede historical
prototype APK and completion claims.

## Verified core race

Godot 4.7.2 headless tests drive six karts through three Arlen Speedway laps,
188 ordered finite gates per lap, with actual CharacterBody3D motion rather
than edited lap counters. Tests exercise countdown locking, forward crossing,
ranking, finish times, automatic fall recovery, results and stopped finishers.
The current increment passes 338 Godot checks and 13 Python tests locally.

The source snapshot still uses placeholder kart/road visuals and has no
original audio. Pickup contacts, per-kart inventory, boosts, shields, projectiles, hazards and
AI item use now have behavioral tests. Pause/restart/menu/save flow,
original-content import and battle rules remain incomplete. Other maps have
data/geometry checks, not this full-race proof. AI, interpolation, gates,
recovery and physics include explicitly labelled port-side implementations;
native behavior equivalence is not established.

## Reproduce from the repository root

```sh
python3 -m unittest discover -s tests -p 'test_*.py' -v
python3 scripts/checked_process.py --timeout 240 -- godot --headless --path port --import
for test in port/tests/test_*.gd; do
  name="$(basename "$test" .gd)"
  python3 scripts/checked_process.py --timeout 180 --require 'PASS:' -- \
    godot --headless --fixed-fps 60 --path port --script "res://tests/$name.gd"
done
```

Fixed physics time accelerates simulation; it is **not** an Android 60 FPS
benchmark. No physical Android device or emulator result is claimed here.

The local Android export of this revision failed because SDK, export templates
and editor paths were missing. Historical APKs do not prove a build of this
source. Release credentials must stay out of Git; public distribution of
original game art, music, voices and trademarks requires authorization.

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
