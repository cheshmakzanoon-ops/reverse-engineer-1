# Source-gated content recovery

This pipeline is a **recovery/conversion foundation**, not a recovered Arlen
Speedway production scene. The original DMG has since been received, hash-verified
and extracted. Real Smith's Dreamworld decoding is recorded in
[the numeric-recovery evidence](status/2026-10-04-numeric-recovery.md).
Authored GLB fixtures establish importer behavior, not production scene fidelity.
Full catalog/reference completion and playable original-content integration remain
separate gates; successfully retaining raw objects does not pass either gate.

Keep original media, raw object bytes, fonts, converted game art and audio local.
Do not include them in the public repository or CI artifacts without a separate
content-authorization decision. `port/recovered/` is ignored. The existing game
still uses its engineering visuals; this increment does not replace them.

## 1. Obtain and verify the real input

From a network-equipped clone with Git LFS configured:

```sh
git lfs pull --include='games/Warped.Kart.Racers.v2.02.dmg'
python3 tools/recover_content.py verify-dmg games/Warped.Kart.Racers.v2.02.dmg
```

The source gate requires **1,190,250,225 bytes** and SHA-256
`4720e9fbaabd6d39bceac362e271e24e9c08d8f549191f70bad34e6d5c28b6be`.
An LFS pointer is rejected, not mistaken for recovered game bytes. No command
modifies the DMG. Extract the verified input with the existing documented
`tools/udif_extract.py` / current 7-Zip path in `RE-SETUP.md`; preserve all of
`Contents/Resources/Data`, especially StreamingAssets, catalogs, bundles and
resource streams. Extracted-data inventory hashes are not independently proof
that those files came from the verified DMG: retain the extraction log too.

Install the optional parser in an isolated Python environment. Python 3.10+ is
required by the stdlib tools; this increment was tested on Python 3.13.

```sh
python3 -m venv .venv-content
.venv-content/bin/python -m pip install -r tools/content-requirements.txt
.venv-content/bin/python tools/recover_content.py inventory \
  /absolute/extracted/Game.app/Contents/Resources/Data /absolute/new-content-catalog \
  --checkpoints /absolute/private-content-checkpoints \
  --bundle-timeout 120 --budget 1800 \
  --progress-report /absolute/reports/inventory-progress.json
```

UnityPy is pinned to **1.25.4** and the adapter refuses other versions. Its source
version fallback is the previously identified Unity **2021.3.56f2**. The pin is
not a claim that every bundle/version feature has been decoded successfully.
Transitive Python packages are not locked by this first adapter increment.
`tools/extract_assets.py --production INPUT_DIRECTORY NEW_OUTPUT_DIRECTORY`
also invokes this path. Legacy scene/route extraction remains unchanged, so
regenerating the committed track/tuning data is not coupled to this new format.

For timeout isolation, resumability, checkpoint integrity and hosted/private
storage boundaries, see [bounded inventory](BOUNDED-CONTENT-INVENTORY.md).
The legacy no-checkpoint invocation remains available for compatibility; it
runs the same decoder and catalog gates without per-bundle process limits.

## 2. Inventory and reference integrity

The inventory walks all `.bundle` files rather than assuming a historical bundle
count. FMOD banks, resource streams and Addressables catalogs are hashed as
inputs. macOS metadata sidecars are excluded. Inputs are read-only, symlinks are
rejected, and outputs must be new directories outside the source tree.

Each serialized object gets an identity derived from its bundle-relative file
key and exact signed path ID. Names are only display labels: duplicate names do
not overwrite files, and int64 IDs do not round through JSON floating point.
External `m_FileID` references use the owning serialized file's external table.
Missing or ambiguous files/objects are errors, never a same-number local match.

Per object the catalog retains raw bytes and the decoded typetree, including
byte arrays and large integers. Mesh streams decode through UnityPy MeshHandler;
textures/sprites decode to RGBA PNG. Renderer slots, transforms, bones, bind poses,
AnimationClips, Animator state, colliders, LODs, VFX and other component records
remain available in typetrees even when runtime conversion is unsupported.

```sh
python3 tools/recover_content.py verify-catalog /absolute/new-content-catalog
```

Validation checks object identities, artifact hashes/sizes, counts and status,
and recomputes every pointer edge from the retained typetrees. A catalog with
recorded failures is `incomplete` and the CLI exits nonzero. It is still retained
for diagnostics; an incomplete catalog is not a successful full-game extraction.
No original source methods are recovered by this tool.

## Raw numeric preservation versus runtime conversion

Unity typetrees can contain non-finite numeric fields. In the verified Smith's
Dreamworld source, positive infinities occur in particle size-curve slopes and a
timeline clip's post-extrapolation time. Their source-specific meaning is **not
inferred by the inventory**. They are neither replaced with zero nor accepted as
valid mesh coordinates, material parameters or animation times.

`encode_raw_tree` stores each non-finite parsed Python float as an explicit
`{"$float64":"7ff0000000000000"}`-style tag. The 16 lowercase hexadecimal digits
are its parsed binary64 bits. This does **not** claim the original Unity field
was binary64 or that a NaN payload survived the reader's initial conversion.
The separately hashed original `.bin` is the source-byte authority. Canonical
JSON still rejects bare `NaN`/`Infinity` tokens. Source dictionaries using this
reserved key are escaped through `$literal`; they are not interpreted as tags.

`decode_raw_tree` is used only at raw-inventory/reference inspection boundaries.
The record's `numeric_specials` array gives each field path, numeric category and
parsed bits. Catalog verification recomputes that array from the typetree and
rejects omissions or forged diagnostics. Pointer traversal continues through the
entire object: an infinity cannot hide an unresolved reference.

`encode_tree` and `decode_tree` remain **finite-only conversion APIs**.
`SceneReader.tree`, decoded mesh serialization and GLB conversion do not opt in
to raw special numbers. An affected runtime component needs an explicit,
evidence-backed semantic converter before its fields can be used in Godot.
An error-free raw decode or an `indexed` catalog is not a converted particle
system, playable animation, complete-media report or successful scene import.

The decoder recipe hashes these modules. Checkpoints from the old finite-only
raw serializer are incompatible and must not be relabelled or silently reused.
Finite typetrees without reserved-key collisions retain their previous encoding.

## 3. Derive the actual Arlen requirements

```sh
python3 tools/recover_content.py requirements --output /absolute/new-arlen-requirements.json
```

Defaults select `Map_Race_ArlenSpeedway`, `Character_KH_Hank`,
`Kart_KH_LandryLonghorner`, and that kart's referenced stock wheels. The current
committed definitions yield **17 requirements: 12 Addressables references,
4 scene names and 1 FMOD event**. Each retains its definition ID, exact field
path, GUID/subobject or scene/event identity, and unresolved status. This is
not an invented mapping from a game definition to a guessed `.glb` filename.
The committed status manifest can be regenerated byte-for-byte from definitions.

Compact/binary Addressables catalog decoding for this game is still a gate.
Until verified, `bind` accepts explicitly reviewed analyst mappings, requiring
the catalog-source filename/hash and exact source key. It records them as
**analyst-bound-not-imported**, not automatic discovery:

```json
[
  {
    "key": "<requirement key from requirements.json>",
    "asset": "<existing GameObject/object ID from catalog.json>",
    "evidence": {
      "file": "<catalog-relative input filename>",
      "sha256": "<that input's recorded SHA-256>",
      "source_key": "<exact key inspected in that catalog>"
    }
  }
]
```

```sh
python3 tools/recover_content.py bind \
  --catalog /absolute/new-content-catalog \
  --requirements /absolute/new-arlen-requirements.json \
  --bindings /absolute/reviewed-bindings.json --output /absolute/new-bound-manifest.json
```

An unresolved requirement or a broken dependency closure keeps the manifest
incomplete. No reviewed production bindings exist in this increment. FMOD event
binding does not decode samples, loops, parameters or event graphs.

## 4. Assemble supported renderer hierarchies and export GLB

```sh
python3 tools/recover_content.py export-root \
  --catalog /absolute/new-content-catalog \
  --root '<explicit root GameObject asset ID>' --output /absolute/exports/new-model.glb
```

Supported structural conversion includes static and skinned renderers,
parent/child transforms, multiple submeshes and material slots, positions,
normals, tangents, two UV sets, vertex colors, bone weights and inverse bind
matrices. Explicit normalized TRS animation channels can be exported through
`export-scene decoded-scene.json new-model.glb` with LINEAR, STEP or CUBICSPLINE
interpolation. **Unity compressed/humanoid clips and AnimatorControllers are
not decoded into those channels yet.** The authored test animation is not a
recovered character clip.

The normalized scene contract is illustrated by `tests/content_fixture.py`;
its pixels, three vertices and motion were authored solely for tests and are
never copied into the game project. `source_status` distinguishes fixtures,
Unity-byte inventory and unspecified external normalized inputs.

Coordinate policy is an explicit mirror of X, with reversed triangle winding,
UV V conversion, quaternion basis conversion and `C*M*C` inverse-bind matrices.
This policy is internally tested; alignment with the existing recovered race
spline and gameplay coordinates **must be verified before attaching real art**.
Source material pixels are retained, but the basic URP Lit/Unlit-to-glTF mapping,
sampler defaults, color interpretation and target lighting remain approximations.
Custom shaders, extra texture channels, baked lightmaps, morphs, extra UV sets,
Animator/Animation, LODs, particle systems and disabled source renderers/nodes
fail visual export instead of silently disappearing. Other components such as
colliders/lights are listed in `retained_only`, not applied to the visual GLB.
Original mip chains, compression formats and sampling behavior are not preserved
by the current PNG/standard-material path.

Conversion rejects missing textures/materials, invalid bones/weights/parents,
nonfinite values, invalid PNG CRC/data, zero/degenerate geometry (including
float32 collapse), and timestamps that lose ordering in float32. No missing
resource is replaced with a cube or a gray material. The GLB and JSON sidecar
use new paths, store hashes/counts, and report **converted-not-game-verified**.

## 5. Run real Godot import validation

```sh
godot --headless --path port --script "$PWD/tools/validate_content_import.gd" -- \
  /absolute/exports /absolute/new-import-report.json
```

This loads every generated GLB under the supplied directory with GLTFDocument,
instantiates scenes and validates geometry/material/texture/skin structure and
sidecar hashes. It fails on an empty directory, symlinks, corrupt GLBs, missing
sidecars or inconsistent imported counts. An import pass is not native-game
visual fidelity, actual gameplay integration, Android runtime or phone FPS proof.
Use the checked-process wrapper as in `scripts/test_godot.sh` to also reject
Godot error logs that accompany an exit code of zero.

Reproduce all local verification without game media or the optional UnityPy:

```sh
GODOT_TEST_BIN="$(command -v godot)" python3 -m unittest discover -s tests -v
python3 scripts/checked_process.py --timeout 240 -- godot --headless --path port --import
GODOT="$(command -v godot)" bash scripts/test_godot.sh verification/content
```

The full runner generates its tiny authored GLB outside `port/`, validates it,
then executes all runtime suites. The content-import suite verifies actual
skinning and bone motion at half time, not only that a file exists. Xvfb/xauth
are required for the separate graphical session-flow suite.

## Remaining production gates

Materialize the verified DMG and dependencies; run real bundle inventory and
resolve its diagnostics; verify Addressables mapping and the exact Hank/kart/
Arlen dependency closures; decode actual character animation clips; implement
source shader/texture semantics and scene collisions/lighting/LOD handling;
recover lawful FMOD samples and event behavior; render and compare the recovered
content; attach it to the tested race; then build/install/test Android. Do not
skip these gates merely because synthetic GLB import and race tests pass.

## Primary technical references

- UnityPy 1.25.4: https://pypi.org/project/UnityPy/1.25.4/
- UnityPy API and MeshHandler: https://github.com/K0lb3/UnityPy
- glTF 2.0 specification: https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html
- Godot GLTFDocument: https://docs.godotengine.org/en/stable/classes/class_gltfdocument.html

The actual game definition values come from the repository's committed
`work/assets/definitions/definitions.json`, not these external references.
