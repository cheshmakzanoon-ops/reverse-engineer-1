# Serialized-empty font atlases

## Scope

A raw Unity `Texture2D` may be serialized without pixels because a font atlas is
populated at runtime. That is not a recovered renderable image. The recovery
pipeline must retain the object without inventing a PNG or treating every empty
texture as an acceptable font atlas.

The preceding numeric-recovery evidence recorded six `Texture2D` image errors
in the original font bundle. This increment implements a narrow storage and
ownership classifier, plus an independently runnable original-source check.

## Storage and ownership gates

`tools/content_textures.py` requires both dimensions to be exactly zero, no inline
image bytes, no complete image bytes, an empty stream path with zero offset/size,
one mip, an image count of zero or one, and a positive serialized format ID.
Missing, contradictory, boolean or fractional fields are not silently repaired.
Textures with positive dimensions still use the existing image decoder.

The raw record and its typetree remain independently hashed. The record is labelled
`serialized-empty-image-not-renderable`; `image_storage.runtime_image_available`
is false, and no `decoded-image` artifact is generated.

After file-scoped references have been resolved, the finalizer requires at least
one supported owner:

* A native `Font` with an exact `m_Texture` relationship, nonempty embedded
  `m_FontData`, and no baked character rectangles.
* A `TMPro.TMP_FontAsset` whose `MonoScript` identity is resolved, with dynamic
  population, clear-on-build enabled, empty glyph/character tables, positive atlas
  dimensions, and an exact reference to a retained native source Font.

Names and proximity to another font object do not establish ownership. This is
serialized ownership evidence, **not validation that the embedded font binary can
be rendered**. Authored tests deliberately use non-font bytes for those structural
checks. Unresolved dependencies or unsupported owners keep the catalog incomplete.

Catalog verification recomputes storage/owner evidence from the retained typetrees
and checks the artifact hashes. It rejects missing or forged diagnostics and
manufactured decoded-image outputs. `SceneReader` refuses an empty atlas wherever
a runtime material requests image pixels. Runtime font reconstruction remains a
separate implementation task.

## Focused original-source verification

Run against an already extracted `Contents/Resources/Data` directory:

```sh
python scripts/check_font_atlases.py --data /private/Game.app/Contents/Resources/Data \
  --report verification/font-atlas.json
```

Or use the owner's original public-link download with the existing fixed DMG hash:

```sh
python scripts/check_font_atlases.py --drive-id 15gzBmVRHNf8PpJAH2Ri0ncDfjnsgdorb \
  --report verification/font-atlas.json
```

The script verifies the exact font and MonoScript bundle sizes and SHA-256 hashes
recorded in the preceding extraction. It checks all six known empty-atlas IDs,
retained raw data, supported owner relationships, and runtime-image rejection.
It also rejects decoder errors in the selected bundles. Its selected two-bundle
catalog may retain references to omitted shader/other bundles; the report exposes
that status and count rather than claiming full dependency completion.

Only the metadata report is retained. Temporary original bundles, native font
bytes, decoded images and typetrees are removed. No original font file may be
included in public evidence or response attachments. The `font-atlas-evidence`
workflow runs this check on trusted `main` updates and uploads only that report.
It does not need or rotate the private-source receiving key.

`targeted-storage-verified` is intentionally different from a complete catalog,
recovered original C# source, a rendered font, imported Godot game content, or an
Android build. The report keeps all those completion flags false.

## Reproduce tests

```sh
python -m unittest tests.test_content_textures tests.test_font_atlas_evidence -v
GODOT_TEST_BIN=/path/to/godot python -m unittest discover -s tests -v
```

Existing object checkpoints include the decoder recipe. The new texture module
is included in that recipe, so older checkpoints are not silently relabelled as
outputs of this decoder. A compatible same-recipe resume remains supported.

## External documentation, not original-game evidence

Unity's TextMeshPro documentation explains runtime-populated atlases and zero-size
reset behavior. Those descriptions motivate the supported policy but do not prove
the state of this particular game; the hash-locked source check is the production
evidence gate:

https://docs.unity.cn/Packages/com.unity.textmeshpro@4.0/manual/FontAssetsDynamicFonts.html
https://docs.unity.cn/Packages/com.unity.ugui@3.0/manual/TextMeshPro/FontAssets.html
