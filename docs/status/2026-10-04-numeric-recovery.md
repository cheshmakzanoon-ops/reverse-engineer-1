# Lossless raw numeric recovery — 2026-10-04 (America/Halifax)

Base: `25202ff77c461add4a728b394fc8d634a5a91bff`, single `main`.
This increment fixes **raw evidence preservation**, not curve interpolation,
particle behavior, character animation, original C# recovery or a finished port.
No original media, private keys, generated game data modules or runtime game
scripts are added or changed. The preceding key-only commit obtained the existing
authorized input; this report concerns the actual decoder repair that follows it.

## Original-source diagnosis

The owner's DMG was received using private-source workflow `37245830720`.
All six encrypted ZIP digests were checked before local decryption. The DMG
matched 1,190,250,225 bytes and SHA-256
`4720e9fbaabd6d39bceac362e271e24e9c08d8f549191f70bad34e6d5c28b6be`.
The existing 7-Zip path extracted 276 application files (1,596,079,924 bytes),
including 222 regular Unity bundles. Original bytes were never modified.
The universal Mach-O and metadata-v31 files were hashed separately; identifying
them is not recovering their original high-level method implementations.

The failing bundle is
`map_racesmithsdreamworld_scenes_all_55c2ac0e72c14e09480332ee0d5ca96b.bundle`,
39,196,611 bytes, SHA-256
`e254fe6859817590b136eddd4000adf36d2085e5a547e359f440d8bb49fb1c35`.
Direct UnityPy reads reproduced the old encoder's failure on three objects:

| Serialized file / path ID | Type | Actual non-finite fields |
|---|---|---|
| `CAB-c67847e9f27a96c74b562fb5b4888dfe` / `3109` | ParticleSystem | Size curve key 1 `outSlope`, key 2 `inSlope` |
| Same file / `3113` | ParticleSystem | Same two size-curve slope fields |
| `CAB-b86a964094d1509387980b1adb8d7ba2.sharedAssets` / `10` | MonoBehaviour | `/m_Clips/1/m_PostExtrapolationTime` |

All five fields decode as positive infinity (`7ff0000000000000` in the parsed
Python binary64 representation). The old finite-only serializer rejected the
whole typetree. This establishes the data and the failure, **not** the meaning
of those fields in the original runtime. No infinity was changed to zero, a
large finite number, a constant curve, or an unlimited-duration implementation.

## Implemented repair and boundaries

`encode_raw_tree` / `decode_raw_tree` retain non-finite parsed numbers using an
explicit `$float64` hexadecimal tag. Finite data retains the existing encoding,
including signed zero, exact large integers and byte arrays. Reserved source
keys are escaped rather than confused with codec tags. The original object
`.bin` remains the authority for source byte width and pre-decoder NaN payloads.

Each affected record now carries `numeric_specials` with exact field paths,
categories and parsed bits. Catalog verification recomputes those diagnostics
and rejects missing, duplicated or forged entries. Raw reference traversal still
visits the complete typetree; missing/ambiguous pointers remain explicit errors.

The conversion APIs `encode_tree` / `decode_tree` stay finite-only. Bare JSON
NaN/Infinity tokens, malformed numeric tags, non-finite decoded vertices and
uninterpreted special metadata in `SceneReader` are rejected. Raw mesh bone-bound
metadata can therefore be retained alongside independently finite decoded mesh
streams, without claiming the mesh is fully assembled or renderable. Applying
component-specific semantics remains a separate, evidence-backed converter task.

Decoder recipe hashes change with this repair, so old raw-serializer checkpoints
must not be relabelled as compatible. New checkpoints preserve the tagged raw
records through the existing object/bundle persistence mechanisms.

## Real complete-bundle test

A fresh actual `read_bundle` run processed **11,564 Smith's Dreamworld records**
with **zero bundle/object decoder errors** and wrote **23,227 artifact files**.
That is three more typetree files than the prior 23,224-artifact result. The
three known objects' original raw-byte hashes stayed identical, all five raw
numeric fields round-tripped, and the strict runtime decoder still rejected
their special metadata. Their conversion state remains `raw-only`.

The measured local decode took 55.908 seconds. This is a reproduction observation,
not an Android performance measurement or promised future execution budget.
UnityPy used the configured `2021.3.56f2` fallback where bundle version data was
stripped. The fallback is not independent proof of the original Unity version.

The [metadata-only JSON evidence](2026-10-04-numeric-recovery.json) contains exact
object/source hashes and field diagnostics, not original meshes, pixels or audio.

## Full inventory result

A 900-second bounded full-source attempt sealed **194 of 222 bundles** and
retained **237,217 object records**, including **2,738 decoded mesh records** and
**800 decoded image records**. It recorded 1,530 objects containing 239,456 tagged
numeric fields (many in mesh bone bounds). These are file-scoped records, not a
claim of globally deduplicated asset counts or production Godot integration.

Six Texture2D image-decoding errors in the fonts bundle remain explicit. Direct
source inspection confirms those six records have 0x0 dimensions, zero image
bytes and empty stream paths. No font-atlas generation semantics are assumed,
and no pixels are fabricated for those serialized empty records. The
attempt exhausted its budget in Planet Shlorp while decoding a Texture2D after
1,996 completed objects, leaving 28 bundles pending. The checkpointed output was
retained and a same-recipe resume was executed. This publication's bounded-run
snapshot is **not a finalized full catalog**: global reference resolution is not
established, and an incomplete bundle is not counted as recovered. The known
Smith's Dreamworld numeric regression above was tested independently in full.

Full catalog completion, the six image errors, dependency binding and
source-specific runtime conversions must be resolved separately. The original
font files and all other raw/decoded game media remain private. The attached
metadata snapshot identifies the exact receipts and remaining failures; it does
not claim the game contains these resources at runtime.

## Regression and runtime verification

**196 Python tests pass**, including 20 numeric tests. The initial 19 tests
were run red before implementation; a twentieth test was added after observing
special bone bounds in production mesh records. It checks that finite decoded
vertices are retained while full scene conversion remains conservative. Test
fixtures are authored and do not embed the game's art or binaries.

**483 Godot checks in all 16 suites pass**, including clean/combat three-lap
physical races, real graphical synthetic-touch UI tests, local saves and authored
GLB skinning/animation import. Project import, a 360-frame frontend boot and
byte-identical regeneration of all seven recovered-data modules also pass.
Both edited workflow YAML files and their nine embedded shell blocks parse.
The numeric suite is explicitly included in source recovery and verification CI.
No failed tests were suppressed or skipped in the final local run.

```sh
GODOT_TEST_BIN=/path/to/godot python -m unittest discover -s tests -v
python scripts/checked_process.py --timeout 240 -- godot --headless --path port --import
python scripts/checked_process.py --timeout 120 -- godot --headless --path port --quit-after 360
GODOT=/path/to/godot bash scripts/test_godot.sh verification
python tools/recover_content.py inventory "$EXTRACTED_DATA" "$NEW_CATALOG" \
  --checkpoints "$PRIVATE_CHECKPOINTS" --bundle-timeout 120 --budget 900 \
  --progress-report "$REPORTS/inventory-progress.json"
# After a bounded interruption, keep source/code unchanged and add --resume.
# Raw outputs and checkpoints are private. Never upload them unencrypted.
```

No new Android export, emulator or physical-phone test was run for this
source-decoder-only change. Earlier hosted engineering APK evidence is not a
new release, and neither packaging nor passing Godot tests proves original-content
parity. Production scene/material assembly, animation semantics, FMOD events,
all-content gameplay and Android-device acceptance remain separate work.
