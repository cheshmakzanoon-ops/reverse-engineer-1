# Verified original Addressables locations — 2026-10-05

Implementation: `03566a17a175f257234c8d1a850ce9025fd699c3`, repaired against
original-source observations in `6ad5edb4e86b05dcc73504dae955db0f922227f1`.
Changes remain on the single existing `main` branch. This increment implements
asset-location recovery, not original method-body recovery or imported game art.

## Original-source result

Original-source workflow **37390404124** passed on `6ad5edb`. Its artifact
**11380323338** was downloaded and independently SHA-256 verified before reading:
`5baf2f22ff591a0acf2e3341fe1e810d56aa1c9d9510dc03e7d5cf51fd74f10d`.
The 59,654-byte `addressables.json` report has SHA-256
`49ca99c115d5c08d5fab58a5338d5b953b0e9e09c2fc5c16cf44151ce1ef135c`.
A deduplicated metadata summary is retained in
[the evidence JSON](2026-10-05-addressables-recovery.json).

The workflow verified the exact original DMG, the extracted application's
276-file manifest, the original catalog, and every selected bundle's size/hash.
The catalog contains **7,648 keys, 8,699 locations and 44,737 dependency edges**.
All **16 Unity requirements** in the Arlen/Hank/Landry Longhorner/stock-wheels
slice now resolve to catalog locations backed by **14 distinct verified bundles**.
The seventeenth requirement, `event:/Music/KOH/KOH_Race_2`, is FMOD and remains
explicitly unresolved. No Unity object is substituted for an audio event.

Examples of source-backed location entries are Hank `4692`, kart `7889`, front
wheels `1508`, rear wheels `8633`, and Arlen scene entries `898`, `967`, `1535`
and `7955`. These are catalog indices, **not Unity serialized path IDs**.
The selected bundle sets and original internal IDs are retained in the JSON.

The first production run, **37389708335** at `03566a1`, was not a success:
parsing completed but six Unity requirements were rejected. Two GUID buckets
exposed multiple resource types, and four SceneInstance locations used
BundledAssetProvider rather than SceneProvider. The repair selects an exact,
unambiguous assembly-qualified requested type where valid, and validates scene
identity by the original SceneInstance type. Original provider IDs are retained,
not executed or silently changed. Ambiguous same-type and atlas-parent mappings
still fail. Forty-eight authored regressions cover the new stage, including
these production-discovered format differences.

## Implementation and validation

`tools/content_addressables.py` supplies a bounded, inert compact-catalog reader
and location resolver. `scripts/check_addressables.py` adds exact original-input
provenance checks. The separate `addressables-evidence.yml` workflow tests fixtures
before reading production input and uploads only metadata. Commands and supported
format boundaries are in [ADDRESSABLES-RECOVERY.md](../ADDRESSABLES-RECOVERY.md).

Local full-suite result: **373 tests collected, 372 passed, one skipped**. The
skip is the optional real-Godot template-installation test; the engine was not
installed in that local runtime. All **48 new-stage tests passed**. All seven
recovered-data modules regenerate byte-identically, and the five existing decoder
recipe modules remain byte-identical to the inspected base. Raw checkpoint
compatibility was not invalidated to add catalog lookups.

At implementation commit `6ad5edb`, hosted verify run **37390404170** passed.
Hosted runtime run **37390404609** also passed: project import, a 300-frame boot,
and all 16 Godot regression suites (**483 checks**, plus the separate one-check
GLB fixture-import gate). Its Python pass likewise reports 373 tests with the
one optional engine test skipped before engine installation.

Android packaging run **37390404025** subsequently completed successfully at the
same implementation commit. Package job **112034102198** passed export guards,
real-engine template installation, and export/verification of the ARM64 debug
APK, test-signed AAB and emulator APK. Emulator job **112036072686** also passed.
This is packaging and the existing launch/background/resume smoke test, not
proof of a visible Android frontend, a complete phone race, original art/audio,
performance, or physical ARM64-device operation. No Godot gameplay or rendering
implementation was changed by the Addressables recovery stage.

## Remaining gates

A verified Addressables location is not a bound serialized GameObject, sprite
subobject, usable mesh, converted material or animation. This check does not
complete the separate full raw-object catalog finalization. The existing
resumable finalizer was preserved rather than duplicated by this increment.

Next, use the verified locations and bundle identities to resolve AssetBundle
container/scene entries to actual serialized objects; complete and validate their
reference closures; resolve SpriteAtlas members; and inventory the selected
source shaders, rigs and animation formats before extending scene conversion.
Then import the real Arlen/Hank/kart content into the tested race. Original
method-body analysis, FMOD sample/event recovery, native behavioral comparison,
visible Android interaction and physical-device acceptance remain separate work.

Original media, fonts, catalog payloads and keys are not included in this public
evidence. All full-source/full-media/Godot-import completion flags remain false.
