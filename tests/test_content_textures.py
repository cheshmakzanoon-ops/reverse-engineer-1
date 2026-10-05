"""Authored empty-atlas fixtures, not original fonts or game artwork."""
from __future__ import annotations
import copy
import json
from pathlib import Path
import tempfile
import unittest
from tools.content_pipeline import ContentError, canonical, encode_raw_tree, verify_catalog
from tools.content_unity import inventory, SceneReader
from tests.content_fixture import bundle_fixture


def empty_texture():
    return {'m_Name': 'Authored empty atlas', 'm_Width': 0, 'm_Height': 0,
            'm_CompleteImageSize': 0, 'm_ImageCount': 1, 'm_MipCount': 1,
            'm_TextureFormat': 1, 'image data': b'',
            'm_StreamData': {'path': '', 'offset': 0, 'size': 0}}


class EmptyStorageTests(unittest.TestCase):
    def classify(self, tree):
        from tools.content_textures import empty_texture_storage
        return empty_texture_storage(tree)

    def test_standalone_cli_imports_outside_repository(self):
        import subprocess
        import sys
        with tempfile.TemporaryDirectory() as work:
            script = Path(__file__).resolve().parents[1]/'tools/recover_content.py'
            result = subprocess.run([sys.executable, str(script), '--help'], cwd=work,
                                    capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('verify-catalog', result.stdout)

    def test_exact_empty_storage_has_no_runtime_pixels(self):
        result = self.classify(empty_texture())
        self.assertEqual(result['state'], 'serialized-empty')
        self.assertFalse(result['runtime_image_available'])
        self.assertEqual(result['dimensions'], [0, 0])

    def test_zero_and_one_image_counts_are_preserved(self):
        for count in (0, 1):
            t = empty_texture(); t['m_ImageCount'] = count
            self.assertEqual(self.classify(t)['image_count'], count)

    def test_nonempty_dimensions_use_existing_decoder(self):
        t = empty_texture(); t.update(m_Width=1, m_Height=1)
        self.assertIsNone(self.classify(t))

    def test_partial_or_negative_dimensions_are_not_empty_atlases(self):
        for w, h in ((0, 1), (1, 0), (-1, 0), (0, -1)):
            t = empty_texture(); t.update(m_Width=w, m_Height=h)
            with self.subTest(w=w, h=h), self.assertRaises(ContentError): self.classify(t)

    def test_boolean_float_and_missing_dimensions_are_rejected(self):
        for value in (False, 0.0, None, '0'):
            t = empty_texture(); t['m_Width'] = value
            with self.subTest(value=value), self.assertRaises(ContentError): self.classify(t)
        t = empty_texture(); del t['m_Height']
        with self.assertRaises(ContentError): self.classify(t)

    def test_conflicting_or_missing_empty_storage_fields_are_rejected(self):
        for field, value in [('m_CompleteImageSize', 1), ('image data', b'x'),
                             ('image data', ''), ('m_ImageCount', 2), ('m_MipCount', 2),
                             ('m_TextureFormat', 0), ('m_CompleteImageSize', False)]:
            t = empty_texture(); t[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ContentError): self.classify(t)
        for field in ('m_CompleteImageSize', 'image data', 'm_StreamData', 'm_ImageCount', 'm_MipCount', 'm_TextureFormat'):
            t = empty_texture(); del t[field]
            with self.subTest(missing=field), self.assertRaises(ContentError): self.classify(t)

    def test_empty_texture_cannot_hide_stream_declarations(self):
        for field, value in [('path', 'missing.resS'), ('size', 1), ('offset', 1), ('size', False)]:
            t = empty_texture(); t['m_StreamData'][field] = value
            with self.subTest(field=field), self.assertRaises(ContentError): self.classify(t)


class EmptyAtlasInventoryTests(unittest.TestCase):
    def setup_fixture(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        base = Path(temp.name); root = base/'source'; out = base/'catalog'
        loader, decoder, objects = bundle_fixture(root)
        texture = objects['assets.bundle'][-1]
        texture.tree = empty_texture()
        texture.parse_as_object = lambda: self.fail('An empty atlas must not invoke the image decoder')
        cls = type(texture); sf = texture.assets_file
        ptr = lambda n: {'m_FileID': 0, 'm_PathID': n}
        font = cls(sf, 11, 'Font', {'m_Name': 'Authored font', 'm_FontData': b'authored non-font bytes',
                                    'm_CharacterRects': [], 'm_Texture': ptr(10)})
        objects['assets.bundle'].append(font)
        return root, out, loader, decoder, objects, texture, font

    def run_fixture(self, fixture):
        root, out, loader, decoder, objects, texture, font = fixture
        cat = inventory(root, out, loader=loader, mesh_decoder=decoder)
        rec = next(o for o in cat['objects'] if o['type']=='Texture2D')
        return out, cat, rec

    def make_tmp(self, fixture):
        root, out, loader, decoder, objects, texture, font = fixture
        ptr = lambda n: {'m_FileID': 0, 'm_PathID': n}
        font.tree['m_Texture'] = ptr(0)
        cls = type(font); sf = font.assets_file
        script = cls(sf, 12, 'MonoScript', {'m_ClassName': 'TMP_FontAsset', 'm_Namespace': 'TMPro'})
        owner = cls(sf, 13, 'MonoBehaviour', {'m_Name': 'Authored TMP font', 'm_Script': ptr(12),
            'm_AtlasPopulationMode': 1, 'm_ClearDynamicDataOnBuild': 1,
            'm_AtlasWidth': 1024, 'm_AtlasHeight': 1024, 'm_GlyphTable': [], 'm_CharacterTable': [],
            'm_AtlasTextures': [ptr(10)], 'm_SourceFontFile': ptr(11)})
        objects['assets.bundle'].extend([script, owner])
        return script, owner

    def test_embedded_font_owner_preserved_without_fabricating_png(self):
        out, cat, rec = self.run_fixture(self.setup_fixture())
        self.assertEqual(cat['status'], 'indexed')
        self.assertFalse(cat['original_media_complete']); self.assertFalse(cat['godot_imported'])
        self.assertEqual(rec['conversion'], 'serialized-empty-image-not-renderable')
        self.assertEqual({a['role'] for a in rec['artifacts']}, {'raw-object', 'typetree'})
        self.assertEqual(rec['image_owners'][0]['kind'], 'embedded-font-atlas')
        self.assertFalse((out/'images').exists())
        verify_catalog(out)

    def test_verified_tmp_dynamic_owner_is_recorded(self):
        fixture = self.setup_fixture(); self.make_tmp(fixture)
        _, cat, rec = self.run_fixture(fixture)
        self.assertEqual(cat['status'], 'indexed')
        self.assertEqual(rec['image_owners'][0]['kind'], 'tmp-dynamic-font-atlas')
        self.assertEqual(rec['image_owners'][0]['atlas_dimensions'], [1024, 1024])

    def test_unowned_empty_image_keeps_raw_evidence_but_fails_catalog(self):
        f = self.setup_fixture(); f[-1].tree['m_Texture']['m_PathID'] = 0
        out, cat, rec = self.run_fixture(f)
        self.assertEqual(cat['status'], 'incomplete')
        self.assertTrue(any('verified font owner' in e['reason'] for e in rec['errors']))
        self.assertEqual(len(rec['artifacts']), 2); verify_catalog(out)

    def test_font_name_is_not_owner_evidence(self):
        f = self.setup_fixture(); f[-1].type.name = 'MonoBehaviour'
        _, cat, _ = self.run_fixture(f); self.assertEqual(cat['status'], 'incomplete')

    def test_empty_embedded_font_or_static_glyph_rects_are_not_accepted(self):
        for field, value in [('m_FontData', b''), ('m_CharacterRects', [{}])]:
            f = self.setup_fixture(); f[-1].tree[field] = value
            with self.subTest(field=field):
                _, cat, _ = self.run_fixture(f); self.assertEqual(cat['status'], 'incomplete')

    def test_tmp_static_populated_or_unknown_owner_is_not_whitelisted(self):
        for field, value in [('m_AtlasPopulationMode', 0), ('m_AtlasPopulationMode', 2),
                             ('m_AtlasPopulationMode', True), ('m_ClearDynamicDataOnBuild', 0),
                             ('m_GlyphTable', [{}]), ('m_CharacterTable', [{}]), ('m_AtlasWidth', 0)]:
            f = self.setup_fixture(); _, owner = self.make_tmp(f); owner.tree[field] = value
            with self.subTest(field=field, value=value):
                _, cat, _ = self.run_fixture(f); self.assertEqual(cat['status'], 'incomplete')

    def test_tmp_script_identity_is_required(self):
        f = self.setup_fixture(); script, _ = self.make_tmp(f); script.tree['m_ClassName'] = 'NotFont'
        _, cat, _ = self.run_fixture(f); self.assertEqual(cat['status'], 'incomplete')

    def test_missing_script_reference_is_not_hidden_by_empty_texture(self):
        f = self.setup_fixture(); _, owner = self.make_tmp(f); owner.tree['m_Script']['m_PathID'] = 999
        out, cat, rec = self.run_fixture(f)
        self.assertEqual(cat['status'], 'incomplete'); self.assertEqual(rec['image_owners'], [])
        verify_catalog(out)

    def test_no_font_name_matching_across_file_id_spaces(self):
        f = self.setup_fixture(); f[-1].tree['m_Texture']['m_FileID'] = 1
        _, cat, rec = self.run_fixture(f)
        self.assertEqual(cat['status'], 'incomplete'); self.assertEqual(rec['image_owners'], [])

    def test_runtime_material_refuses_empty_texture_instead_of_substitution(self):
        out, cat, rec = self.run_fixture(self.setup_fixture())
        root = next(o['id'] for o in cat['objects'] if o['name']=='FixtureRoot')
        with self.assertRaisesRegex(ContentError, 'no serialized pixels'):
            SceneReader(out).assemble(root)

    def test_verified_diagnostic_cannot_be_removed_or_forged(self):
        out, cat, rec = self.run_fixture(self.setup_fixture())
        for field, value in [('image_storage', None), ('image_storage', {'state':'serialized-empty'}),
                             ('image_owners', []), ('conversion', 'decoded-image-not-bound')]:
            changed = copy.deepcopy(cat); r = next(o for o in changed['objects'] if o['id']==rec['id'])
            if value is None: r.pop(field, None)
            else: r[field] = value
            (out/'catalog.json').write_bytes(canonical(changed))
            with self.subTest(field=field), self.assertRaises(ContentError): verify_catalog(out)

    def test_hidden_owner_error_is_rejected(self):
        f = self.setup_fixture(); f[-1].tree['m_Texture']['m_PathID'] = 0
        out, cat, rec = self.run_fixture(f)
        rec['errors'] = []; cat['status'] = 'indexed'; cat['counts']['errors'] = 0
        (out/'catalog.json').write_bytes(canonical(cat))
        with self.assertRaises(ContentError): verify_catalog(out)

    def test_resumed_empty_texture_is_identical(self):
        from tools.content_unity import read_bundle, source_files
        from tools.content_batches import recipe
        f = self.setup_fixture(); root, _, loader, decoder, _, _, _ = f
        source = next(s for s in source_files(root) if s['path']=='assets.bundle')
        out = root.parent/'journal'; out.mkdir()
        first = read_bundle(root, source, out, loader=loader, mesh_decoder=decoder, journal_recipe=recipe())
        second = read_bundle(root, source, out, loader=loader, mesh_decoder=decoder, journal_recipe=recipe())
        self.assertEqual(canonical(first), canonical(second))
        self.assertTrue(any(o.get('image_storage') for o in first['objects']))


if __name__ == '__main__': unittest.main()
