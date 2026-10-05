# Original Addressables location recovery

This stage connects recovered definition GUIDs and SceneName values to the
original compact catalog's locations and bundle dependencies. It does **not**
claim that a catalog location is a decoded/assembled GameObject, a resolved
SpriteAtlas subobject, recovered C# method code, audio, or a Godot import.

## Commands

No UnityPy or engine is required to inspect a catalog:

```sh
python tools/content_addressables.py \
  --catalog /private/Data/StreamingAssets/aa/catalog.json \
  --requirements docs/status/2026-10-04-arlen-content-requirements.json \
  --data /private/Data --output /private/reports/arlen-locations.json
python -m unittest tests.test_content_addressables tests.test_addressables_evidence -v
```

This general command hashes its inputs, but those hashes alone do not prove an
arbitrary directory came from the original DMG. For original-source evidence:

```sh
python scripts/check_addressables.py \
  --data '/private/Game.app/Contents/Resources/Data' \
  --report /private/reports/original-addressables.json
```

The evidence command requires the exact original catalog (1,505,032 bytes;
SHA-256 `742d72cb89e182f97c3656fef22eac67a863da6cfd5a2a8940a5822a57f6b9be`)
and the canonical 276-file extracted-application manifest digest
`89ed0deffe5bad3a93e38b769037fe943071185c748e65fa9dd814291f0db25b`.
The digest was read from source run `37359017829`, artifact `11368488005`, whose
ZIP SHA-256 is `fef3c1cb3ddf5fc2b31bad856e7fffa4eeff77e89624d52489fe502501208d16`.
Every selected bundle's bytes and hash must match that manifest. The optional
`--drive-id` path verifies the existing exact DMG gate before extraction too.

`addressables-evidence.yml` performs this check against the owner-provided
original. Only the selected location plan, identifiers, hashes, counts, status
and bounded parser diagnostics are retained. No original catalog bytes, source
binary, extracted images, fonts, typetrees or keys enter the artifact. An absent
or ambiguous Unity location fails the command, while the FMOD requirement remains
explicitly unresolved as a separate subsystem.

## Supported format and integrity boundaries

The reader supports compact JSON/Base64 catalogs with seven little-endian int32
fields per location. It decodes key offsets and bucket membership, distinguishes
boxed integer types from strings and Hash128, expands the reference internal-ID
prefix format, and traverses dependency **key buckets**, not mistaken direct
entry indices. JSON extra data is inert; assemblies/providers are never loaded.

Strict bounds, duplicate-key checks, text validation, table-size checks, cycle
checks and a dependency-expansion budget reject corrupt inputs. Type-object keys,
custom JSON keys, binary catalog formats and arbitrary provider execution are
unsupported, not silently approximated. SceneName lookup uses an exact catalog
key or a unique SceneProvider internal scene basename; it never fuzzy-matches
an art filename. Only the known local Addressables RuntimePath can identify a
bundle path. Remote URLs and other runtime-property substitutions are rejected.

A resolved location retains the source GUID/scene, catalog entry, internal ID,
provider/type, dependency entries, selected bundle identities, and any requested
sprite subobject. Its status is `location-resolved-not-object-bound`. Source
shader/animation semantics, Unity AssetBundle container-to-object binding,
SpriteAtlas membership, object dependency closure, FMOD and scene conversion
remain separate gates. The five existing decoder-recipe modules are unchanged,
so this stage does not invalidate raw bundle checkpoints.

## Format references

Unity's package source is the format reference, not evidence of the target
build's exact Addressables package version:

- `needle-mirror/com.unity.addressables`, tag `1.21.21`,
  `Runtime/ResourceLocators/ContentCatalogData.cs`, blob
  `695c1633b776f613b086a278bd540ba9dd35da4a`.
- Same tag, `Runtime/Utility/SerializationUtilities.cs`, blob
  `575dd4f1f04cf9ac0fe2fcd613c1b6ba1287dfd6`.
- https://docs.unity3d.com/Packages/com.unity.addressables@1.20/manual/AssetReferences.html

Authored test success and production-source verification are different claims;
read the retained source report before asserting the latter.
