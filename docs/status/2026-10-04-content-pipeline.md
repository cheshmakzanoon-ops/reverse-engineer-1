# Content-recovery foundation — 2026-10-04

Source base: `1feb43d35a8eaa73175e2e053db1169144bf5bc2`, single `main`.
The restored local source tree matched remote tree
`e187dda49c0713daf334bdcfc73bbcc8d4a5cab8` exactly before edits. The source
snapshot and pinned Godot engine were restored from the previously verified
runtime artifact and the preceding two patches. This is not a new clone of the
1.19 GB LFS object.

## Scope and actual outcome

Implemented a source-gated inventory/reference graph, exact Arlen requirements,
supported decoded-content GLB conversion, and real Godot import regressions.
**No original Arlen track, Hank character, kart, animation or audio was extracted
or integrated in this increment.** The existing game visuals are unchanged.
The original media and real UnityPy decoding remain unverified external gates.

The new production path is separate from the legacy route/data exporter.
It does not silently alter the seven committed recovered data modules.
`extract_assets.py --production` routes to the strict inventory; its old mode
remains for existing regeneration workflows, not production-content parity.

## Implemented behavior

- Verify source size/SHA-256 and reject an LFS pointer as input game bytes.
- Inventory bundle objects by qualified serialized-file/path-ID identity, retain
  raw bytes/typetrees and external GUID metadata, and decode mesh/PNG streams
  through the pinned UnityPy adapter interface.
- Resolve external PPtrs through their external-file table, preserve int64 IDs
  and byte arrays, reject ambiguous filenames even when one is in the same
  bundle, and retain failures rather than dropping objects.
- Validate catalog IDs, hashes, sizes, counts, status and edges recomputed from
  preserved typetrees. Failed catalogs cannot report indexed success.
- Generate the exact Arlen/Hank/Landry Longhorner/stock-wheel requirements from
  committed definitions: 12 Addressables references, four scenes and one music
  event. All 17 remain unresolved; none is a fabricated resource path.
- Assemble explicitly selected supported renderer hierarchies. Export static/
  skinned geometry, material slots, supported attributes, skinning and explicitly
  decoded TRS animation channels as deterministic GLB, with per-output evidence.
- Reject invalid PNG CRC/data, missing resources, unsupported features, bad rigs,
  NaN/Infinity, degeneracy and ordering failures after float32 conversion.
- Instantiate generated GLBs in the real Godot importer, check structure and
  actual texture/skin/animation behavior, and keep fixtures outside the game.

This is not an automatic Addressables decoder, compressed/humanoid AnimationClip
converter, shader reconstruction, complete scene import, FMOD extractor, or
native method recovery. Analyst bindings are explicitly marked analyst-asserted.
Collider/light components can be retained without being applied to the visual
GLB; unsupported Animator/LOD/particle/lightmap cases block that export. Basic
URP-to-glTF materials, sampler defaults and color interpretation are explicitly
approximated. Original mip chains and shader behavior are not preserved by PNG
extraction. See `docs/CONTENT-RECOVERY.md` before using the converter on real art.

## Tests and reproducibility

**74 Python tests pass**, including **46 new content tests**: 33 inventory/
reference/requirements/conversion tests and 13 adversarial-integrity tests.
The existing real pinned-engine Android-template component test also passes;
it deliberately creates no usable Android package.

**483 Godot checks across 16 suites pass** using
`4.7.2.stable.official.ed1daf0bf`. The new content suite contributes 20 checks:
actual GLB parsing/scene generation, one reflected root transform, nonempty
geometry, UVs, imported skin weights/binds/bones, alpha cutoff, nonempty texture
pixels and a one-second animation that moves the imported bone to x=-0.5 at
half time. These use an authored three-vertex/one-pixel fixture, not game art.
A separate general batch importer passes on that fixture. Import and a 360-frame
headless frontend boot pass. All seven recovered data modules reproduce
byte-for-byte, and the new requirements manifest reproduces exactly.

The full race, combat, input, UI and save suites remain green. Their existing
Arlen physics/session evidence does not become original-art or Android evidence.
The final reference-ambiguity guard was subsequently rerun with all 74 Python
tests, followed by the 20-check Godot content test and batch import. No live
race script changed in this increment.

Test-driven failures were recorded before fixing invalid IDs/edges, PNG CRC/
truncation, float32 time/triangle collapse, non-ancestor skeletons, invalid
colors, false catalog success/counts and ambiguous external-file preference.
Synthetic adapter tests assert their synthetic provenance, including the
connected inventory -> hierarchy -> GLB path. They do not load real Unity
AssetBundle bytes. The UnityPy library could not be installed locally; the
adapter API was checked against the current primary documentation/source.

```sh
GODOT_TEST_BIN=/path/to/Godot_v4.7.2-stable_linux.x86_64 \
  python3 -m unittest discover -s tests -p 'test_*.py' -v
python3 scripts/checked_process.py --timeout 240 -- godot --headless --path port --import
GODOT=/path/to/Godot_v4.7.2-stable_linux.x86_64 \
  bash scripts/test_godot.sh verification/content
python3 scripts/checked_process.py --timeout 120 -- \
  godot --headless --path port --quit-after 360
```

## Actual external attempts

The DMG tracked in ordinary Git is a 135-byte LFS pointer. Its required object
is 1,190,250,225 bytes with SHA-256
`4720e9fbaabd6d39bceac362e271e24e9c08d8f549191f70bad34e6d5c28b6be`.
The `verify-dmg` command correctly failed on that pointer. The actual inventory
command failed because the extracted `work/dmg` source directory is absent.
Direct Git/network access cannot resolve github.com in this environment; no
original bytes were materialized by those attempts. Library search/listing did
not provide the original DMG; its recursive Drive listing is unsupported, so
this is not a claim that no copy exists anywhere in the user's storage.

Debug APK and test AAB commands were executed with the pinned Godot and local
JDK and failed dependency preflight on missing Android SDK/build-tools/templates.
Release AAB stopped at missing owner-controlled signing variables. **No APK,
AAB, verified signing sidecar, emulator run or physical-device result exists for
this increment.** No signing key was generated to misrepresent a release build.

The GitHub connector available in this turn exposes reads but no tree/commit/
reference write actions. Local Git's network path also fails DNS. A local
commit/patch must not be described as pushed; publication status is reported
separately after the actual push attempt and remote readback. The source and
verification work remain reproducible even when that transport is unavailable.

## Exact changed paths

`.github/workflows/verify.yml`, `.gitignore`, `README.md`,
`docs/CONTENT-RECOVERY.md`,
`docs/status/2026-10-04-arlen-content-requirements.json`,
`docs/status/2026-10-04-content-pipeline.md`, `port/README.md`,
`port/tests/test_content_import.gd`, `port/tests/test_content_import.gd.uid`,
`scripts/test_godot.sh`, `tests/__init__.py`, `tests/content_fixture.py`,
`tests/test_content_integrity.py`, `tests/test_content_pipeline.py`,
`tools/content-requirements.txt`, `tools/content_gltf.py`,
`tools/content_pipeline.py`, `tools/content_unity.py`,
`tools/extract_assets.py`, `tools/recover_content.py`,
`tools/validate_content_import.gd`.

## Next dependency-valid task

On a host able to retrieve the verified LFS object, run actual extraction and
inventory, resolve its diagnostics and inspect the Addressables catalog. Bind
the exact Arlen/Hank/kart dependencies, then implement the real clip/shader/scene
features that block their export. Only after actual resource import and visual/
coordinate validation should those assets replace engineering geometry in the
already tested race. Audio and Android physical-device acceptance remain
separate required gates. This increment does not satisfy that complete slice.
