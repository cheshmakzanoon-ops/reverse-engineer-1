# Warped Kart Racers v2.02 — Reverse Engineering Findings

Target: `games/Warped.Kart.Racers.v2.02.dmg` (1.19 GB) — Electric Square's macOS
TestFlight build, bundle id `com.electricsquare.atlas-testflight`.

**Goal:** recover enough of the game to reimplement it in Godot 4 and ship an
Android APK.

**Status:** the game's *entire managed API surface* is recovered and
cross-validated, and the kart handling model has been **ported to Godot 4 and
exported as a signed Android APK** (`port/`, see [§5](#5-the-godot-port-apk-and-what-remains)).
Method bodies for core kart physics are decompiled. Asset and audio extraction,
real tuning values, and touch controls are **not** done — see
[Limitations](#limitations).

---

## 1. What the binary actually is

| Property | Value |
|---|---|
| Engine | **Unity 2021.3.56f2** (LTS) |
| Scripting backend | **IL2CPP** (C# → native ARM64; no assemblies ship) |
| Architectures | Universal binary: `x86_64` + `arm64` |
| Renderer | Universal Render Pipeline (URP) |
| Bundle id | `com.electricsquare.atlas-testflight` |
| Version | 2.02 (build 1620) |
| Min macOS | 12.0, built with Xcode 16C5032a / macOS SDK 15.2 |

Contents of the extracted `.app`:

```
Contents/MacOS/Warped Kart Racers              82 KB   (thin launcher)
Contents/Frameworks/GameAssembly.dylib        274 MB   <-- ALL game logic
Contents/Frameworks/UnityPlayer.dylib          56 MB   Unity runtime
Contents/PlugIns/libServices.dylib            12 MB   netcode (Electric Square)
Contents/PlugIns/fmodstudio.bundle                    FMOD audio runtime
Contents/PlugIns/resonanceaudio.bundle                 Resonance Audio
Contents/Resources/Data/il2cpp_data/Metadata/global-metadata.dat   11 MB
Contents/Resources/Data/StreamingAssets/               444 AssetBundles + 9 FMOD banks
```

1.5 GB across 276 real files. (The DMG carries ~300 extra `*:com.apple.macl`
xattr sidecar files — they are macOS provenance blobs, not game data, and are
skipped.)

### The IL2CPP consequence

There is no `Assembly-CSharp.dll` to decompile — `ScriptingAssemblies.json`
lists 123 assemblies, but IL2CPP compiles them all into `GameAssembly.dylib`.
The only thing standing between the binary and readable C# is
`global-metadata.dat`, which still holds every type name, method signature,
field name and offset. Recovering that metadata is the single highest-value
step, and it is not guesswork: it is the original build's own metadata.

---

## 2. Recovery achieved

### 2.1 Type/signature recovery (complete)

`Il2CppDumper` against `GameAssembly.dylib` + `global-metadata.dat`:

```
Metadata Version: 31 → Il2Cpp Version: 31 (searched back to v29)
```

Produced, in `work/analysis/il2cpp/`:

| Artifact | Size | What it is |
|---|---|---|
| `dump.cs` | 30 MB | every class, method signature, field + offset |
| `il2cpp.h` | 82 MB | full C++ headers for the generated types |
| `script.json` | 90 MB | 194,548 method → RVA mappings |
| `DummyDll/` | 18 MB | 101 stub assemblies, ILSpy-readable |

Two independent counts of the same recovered metadata:

- **`12,405` types** across 101 assemblies, from the `DummyDll/*.dll` metadata
  (`il2cpp_triage.py asm --dlls`). This is the authoritative total.
- **`8,088` types**, plus **11,817 methods**, **8,429 fields**, **690 enum
  members** and **956 types with a known RVA**, from `tools/il2cpp_triage.py`
  parsing the 820k-line `dump.cs`.

The ~4,300-type gap is a limitation of the flat-text parser, not missing data:
`dump.cs` renders generics, compiler-generated closures, and some nested types
in forms the type regex does not match. `DummyDll` has them all. Where they
agree (naming, RVAs, field offsets) they agree exactly.

### 2.2 Game code layout

Game logic lives in **`Atlas.dll`** — **1,407 types, second only to mscorlib**
in the whole binary. `Assembly-CSharp.dll` is almost empty (12 KB); nearly
everything is in the `Atlas` namespace tree.

| Namespace | Types | Area |
|---|---|---|
| `Atlas.UI.Screens` | 115 | menus, kart select, results |
| `Atlas.UI` | 105 | HUD, indicators, metagame |
| `Core.Services` | 92 | save, cloud, platform services |
| `Atlas.Gameplay.Kart` | 89 | **kart physics — the core** |
| `Core.Networking` | 71 | netcode |
| `Atlas.Definitions` | 70 | ScriptableObject data definitions |
| `Atlas.Gameplay` | 62 | race/battle loop |
| `Atlas.Contexts` | 35 | game state containers |
| `Atlas.Gameplay.Usables` | 26 | pickups / power-ups |
| `Atlas.Geometry` | 24 | track splines, surfaces |
| `Atlas.Stats` | 18 | kart stat definitions |
| `Atlas.League` | 15 | metagame progression |

Third-party libraries present (all open source, reimplementable or
substitutable): DOTween, Cinemachine, UniRx.Async, FMOD, Newtonsoft.Json,
FlatBuffers, Input System, Addressables, Unity Localization, Burst/Jobs.

### 2.3 The kart handling model — the port's centre of gravity

`Atlas.Gameplay.Kart.KartPhysicsHandling` is a `Definition` (ScriptableObject)
with **93 tuning fields**, every one recovered with its exact offset. This is
the driving model, field for field:

```
_forwardAccelCurve / _reverseAccelCurve     AnimationCurve   +0x20/+0x28
_speedHardCap / _verticalSpeedCap            float            +0x30/+0x34
_braking / _driftSpeedMin                    float            +0x38/+0x3C
_slopeCompensation{Up,Down,AngleStart,AngleMax}  float        +0x40..+0x4C
_rotSpeedFromKartSpeedCurve                  AnimationCurve   +0x50
_rotSpeedFromSteerAngleCurve                 AnimationCurve   +0x58
_tireGrip / _turnSpeedCompensation           float            +0x68/+0x6C
_steerMinToDrift / _rotSpeedDriftRatio       float            +0x70/+0x74
_tireGripDrift / _tireGripDriftMaxSteer      float            +0x78/+0x7C
_driftSteer{Min,Max,Neutral}Default          float            +0x80/+0x84/+0x88
_boost / _boostDuration / _boostSpeedOffset  float            +0xA4..+0xAC
_hopBoost{Ratio,Duration,SpeedThreshold,GravityScale}         +0xB4..+0xC0
_driftBoostLevelsTable                       DriftBoostLevel[] +0xD0
_boosts, kart-to-kart bounce, kart-to-static bounce,
collision separation thresholds, crash speed threshold,
lostControlTimerOnCrash, _airRotSpeedRatio     float        +0xF0..+0x144
_glidingAccelCurve / _glidingYawSpeed        AnimationCurve/float +0x148/+0x154
```

Forces are split into dedicated config structs — a per-axis force model:

| Struct | Fields |
|---|---|
| `KartPhysicsLngForceConfig` | `BrakingForce`, `EngineForceAtTopSpeed`, `EngineForceMax`, `EngineForceCurve`, `ReverseForceCurve`, `GlidingForceCurve` |
| `KartPhysicsLatForceConfig` | lateral (grip / slide) |
| `KartPhysicsVertForceConfig` | vertical (suspension) |
| `KartPhysicsGlidingForceConfig` | glider |
| `KartPhysicsRotForceConfig` | yaw/pitch/roll |
| `KartPhysicsAeroConfig` | drag / downforce |
| `KartPhysicsBodyConfig` | mass, collider setup |
| `KartSuspensionConfig` | spring, damper, travel |
| `KartHandlingSurfaceType` | per-surface grip modifiers |

Drifting is a level table, not a boolean — `DriftBoostLevel`:

```
_driftBoostType (_driftBoostType)  +0x0
_timeToActivate                    +0x4   seconds of drift before it fires
_driftBoostRatio                   +0x8   speed multiplier
_driftBoostDuration                +0xC
_driftSteer{Min,Max,Neutral}       +0x10/+0x14/+0x18
```

Supporting gameplay types: `KartPhysics` (rigidbody + colliders + raycast),
`KartInputManager`, `KartAI` (+ `KartAIInputData`, `KartAIUsablesParameters*`),
`KartTriggerSpinoutType`, `KartPhysicsCollisionInfo`, `KartKinematicChangeReason`,
`Usables/` (power-ups incl. `BoostUsable`), `FTUE/` (tutorial), `Atlas.League`.

### 2.4 Ghidra decompilation

A GameAssembly has 194,548 managed methods; the BCL (120,775) and Unity
(31,575) are 78% of it and are not the port's problem. `analyzeHeadless` here
runs at roughly **0.25 functions/s**, so bulk decompiling all 13,592
game-assembly methods would take about 15 hours on this box. `DecompileIl2Cpp.java`
therefore applies Il2CppDumper names and decompiles only a named subset.

Verified working on **22 `KartPhysics` methods: 22 written, 0 failed.**

Recovered bodies are real, and cross-validate the metadata. Example,
`KartPhysics.RayCastRBToGround` decompiles to:

```c
iVar3 = func_0x027e2c00(uVar18, uVar2, uVar28, uVar19, uVar25, uVar29,
                        0x40200000, &uStack_460, uVar9, 1, 0);
```

`func_0x027e2c00` is `Physics.Raycast`, and `0x40200000` is `2.5f` — the ground
probe distance. Other recovered constants: `0xbf800000` = −1.0f, `0x3f800000` =
1.0f. Struct loads like `*(undefined4 *)(param_4 + 0x5d0)` line up with the
offsets in `dump.cs`, confirming both the metadata recovery and the decompiler
agree.

**The metadata cross-check that matters:** `AntennaVisualBehaviour.get_WibbleBehaviour`
decompiles to `return *(undefined8 *)(param_1 + 0x20);` and `dump.cs` lists
`_wibbleBehaviour` at `+0x20`. Independent recovery paths agreeing on field
offsets means the reconstruction is sound, not merely plausible.

---

## 3. Content inventory (located, not yet extracted)

**444 Unity AssetBundles** in `Data/StreamingAssets/aa/StandaloneOSX/`, grouped:

| Category | Count | Notes |
|---|---|---|
| gliders | 162 | cosmetic gliders |
| characters | 146 | 2 per character, driver + kart |
| karts | 52 | |
| map | 32 | 18 race tracks + 8 battle maps |
| videos | 20 | H.264 splash videos (16:9 and 4:3) |
| frontend / ui / fonts / definitions / localisation / global / maps / wheels / vfx / usables / boosts / antennas | 2 each | |

Race tracks: `racetheswall`, `racequahogtour`, `racearlensuburbs`, `racearlenspeedway`,
`raceterrykorvohouse`, `racesmithsdreamworld`, `racesharksnest`, `racestewiestimachine`,
`racelangleyfalls`. Battle maps: `battleplanetshlorp`, `battlebobsfunland`,
`battleemperorzing`, `battlelonestargayrodeo`.

**9 FMOD banks** (not FSB — FMOD Studio `.bank` containers, so `vgmstream` will
not read them; they need FMOD itself or an FMOD-aware extractor):

| Bank | Size |
|---|---|
| `Bank_Music.bank` | 154 MB |
| `Bank_VO.bank` | 142 MB |
| `Bank_UI.bank` | 42 MB |
| `Bank_Gameplay_SFX.bank` | 20 MB |
| `Bank_Karts.bank` | 11 MB |
| `Bank_Ambiences.bank` | 6.4 MB |
| `Bank_Collisions.bank` | 2.8 MB |
| `Bank_Master.bank` + `.strings.bank` | 63 KB |

Plus 18 `.resS` resource files and `level0`/`level1` scenes.

---

## 4. Reproducing this

```bash
# 0. Extract the DMG (already present in work/dmg/; re-extract with:)
#    Modern LZFSE DMGs defeat dmg2img 1.6.7 -- use tools/udif_extract.py.

# 1. Carve one architecture out of the universal binaries
python3 tools/macho_slice.py \
  "work/dmg/Warped Kart Racers/Warped Kart Racers.app/Contents/Frameworks/GameAssembly.dylib" \
  work/analysis/slices/GameAssembly.arm64.dylib --arch arm64

# 2. Recover the managed API surface  -> dump.cs, il2cpp.h, script.json, DummyDll/
DOTNET_ROLL_FORWARD=LatestMajor dotnet work/tools/Il2CppDumper/Il2CppDumper.dll \
  work/analysis/slices/GameAssembly.arm64.dylib \
  "work/dmg/Warped Kart Racers/Warped Kart Racers.app/Contents/Resources/Data/il2cpp_data/Metadata/global-metadata.dat" \
  work/analysis/il2cpp
# NOTE: set "RequireAnyKey": false in Il2CppDumper/config.json first, or it
#       blocks forever in a non-interactive terminal.

# 3. Navigate the recovered code
python3 tools/il2cpp_triage.py summary work/analysis/il2cpp/dump.cs
python3 tools/il2cpp_triage.py type   work/analysis/il2cpp/dump.cs KartPhysicsHandling
python3 tools/il2cpp_triage.py find   work/analysis/il2cpp/dump.cs 'Drift' --kind class
python3 tools/il2cpp_triage.py asm    --dlls work/analysis/il2cpp/DummyDll

# 4. Decompile method bodies with real C# names (Ghidra 12)
python3 - <<'EOF'   # build a chunk of methods to decompile
import json; ms=json.load(open("work/analysis/ghidra/game_methods.json"))["ScriptMethod"]
json.dump({"ScriptMethod":[m for m in ms if "KartPhysics." in m["Name"]][:22]},
          open("work/analysis/ghidra/chunks/phys.json","w"))
EOF
/opt/ghidra/support/analyzeHeadless work/analysis/ghidra proj \
  -import work/analysis/slices/GameAssembly.arm64.dylib \
  -processor AARCH64:LE:64:v8A -analysisTimeoutPerFile 45     # import+analyse
/opt/ghidra/support/analyzeHeadless work/analysis/ghidra proj \
  -process GameAssembly.arm64.dylib -noanalysis \
  -scriptPath tools/ghidra-scripts \
  -postScript DecompileIl2Cpp.java work/analysis/ghidra/chunks/phys.json \
             work/analysis/ghidra/physics.c                     # decompile
```

Read the decompiled output, not just its existence: `analyzeHeadless` exits 0
even when a `-postScript` fails to compile. Check the log for `ERROR` and
confirm the `.c` is non-empty — see `docs/RE-SETUP.md`.

### New tools added

- **`tools/macho_slice.py`** — dependency-free fat-Mach-O slicer. Ghidra and
  Il2CppDumper both want one architecture, and both halves cost ~130 MB of disk
  and analysis time each. Handles 32- and 64-bit fat headers, `--list` mode.
- **`tools/il2cpp_triage.py`** — parses `dump.cs` (800k lines, tab-indented,
  `// TypeDefIndex:` trailers) into a queryable index;
  `summary`/`ns`/`asm`/`type`/`find`. Correctness-checked against a known type.
- **`tools/ghidra-scripts/DecompileIl2Cpp.java`** — applies Il2CppDumper names
  and decompiles only game-assembly methods, matched to functions by **address**
  (names collide after sanitising; Ghidra appends disambiguators).

---

## 5. The Godot port, APK, and what remains

The managed surface being recovered turns the port into translation rather
than investigation. That has been carried as far as the recovered data allows.

### 5.1 What is built

`port/` is a Godot 4.7.2 project that builds and runs:

```
build/warped-kart-racers.apk     55 MB   signed debug APK
package   com.electricsquare.atlas.re   versionName 2.02
arch      arm64-v8a, x86_64
signing   APK Signature Scheme v2 + v3 (apksigner verify: VERIFIED)
```

Verified by execution: clean headless import, 120 frames with no runtime
errors, successful export, `aapt2 dump badging` and `apksigner verify` both
pass, and the APK's class registry lists the ported classes.

- **`KartPhysicsHandling`** — **all 93 recovered fields**, verified mechanically
  against `dump.cs` (one is renamed `_driftBoostLevelsTable` →
  `drift_boost_levels`). Original camelCase names and their recovered offsets
  are kept in comments so the file stays diffable against `dump.cs`.
- **`DriftBoostLevel`** — all 8 fields plus the `DriftBoostType` discriminator
  and `is_valid()`, mirroring the recovered `IsValid()` at RVA 0x1256828.
- **`kart.gd`** — the `KartPhysics` behaviour: accel through
  `forward_accel_curve`, capping at `speed_hard_cap`, slope compensation,
  speed-dependent yaw authority, drift with reduced lateral grip and
  level-based boost payout, boost timers, air gravity, ground alignment.

### 5.2 The boundary — what is and isn't the game's own

Every **name, offset, hierarchy and enum** is exact. The **primary tuning
values** are now recovered too: 78 scalars and 5 AnimationCurves from the
`KartPhysicsHandlingDefault` ScriptableObject. What is left are four
`# PORT-SIDE` constants (`steer_speed`, `gliding_speed`, `top_speed`,
`top_acceleration`) that exist only in the port, because the original either
folds them into native code or sets them at runtime and never serializes them.

Curve tangent *weights* are dropped, losslessly: every keyframe uses Unity's
neutral 1/3, and Godot 4's `Curve` has no per-point weight setter.

### 5.3 Remaining work

1. **Art + audio assets** — unpack the other 443 AssetBundles (UnityPy or
   AssetRipper). The 18 recovered `map_race*` bundles replace the procedural
   test track.
2. **Wire the other five handling profiles** to Battle / Race150 / Shooter
   modes — the data is already extractable.
3. **Touch controls** — required for a real Android release; currently
   keyboard/gamepad only.
4. **FMOD banks** → Ogg/WAV for Godot.
5. **Bulk Ghidra decompilation** of the remaining ~13,570 game methods.

### 5.4 Recovering the tuning values

The tuning is **not in the code** — it lives in `Definition` ScriptableObjects
serialized inside `definitions_assets_all_*.bundle` (342 KB). `AssetRipper 2.0`
ships only a web GUI (`--headless --port`, no batch export), so **UnityPy** was
used instead: it reads one bundle in seconds inside the existing venv and
returns typed values directly. The bundle has its Unity version string stripped
by the IL2CPP build, so the version from `globalgamemanagers` is passed
explicitly.

`tools/unity_defs_to_gdscript.py` turns a named profile into
`kart_physics_handling_data.gd` — 78 scalars plus all 15 curve keyframes with
their tangents.

**A silent trap worth recording.** Godot's `Curve.add_point` *clamps* each point
to `min_domain..max_domain` and `min_value..max_value`, both defaulting to
`0..1`. These curves are in absolute game units (time to 47, values to 40), so
every keyframe collapsed onto `(1,1)` — while the project still compiled,
imported and ran with **zero errors**, silently discarding all 15 recovered
keyframes. It surfaced only because the test asserts real values rather than
just “it runs”. `port/tests/test_recovered_tuning.gd` now guards this.

Full detail, including the honest caveats, is in
[`port/README.md`](../port/README.md).

---

## Limitations

Honest about what is **not** done:

- **Bulk decompilation.** 22 methods decompiled and verified; 13,592 game
  methods remain. This box has **2 cores / 3 GB RAM**, and Ghidra managed
  ~0.25 functions/s. Bulk recovery needs a bigger machine and hours.
- **Ghidra auto-analysis is incomplete.** `-analysisTimeoutPerFile 45` cut it
  off after "Disassemble Entry Points"; only 428 functions were pre-defined.
  Bodies recovered via `createFunction` are therefore shallower than a full
  analysis would give — good enough to read an algorithm, not enough to
  guarantee register-level fidelity. On a larger box, drop the timeout.
- **Kart tuning values ARE now recovered.** The 78 scalars and 5 curves (15
  keyframes) of `KartPhysicsHandlingDefault` were read out of
  `definitions_assets_all_*.bundle` with UnityPy by
  `tools/unity_defs_to_gdscript.py`, and asserted by
  `port/tests/test_recovered_tuning.gd`. The earlier placeholder numbers were
  badly wrong — `_boost` is **0.8**, not 16; `_speedHardCap` is **55**, not 42.
- **Only 1 of 6 handling profiles is wired in.** `Default` is used; `Battle`,
  `Race150`, `RaceAI`, `Shooter` and `ShooterExtraSlow` are equally extractable
  by changing the profile id, but are not yet wired to game modes.
- **No art/audio assets extracted.** The 444 AssetBundles beyond
  `definitions_*` have not been unpacked, so the port uses a procedural test
  track and primitive geometry rather than real assets.
- **No audio extracted.** FMOD `.bank` is not FSB; needs an FMOD-aware path.
- **No touch controls.** The APK is keyboard/gamepad only, so it is not yet
  playable on a phone.
- **Godot toolchain is installed locally, not reproducibly.** Godot 4.7.2 +
  Android SDK platform 35 / build-tools 35.0.0 are present, but the **NDK was
  deliberately skipped** (2.4 GB — only needed for native/GDExtension builds)
  and **export templates were installed for Android only** (426 MB vs ~2.5 GB)
  to fit the disk budget. `scripts/install-re-tools.sh` still installs the full
  set, so a fresh machine gets the NDK.
- **The DMG was already extracted** by a previous session into `work/dmg/`;
  this session verified the extraction (1.5 GB, 276 files) rather than
  redoing it. The `dmg2img`→`hpmount` path in the README does not apply —
  `tools/udif_extract.py` is the correct path for this LZFSE DMG.
- **`dump.cs` text parser is lossy** (~8,088 of 12,405 types, §2.1). Use the
  `DummyDll` assemblies for anything the parser misses.