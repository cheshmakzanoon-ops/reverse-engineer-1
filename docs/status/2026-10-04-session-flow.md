# Local session flow, persistence and HUD — 2026-10-04

Base: `d1be9c1c69e1217a15b7dacafb372d4fd4e4279f`, single `main`.
This is a verified port-side increment, **not a complete Android game**.
No DMG, recovered binary, generated data module, signing key or original media
was modified or added. The existing race scene remains independently testable.

## Implemented behavior

`GameSession` boots a title and provides main menu, validated race setup,
settings, cancellable loading, pause/resume/restart, results, rematch and menu
navigation. Selection reaches the actual race director and six-kart field.
Unsupported arenas are filtered out; invalid selections cannot fall back to an
unrelated circuit. Other recovered race routes remain labelled experimental.
The current front end explicitly reports missing original art/audio/content.

The world is PAUSABLE while the front end is ALWAYS-processing. Pause freezes
player/AI motion, race/countdown, pickups and item processing, preserves momentum,
cancels held touch and suppresses carried keyboard input until **every** relevant
action is neutral. Opposing held keys cannot bypass that latch. Drift cancellation
does not award a release boost. Focus gain never resumes automatically; back and
exit use explicit, tested navigation. Old race instances are freed, delayed loads
are cancelled and stale finish callbacks cannot finish a replacement race.

Six audio buses support independent levels, master mute and temporary lifecycle
mute. Returning from pause/results/exit removes temporary mute; cancelled settings
restore the saved values. These tests exercise AudioServer routing with Dummy
audio, not audible source events or native Android audio-focus interruptions.
Shadows and touch-drawing visibility are persisted presentation options. Hidden
touch drawings do not silently reappear on the next finger event.

`LocalProfile` preserves selection, settings, completion/win counts, per-track /
mode / lap-count / item-configuration best times and the latest 64 event IDs.
These are **local port-side equivalents**, not recovered campaign or unlock logic.
Two checksummed, versioned JSON generations keep the prior valid save while
writing the inactive slot. It verifies the write before committing in-memory
state, detects stale loaded writers, rejects invalid/non-finite data, recovers
from a truncated latest file and preserves two corrupt files/newer formats as
read-only. This is not a simultaneous-writer lock, encryption, anti-cheat,
filesystem fsync or power-loss durability claim.

Screenshot review also exposed existing HUD anchor/offset errors: speed was
rendering over race position and the countdown was not consistently centred.
The HUD now places those elements explicitly inside viewport/display-safe bounds.
UI screenshots cover 16:9, wider landscape and tablet-like windows; physical
cutout/density behavior remains a device gate.

## Red-to-green evidence and final tests

Initial profile and front-end tests failed before implementation. During actual
runtime testing, JSON numeric normalization, cancel-settings behavior, hidden
controls, opposing-key cancellation, temporary mute and HUD placement defects
were reproduced and repaired with regression assertions.

**28 Python tests pass**, including the pinned-engine template-installation test.
**463 Godot checks in 15 suites pass** using Godot
`4.7.2.stable.official.ed1daf0bf`:

| Suite | Passing checks |
|---|---:|
| Existing 12 suites | 338 |
| `test_local_profile.gd` | 32 |
| `test_session_flow.gd` | 70 |
| `test_session_race.gd` | 23 |
| Total | 463 |

The graphical session suite feeds real synthetic `InputEventScreenTouch` events
through the engine/window, rather than emitting Button.pressed signals. A second
finger can pause while the first holds GO. It verifies scrolling to touch targets,
settings persistence, layout, countdown pause, moving player/AI freeze, input
cancellation, lifecycle/back/restart, cancelled loads and repeated world disposal.
It requires Xvfb: headless has no window input callback, and must not be passed off
as a successful GUI event test. Screenshots use Mesa llvmpipe and Dummy audio;
that is neither a mobile GPU benchmark nor physical touch-latency evidence.

The new session-race suite uses the actual CharacterBody3D simulation and ordered
gates with an AI test driver for the player. It never assigns checkpoint/lap/
progress/finish state. The 1495.608 m Arlen course completes in 6444 simulation
frames; the player's actual crossing time is 99.737 s, first place; the event
closes at 104.367 s. Six results are presented, that measured time is saved,
a duplicate callback adds no record, rematch uses a new event ID and a fresh
application instance restores the result. It does not claim a human touch-driven
race. Existing clean and combat **three-lap** physical suites also pass again.

Import, a 360-frame headless front-end boot, and byte-identical regeneration of
all seven recovered data modules pass. All local source changes were covered by
the complete run, with the final two-finger input test separately rerun green.

```sh
GODOT_TEST_BIN=/path/to/Godot_v4.7.2-stable_linux.x86_64 \
  python3 -m unittest discover -s tests -p 'test_*.py' -v
python3 scripts/checked_process.py --timeout 240 -- godot --headless --path port --import
python3 scripts/checked_process.py --timeout 120 -- godot --headless --path port --quit-after 360
GODOT=/path/to/Godot_v4.7.2-stable_linux.x86_64 \
  bash scripts/test_godot.sh verification
# Optional standalone UI run, with screenshot capture:
KART_SCREENSHOTS="$PWD/verification/ui-screens" xvfb-run -a godot \
  --audio-driver Dummy --fixed-fps 60 --path port --script res://tests/test_session_flow.gd
```

## Android attempts and external gates

The debug APK and test AAB commands were executed for this source using
`scripts/android_build.py --target debug-apk` / `--target test-aab`, explicit
local JDK/Godot paths and new output paths. Both stop at dependency preflight:
SDK/ADB/build-tools/platforms and matching Android export templates are missing.
The release AAB command stops at the owner-controlled signing-credential gate.
**No APK, AAB, successful signing sidecar or emulator/device result was produced.**

Direct SDK download also fails because the container cannot resolve
`dl.google.com`; the connector download route did not retrieve the repository
metadata. The previous repair's Android run `37178027097`, package job
`111364653973`, was still queued when checked. A future successful CI artifact
must be tied to its source commit and inspected; a queued workflow is not build
verification. No physical phone is connected to this environment.

Original character/kart/track meshes, textures, skeletons/animations, VFX, UI art
and FMOD-derived audio events remain unimported. The local source/evidence
snapshot contains recovered JSON, not those original media bytes. A complete
vertical slice still needs materialized authorized AssetBundles/banks, a validated
conversion/import pipeline, actual Android export/runtime evidence and physical
touch testing. Battle modes, full content selection, campaign/unlocks, Android
low-memory/native audio-focus behavior and 60 FPS acceptance remain unverified
or unimplemented. No placeholder art or silent build is called complete.

## Exact changed files

- `README.md`, `port/README.md`, `.github/workflows/runtime.yml`
- `port/project.godot`, `port/scenes/frontend.tscn`
- `port/scripts/game_session.gd`, `port/scripts/game_session.gd.uid`
- `port/scripts/local_profile.gd`, `port/scripts/local_profile.gd.uid`
- `port/scripts/hud.gd`, `port/scripts/player_driver.gd`
- `port/scripts/race_main.gd`, `port/scripts/touch_controls.gd`
- `port/tests/test_local_profile.gd`, `port/tests/test_local_profile.gd.uid`
- `port/tests/test_session_flow.gd`, `port/tests/test_session_flow.gd.uid`
- `port/tests/test_session_race.gd`, `port/tests/test_session_race.gd.uid`
- `scripts/test_godot.sh`, this report
