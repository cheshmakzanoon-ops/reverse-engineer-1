"""Raw numeric preservation is not permission to export non-finite GPU values."""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path
import struct
import tempfile
import unittest

from tools import content_pipeline as cp
from tools.content_unity import inventory, SceneReader
from tests.content_fixture import bundle_fixture, scene_fixture
from tools.content_gltf import export_glb


class NumericCodecTests(unittest.TestCase):
    def test_raw_roundtrip_preserves_parsed_binary64(self):
        for bits in ('7ff0000000000000', 'fff0000000000000',
                     '7ff8000000000042', 'fff8000000000007'):
            with self.subTest(bits=bits):
                value = struct.unpack('>d', bytes.fromhex(bits))[0]
                encoded = cp.encode_raw_tree({'value': value})
                payload = cp.canonical(encoded)
                self.assertNotIn(b'Infinity', payload)
                self.assertNotIn(b'NaN', payload)
                restored = cp.decode_raw_tree(json.loads(payload))
                self.assertEqual(struct.pack('>d', restored['value']).hex(), bits)
                self.assertEqual(encoded['value'], {'$float64': bits})

    def test_finite_raw_encoding_is_legacy_byte_identical(self):
        value = {'a': [1.5, -0.0, 1 << 62, b'\x00\xff'], 'ptr': {'m_FileID': 1, 'm_PathID': 77}}
        self.assertEqual(cp.canonical(cp.encode_raw_tree(value)), cp.canonical(cp.encode_tree(value)))
        restored = cp.decode_raw_tree(cp.encode_raw_tree(value))
        self.assertEqual(struct.pack('>d', restored['a'][1]), struct.pack('>d', -0.0))

    def test_nested_raw_fields_keep_original_numbers(self):
        value = {'curve': [{'inSlope': math.inf, 'outSlope': -math.inf}], 'n': -(1 << 62), 'data': b'abc'}
        restored = cp.decode_raw_tree(json.loads(cp.canonical(cp.encode_raw_tree(value))))
        self.assertEqual(restored, value)

    def test_reserved_tag_literal_is_not_confused_with_float(self):
        for value in ({'$float64': '7ff0000000000000'},
                      {'$float64': 'not-a-number', 'nested': math.inf},
                      {'$literal': [['x', 1]]}):
            with self.subTest(value=str(value)):
                restored = cp.decode_raw_tree(cp.encode_raw_tree(value))
                self.assertEqual(restored, value)
        literal = {'$float64': '7ff0000000000000'}
        self.assertEqual(cp.decode_tree(cp.encode_tree(literal)), literal)

    def test_raw_associative_pairs_keep_special_values(self):
        value = {7: math.inf, 'x': -math.inf}
        self.assertEqual(cp.decode_raw_tree(cp.encode_raw_tree(value)), value)

    def test_strict_encoder_still_rejects_special_values(self):
        for value in (math.inf, -math.inf, math.nan):
            with self.subTest(value=str(value)), self.assertRaises(cp.ContentError):
                cp.encode_tree({'nested': [value]})

    def test_strict_decoder_rejects_raw_special_tags(self):
        for bits in ('7ff0000000000000', 'fff0000000000000', '7ff8000000000042'):
            with self.subTest(bits=bits), self.assertRaises(cp.ContentError):
                cp.decode_tree({'nested': [{'$float64': bits}]})

    def test_raw_decoder_rejects_malformed_or_finite_tags(self):
        for bits in ('', '0', 'z' * 16, '7FF0000000000000', '0000000000000000',
                     '3ff0000000000000', 7, None, []):
            with self.subTest(bits=bits), self.assertRaises(cp.ContentError):
                cp.decode_raw_tree({'$float64': bits})

    def test_bare_nonstandard_json_constants_are_rejected(self):
        for text in ('{"x":NaN}', '{"x":Infinity}', '{"x":-Infinity}'):
            for decode in (cp.decode_raw_tree, cp.decode_tree):
                with self.subTest(text=text, decode=decode.__name__), self.assertRaises(cp.ContentError):
                    decode(json.loads(text))

    def test_raw_encoder_does_not_stringify_unknown_objects(self):
        with self.assertRaises(cp.ContentError):
            cp.encode_raw_tree({'value': object()})

    def test_metadata_contains_exact_paths_and_parsed_bits(self):
        fields = cp.numeric_specials({'a~/b': [math.inf, -math.inf, math.nan], 'finite': 0.0})
        self.assertEqual([x['field'] for x in fields], ['/a~0~1b/0', '/a~0~1b/1', '/a~0~1b/2'])
        self.assertEqual([x['kind'] for x in fields], ['positive-infinity', 'negative-infinity', 'nan'])
        self.assertTrue(all(len(x['parsed_binary64']) == 16 for x in fields))
        cp.canonical(fields)


class NumericInventoryTests(unittest.TestCase):
    def prepare(self, *, missing=False, bad_mesh=False):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        base = Path(temp.name)
        root, out = base/'input', base/'catalog'
        loader, decoder, objects = bundle_fixture(root)
        go = objects['scene.bundle'][0]
        extra = type(go)(go.assets_file, 20, 'MonoBehaviour', {
            'm_Name': 'NumericFixture',
            'curve': [{'inSlope': math.inf, 'outSlope': -math.inf}],
            'target': {'m_FileID': 1, 'm_PathID': 999 if missing else 7}})
        # This is authored binary data, not a claim of original Unity extraction.
        extra.get_raw_data = lambda: b'authored numeric fixture\x00'+struct.pack('<ff', math.inf, -math.inf)
        objects['scene.bundle'].append(extra)
        if bad_mesh:
            def decoder(obj):
                mesh = copy.deepcopy(scene_fixture()['meshes']['mesh'])
                mesh['positions'][0][0] = math.inf
                return mesh
        cat = inventory(root, out, loader=loader, mesh_decoder=decoder)
        record = next(o for o in cat['objects'] if o['name'] == 'NumericFixture')
        return base, out, cat, record, loader, decoder

    def test_raw_object_metadata_and_references_are_retained(self):
        _, out, cat, record, _, _ = self.prepare()
        self.assertEqual(record['errors'], [])
        self.assertEqual(record['conversion'], 'raw-only')
        self.assertEqual(len(record['numeric_specials']), 2)
        self.assertEqual(record['references'][0]['field'], '/target')
        self.assertEqual(cat['status'], 'indexed')
        self.assertFalse(cat['original_media_complete'])
        self.assertFalse(cat['godot_imported'])
        self.assertEqual(cp.verify_catalog(out), cat)

    def test_raw_metadata_cannot_enter_scene_without_explicit_conversion(self):
        _, out, _, record, _, _ = self.prepare()
        with self.assertRaisesRegex(cp.ContentError, 'Non-finite'):
            SceneReader(out).tree(record['id'])

    def test_special_value_does_not_hide_missing_pointer(self):
        _, out, cat, record, _, _ = self.prepare(missing=True)
        self.assertEqual(cat['status'], 'incomplete')
        self.assertTrue(any('Missing object' in e['reason'] for e in record['errors']))
        self.assertEqual(cp.verify_catalog(out), cat)

    def test_missing_or_forged_numeric_diagnostics_are_rejected(self):
        _, out, cat, record, _, _ = self.prepare()
        for action in ('delete', 'change', 'add'):
            forged = copy.deepcopy(cat)
            victim = next(o for o in forged['objects'] if o['id'] == record['id'])
            if action == 'delete': victim.pop('numeric_specials')
            elif action == 'change': victim['numeric_specials'][0]['field'] = '/made-up'
            else: victim['numeric_specials'].append(copy.deepcopy(victim['numeric_specials'][0]))
            (out/'catalog.json').write_bytes(cp.canonical(forged))
            with self.subTest(action=action), self.assertRaises(cp.ContentError):
                cp.verify_catalog(out)

    def test_finite_records_cannot_claim_unobserved_specials(self):
        _, out, cat, record, _, _ = self.prepare()
        victim = next(o for o in cat['objects'] if o['type'] == 'GameObject')
        victim['numeric_specials'] = record['numeric_specials']
        (out/'catalog.json').write_bytes(cp.canonical(cat))
        with self.assertRaises(cp.ContentError): cp.verify_catalog(out)

    def test_nonfinite_decoded_mesh_remains_an_error(self):
        _, out, cat, _, _, _ = self.prepare(bad_mesh=True)
        mesh = next(o for o in cat['objects'] if o['type'] == 'Mesh')
        self.assertEqual(cat['status'], 'incomplete')
        self.assertTrue(mesh['errors'])
        self.assertNotIn('decoded-mesh', [a['role'] for a in mesh['artifacts']])
        cp.verify_catalog(out)

    def test_raw_bone_bounds_do_not_discard_finite_decoded_vertices(self):
        # Mirrors the *shape* observed in production Mesh metadata, not its art.
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            loader, decoder, objects = bundle_fixture(base/'input')
            source_mesh = objects['assets.bundle'][0]
            source_mesh.tree['m_BonesAABB'] = [
                {'m_Min': {'x': math.inf}, 'm_Max': {'x': -math.inf}}]
            source_mesh.get_raw_data = lambda: b'authored bounds fixture'+struct.pack('<ff', math.inf, -math.inf)
            cat = inventory(base/'input', base/'catalog', loader=loader, mesh_decoder=decoder)
            mesh = next(o for o in cat['objects'] if o['type'] == 'Mesh')
            self.assertEqual(mesh['errors'], [])
            self.assertEqual(mesh['conversion'], 'decoded-mesh-not-assembled')
            self.assertEqual(len(mesh['numeric_specials']), 2)
            decoded = next(a for a in mesh['artifacts'] if a['role'] == 'decoded-mesh')
            vertices = cp.decode_tree(json.loads((base/'catalog'/decoded['path']).read_text()))
            self.assertEqual(vertices['positions'], scene_fixture()['meshes']['mesh']['positions'])
            self.assertFalse(cat['godot_imported'])
            # Full scene assembly is still conservative until these raw fields
            # have an explicit semantic policy. No empty-bound meaning is guessed.
            with self.assertRaisesRegex(cp.ContentError, 'Non-finite'):
                SceneReader(base/'catalog').tree(mesh['id'])

    def test_repeated_raw_inventory_is_byte_identical(self):
        base, out, _, _, loader, decoder = self.prepare()
        other = base/'other'
        inventory(base/'input', other, loader=loader, mesh_decoder=decoder)
        files = lambda p: {q.relative_to(p).as_posix(): q.read_bytes() for q in p.rglob('*') if q.is_file()}
        self.assertEqual(files(out), files(other))

    def test_runtime_glb_still_rejects_nonfinite_geometry(self):
        with tempfile.TemporaryDirectory() as d:
            scene = scene_fixture()
            scene['meshes']['mesh']['positions'][0][0] = math.inf
            target = Path(d)/'bad.glb'
            with self.assertRaises(cp.ContentError): export_glb(scene, target)
            self.assertFalse(target.exists())


if __name__ == '__main__':
    unittest.main()
