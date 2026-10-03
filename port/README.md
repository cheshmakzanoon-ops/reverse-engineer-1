# Warped Kart Racers — Godot 4 / Android port

Target for the reverse-engineering effort in [`docs/RE-FINDINGS.md`](../docs/RE-FINDINGS.md).
The game code was recovered from a macOS IL2CPP binary; this project is the
reimplementation.

## Current state — a playable race on the recovered tracks

```
build/warped-kart-racers.apk     58 MB   signed debug APK
package   com.electricsquare.atlas.re   versionName 2.02
arch      arm64-v8a, x86_64
signing   APK Signature Scheme v2 + v3 (verified)
```

The default scene is a full race on **Arlen Speedway**, a track recovered from
the game's own map AssetBundle: a 1489.8 m racing line with the game's road
widths, 52 pickup boxes on their recovered spots, 696 respawn locations, and a
six-kart field where the AI drives the shipped `RaceKartAIDefinitionHard_NineAI`
profile on the `KartPhysicsHandlingRaceAI` handling profile.

Verified by execution, not assumed:

```bash
godot --headless --path . --import                       # clean, no script errors
godot --headless --path . --quit-after 1800              # runs, no runtime errors
for t in test_recovered_tuning test_game_db test_all_tracks test_race_integration; do
  godot --headless --path . --script "res://tests/$t.gd"
done                                                      # 184 checks, all passing
mkdir -p build
godot --headless --path . --export-debug "Android" build/warped-kart-racers.apk
/opt/android-sdk/build-tools/35.0.0/apksigner verify build/warped-kart-racers.apk
```

The APK ships 30 compiled scripts, including the full recovered data layer
(`data/game_db`, `data/handling`, `data/tracks`, `data/catalog`,
`data/pickups`, `data/modes`, `data/misc`) and every ported system.

## The recovered data layer

`scripts/data/` is generated from the game's shipped ScriptableObjects and map
bundles — never hand-edited:

| File | Contents |
|---|---|
| `game_db.gd` | all 814 definitions, indexed by id; `lookup()`/`resolve()` walk the graph |
| `handling.gd` | the 6 `KartPhysicsHandling` profiles, with curves and surface tables |
| `tracks.gd` | 16 tracks: control points, road widths, pickups, respawns, surfaces |
| `catalog.gd` / `pickups.gd` / `modes.gd` / `misc.gd` | karts, characters, loot tables, modes, the rest |

Regenerate from `work/assets/` with the `tools/dump_definitions.py`,
`tools/game_defs_to_gdscript.py`, `tools/build_tracks.py` and
`tools/tracks_to_gdscript.py` pipeline documented in the root `README.md`.

One quirk worth knowing: `GameDB.lookup()` is deliberately case-insensitive.
Five shipped chapters reference `IP_GENERIC` while the definition that actually
ships is `IP_Generic`, and the original game resolves the mismatch — so the port
does too, rather than "correcting" the recovered data.

## What is ported

`KartPhysicsHandling` — **all 93 recovered fields** are represented
(verified mechanically against `dump.cs`; one is renamed
`_driftBoostLevelsTable` → `drift_boost_levels`). Defaults come from the
generated `kart_physics_handling_data.gd`, which holds the game’s own values.

`DriftBoostLevel` — all 8 fields and the `DriftBoostType` discriminator, plus
`is_valid()` mirroring the recovered `IsValid()` (RVA 0x1256828). The three
levels are the game's own, read from the serialized `_driftBoostLevelsTable`
(`_timeToActivate` 2.0 / 4.2 / 6.9 s, `_driftBoostRatio` 0.24 / 0.38 / 0.52),
re-ordered smallest-first so a drift can be walked by ascending elapsed time.

`KartPhysics` behaviour — `kart.gd` implements the recovered control loop:
forward/reverse accel through `forward_accel_curve` (sampled by **absolute
speed**, as the recovered keyframes require), speed capping at
`speed_hard_cap`, slope compensation, speed-dependent yaw authority
(`rot_speed_from_kart_speed_curve`), drifting with reduced lateral grip and
level-based boost payout, boost timers, air gravity, ground alignment, and
chase camera.

## Recovering the values

The tuning lives in ScriptableObjects, not code, so it is read out of the
AssetBundle rather than decompiled:

```bash
python3 tools/unity_defs_to_gdscript.py \
  "work/dmg/Warped Kart Racers/.../definitions_assets_all_<hash>.bundle" \
  Atlas.Gameplay.Kart.KartPhysicsHandling KartPhysicsHandlingDefault \
  port/scripts/kart_physics_handling_data.gd
```

It uses **UnityPy**, not AssetRipper: AssetRipper 2.0 ships only a web GUI
(`--headless --port`, no batch export), whereas UnityPy reads a single 342 KB
bundle in seconds inside the existing venv and hands back the typed values
directly. The bundle has its Unity version string stripped, so the version from
`globalgamemanagers` is passed explicitly.

## The honesty boundary — read this

**Structure AND the primary tuning values are recovered.**

- **Recovered, exact:** every type, field and method name; every field offset;
  the class hierarchy; the enum discriminators; which curve drives what.
- **Recovered from the game’s own ScriptableObjects:** 78 scalar values and 5
  AnimationCurves (15 keyframes) from the `KartPhysicsHandlingDefault` profile,
  read out of `definitions_assets_all_*.bundle` by
  `tools/unity_defs_to_gdscript.py`. `_speedHardCap` is **55**,
  `_braking` **21**, `_tireGrip` **3.99**, `_boost` **0.8**,
  `_driftSpeedMin` **16** — the game’s numbers, not estimates. These were
  wildly wrong when guessed (the provisional `_boost` was 16 vs the real 0.8),
  which is exactly why they were marked `PROVISIONAL` until extracted.
- **PORT-SIDE, the only numbers still ours:** `steer_speed` (in the original it
  is folded into native code, not stored as a field), `gliding_speed`,
  `top_speed`, `top_acceleration`. Each is marked `# PORT-SIDE` in the source.
- **Not recovered:** `AnimationCurve` tangent *weights* — every keyframe uses
  Unity’s neutral 1/3, so they carry no information, and Godot 4’s `Curve` has
  no per-point weight setter. Dropping them is lossless.

Five handling profiles ship in the game and are all recoverable the same way —
`Default`, `Battle`, `Race150`, `RaceAI`, `Shooter`, `ShooterExtraSlow`.

### Verifying the values

```bash
godot --headless --path . --script res://tests/test_recovered_tuning.gd
# PASS: 34 checks
```

This test exists because a silent failure got through once: Godot’s
`Curve.add_point` clamps to `min/max_domain` and `min/max_value`, both of which
default to `0..1`. These curves are in absolute game units, so every keyframe
silently collapsed onto `(1,1)` — the project compiled, imported and ran with
**zero errors** while discarding all 15 recovered keyframes. Asserting on real
values, not just “it runs”, is the only way that surfaces.

## Six handling profiles

The game ships six `KartPhysicsHandling` profiles; only `Default` is wired into
the demo. The others regenerate by changing the profile id:

```bash
python3 tools/unity_defs_to_gdscript.py <bundle> \
  Atlas.Gameplay.Kart.KartPhysicsHandling KartPhysicsHandlingBattle \
  port/scripts/kart_physics_handling_battle.gd
```

| Profile | `_speedHardCap` | `_tireGrip` |
|---|---|---|
| Default | 55.0 | 3.99 |
| Battle | 55.0 | 3.9 |
| Race150 | 55.0 | 4.0 |
| RaceAI | 55.0 | 4.2 |
| Shooter | 40.0 | 3.9 |
| ShooterExtraSlow | 32.0 | 3.9 |

## Controls

| Action | Key |
|---|---|
| Steer | `A` / `D` or `←` / `→` |
| Accelerate / brake | `W` / `S` or `↑` / `↓` |
| Drift | `Space` (needs speed + steering input) |
| Respawn | `R` |

Touch controls are not implemented yet — that is required for a real Android
release and is the obvious next step alongside AssetRipper.

## Layout

```
port/
├── project.godot                  input map, ETC2/ASTC (required for Android)
├── export_presets.cfg             Android preset, arm64-v8a + x86_64
├── scripts/
│   ├── kart_physics_handling.gd    the 93 recovered fields
│   ├── kart_physics_handling_data.gd  GENERATED: real values + curves
│   ├── drift_boost_level.gd        DriftBoostLevel, 3 recovered levels
│   ├── kart.gd                     KartPhysics behaviour
│   └── main.gd                     procedural track + scene assembly
├── tests/
│   └── test_recovered_tuning.gd   asserts the recovered values survived
└── build/warped-kart-racers.apk    output
```

The track is built procedurally so the APK is self-contained before any art is
extracted; the 18 recovered race tracks
(`map_race*_scenes_all_*.bundle`) replace it once AssetRipper runs.

## Toolchain note

Godot 4.7.2 + Android SDK platform 35 / build-tools 35.0.0 were installed
locally. **Export templates were installed for Android only** (426 MB instead
of ~2.5 GB) — extract other platforms from the `.tpz` if a desktop build is
wanted. The **NDK was deliberately skipped** (2.4 GB): it is only needed for
native/GDExtension builds, and this project is pure GDScript.