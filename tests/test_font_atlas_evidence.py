"""Additional adversarial checks and metadata-only production-check coverage."""
from __future__ import annotations
import json
from pathlib import Path
import tempfile
import unittest
from tools.content_pipeline import ContentError, canonical, verify_catalog
from tests import test_content_textures as fixtures


class FontAtlasEvidenceTests(unittest.TestCase):
    setup_fixture = fixtures.EmptyAtlasInventoryTests.setup_fixture
    run_fixture = fixtures.EmptyAtlasInventoryTests.run_fixture

    def test_fake_image_is_rejected_even_with_matching_hash(self):
        from tools.content_pipeline import verify_source
        out, cat, rec = self.run_fixture(self.setup_fixture())
        image = out/'invented.png'; image.write_bytes(b'not original pixels')
        rec['artifacts'].append({'role': 'decoded-image', 'path': image.name, **verify_source(image)})
        (out/'catalog.json').write_bytes(canonical(cat))
        with self.assertRaisesRegex(ContentError, 'cannot claim a decoded image'):
            verify_catalog(out)

    def test_owner_digest_tampering_is_rejected(self):
        out, cat, rec = self.run_fixture(self.setup_fixture())
        rec['image_owners'][0]['source_font_sha256'] = '0'*64
        (out/'catalog.json').write_bytes(canonical(cat))
        with self.assertRaisesRegex(ContentError, 'owner evidence'):
            verify_catalog(out)

    def test_nonempty_texture_still_requires_real_pixels(self):
        f = self.setup_fixture()
        f[-2].tree.update(m_Width=16, m_Height=16)
        out, cat, rec = self.run_fixture(f)
        self.assertEqual(cat['status'], 'incomplete')
        self.assertTrue(any(e['reason'].startswith('image:') for e in rec['errors']))
        self.assertNotIn('image_storage', rec)
        verify_catalog(out)

    def test_missing_font_bytes_cannot_be_replaced_by_a_name(self):
        f = self.setup_fixture(); del f[-1].tree['m_FontData']
        _, cat, rec = self.run_fixture(f)
        self.assertEqual(cat['status'], 'incomplete'); self.assertEqual(rec['image_owners'], [])

    def test_valid_cross_file_font_owner_is_resolved_by_pointer(self):
        f = self.setup_fixture()
        font = f[-1]; f[4]['assets.bundle'].remove(font)
        font.assets_file = f[4]['scene.bundle'][0].assets_file
        font.tree['m_Texture']['m_FileID'] = 1
        f[4]['scene.bundle'].append(font)
        _, cat, rec = self.run_fixture(f)
        self.assertEqual(cat['status'], 'indexed'); self.assertEqual(len(rec['image_owners']), 1)

    def test_embedded_font_invalid_byte_list_is_not_owner_evidence(self):
        for data in ([256], [True], [-1], ['0']):
            f = self.setup_fixture(); f[-1].tree['m_FontData'] = data
            with self.subTest(data=data):
                _, cat, rec = self.run_fixture(f)
                self.assertEqual(cat['status'], 'incomplete'); self.assertEqual(rec['image_owners'], [])

    def targeted_fixture(self):
        from tools.content_pipeline import verify_source
        f = self.setup_fixture()
        sources = [{'path': name, **verify_source(f[0]/name)} for name in ('assets.bundle', 'scene.bundle')]
        return f, sources

    def test_targeted_report_is_metadata_only_and_labels_authored_inputs(self):
        from scripts.check_font_atlases import check_atlases
        f, sources = self.targeted_fixture()
        report = check_atlases(f[0], _loader=f[2], _mesh_decoder=f[3],
                              _sources=sources, _expected_ids=['10'])
        self.assertEqual(report['status'], 'targeted-storage-verified')
        self.assertEqual(report['source_status'], 'synthetic-adapter-fixture')
        self.assertFalse(report['font_rendering_verified']); self.assertFalse(report['godot_imported'])
        self.assertNotIn(b'authored non-font bytes', canonical(report))
        self.assertEqual(len(report['empty_atlases']), 1)
        self.assertEqual(report['decoder_errors'], 0)

    def test_targeted_source_hash_mismatch_is_rejected(self):
        from scripts.check_font_atlases import check_atlases
        f, sources = self.targeted_fixture(); sources[0]['sha256'] = '0'*64
        with self.assertRaisesRegex(ContentError, 'SHA-256'):
            check_atlases(f[0], _loader=f[2], _mesh_decoder=f[3], _sources=sources, _expected_ids=['10'])

    def test_targeted_check_cannot_hide_missing_or_extra_empty_atlas(self):
        from scripts.check_font_atlases import check_atlases
        f, sources = self.targeted_fixture()
        with self.assertRaisesRegex(ContentError, 'identities'):
            check_atlases(f[0], _loader=f[2], _mesh_decoder=f[3], _sources=sources, _expected_ids=['10','99'])

    def test_targeted_check_requires_owner_evidence(self):
        from scripts.check_font_atlases import check_atlases
        f, sources = self.targeted_fixture(); f[-1].tree['m_Texture']['m_PathID'] = 0
        with self.assertRaisesRegex(ContentError, 'owner evidence'):
            check_atlases(f[0], _loader=f[2], _mesh_decoder=f[3], _sources=sources, _expected_ids=['10'])

    def test_targeted_cli_failure_report_does_not_leak_input_or_succeed(self):
        from scripts.check_font_atlases import main
        with tempfile.TemporaryDirectory() as work:
            root = Path(work); (root/'data').mkdir()
            result = main(['--data', str(root/'data'), '--report', str(root/'report.json')])
            self.assertEqual(result, 1)
            report = json.loads((root/'report.json').read_text())
            self.assertEqual(report['status'], 'failed')
            self.assertNotIn(work, json.dumps(report)); self.assertFalse(report['original_media_complete'])


if __name__ == '__main__': unittest.main()
