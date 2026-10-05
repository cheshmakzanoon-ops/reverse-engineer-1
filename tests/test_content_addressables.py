"""Authored catalog byte fixtures; no original game catalog or media included."""
from __future__ import annotations
import base64
import copy
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from tools.content_addressables import (CompactCatalog, ContentError, Key, MAX_BYTES,
    RUNTIME_PATH, Reader, bundle_relative_path, expand_internal_id, read_value,
    resolve_locations, strict_json, main)
from tools.content_pipeline import canonical, make_requirements

ROOT = Path(__file__).resolve().parents[1]
GUID = 'e2e28c6886d8885478abfb21f768ae2a'
PROVIDERS = ['UnityEngine.ResourceManagement.ResourceProviders.BundledAssetProvider',
             'UnityEngine.ResourceManagement.ResourceProviders.AssetBundleProvider',
             'UnityEngine.ResourceManagement.ResourceProviders.SceneProvider']


def enc(value: Key, *, unicode=False) -> bytes:
    if value.kind == 'string':
        raw = value.value.encode('utf-16-le' if unicode else 'ascii')
        return bytes([int(unicode)]) + struct.pack('<i', len(raw)) + raw
    if value.kind == 'hash128':
        raw = value.value.encode('ascii')
        return b'\x05' + bytes([len(raw)]) + raw
    tag, fmt = {'uint16': (2, '<H'), 'uint32': (3, '<I'), 'int32': (4, '<i')}[value.kind]
    return bytes([tag]) + struct.pack(fmt, value.value)


def make_catalog(keys=None, buckets=None, records=None, internal=None, extra=b'') -> dict:
    keys = keys or [Key('string', GUID), Key('string', 'driver'), Key('int32', 7),
                    Key('string', 'driver.bundle'), Key('string', 'SC_Test'), Key('string', 'shared.bundle')]
    buckets = buckets or [[0], [0], [1, 3], [1], [2], [3]]
    records = records or [(0, 0, 2, 123, -1, 1, 0), (1, 1, -1, 0, -1, 3, 1),
                          (2, 2, 2, 123, -1, 4, 2), (3, 1, -1, 0, -1, 5, 1)]
    internal = internal or ['Assets/Characters/driver.prefab', RUNTIME_PATH + 'StandaloneOSX/driver.bundle',
                            'Assets/Maps/SC_Test.unity', RUNTIME_PATH + 'StandaloneOSX/shared.bundle']
    kd = bytearray(struct.pack('<i', len(keys)))
    bd = bytearray(struct.pack('<i', len(keys)))
    for key, locations in zip(keys, buckets):
        bd.extend(struct.pack('<ii', len(kd), len(locations)))
        bd.extend(b''.join(struct.pack('<i', v) for v in locations))
        kd.extend(enc(key))
    ed = struct.pack('<i', len(records)) + b''.join(struct.pack('<7i', *r) for r in records)
    return {'m_ProviderIds': PROVIDERS, 'm_InternalIds': internal,
            'm_InternalIdPrefixes': [],
            'm_resourceTypes': [{'m_AssemblyName': 'UnityEngine.CoreModule', 'm_ClassName': 'UnityEngine.GameObject'},
                                {'m_AssemblyName': 'Unity.ResourceManager', 'm_ClassName': 'IAssetBundleResource'},
                                {'m_AssemblyName': 'Unity.ResourceManager', 'm_ClassName': 'SceneInstance'}],
            **{name: base64.b64encode(value).decode('ascii') for name, value in (
                ('m_KeyDataString', kd), ('m_BucketDataString', bd),
                ('m_EntryDataString', ed), ('m_ExtraDataString', extra))}}


def plan(*reqs):
    return {'schema': 1, 'definitions_sha256': 'a' * 64, 'requirements': list(reqs) or [
        {'key': 'hank', 'kind': 'addressable', 'guid': GUID, 'subobject': ''},
        {'key': 'scene', 'kind': 'scene', 'scene': 'SC_Test'},
        {'key': 'music', 'kind': 'fmod-event', 'event': 'event:/Music/Test'}]}


def mutate_i32(source, buffer, offset, value):
    result = copy.deepcopy(source)
    data = bytearray(base64.b64decode(result[buffer]))
    data[offset:offset + 4] = struct.pack('<i', value)
    result[buffer] = base64.b64encode(data).decode()
    return result


class ValueTests(unittest.TestCase):
    def test_ascii_unicode_are_same_key_type(self):
        key = Key('string', 'driver')
        self.assertEqual(read_value(enc(key), 0, key=True)[0], read_value(enc(key, unicode=True), 0, key=True)[0])

    def test_unicode_non_ascii_and_empty_string(self):
        for text in ('', 'éمدل'):
            key = Key('string', text)
            self.assertEqual(read_value(enc(key, unicode=True), 0, key=True)[0], key)

    def test_numeric_types_and_strings_do_not_alias(self):
        values = [Key('string', '7'), Key('uint16', 7), Key('uint32', 7), Key('int32', 7)]
        self.assertEqual(len({read_value(enc(v), 0, key=True)[0] for v in values}), 4)

    def test_numeric_extremes_and_hash(self):
        for value in [Key('uint16', 65535), Key('uint32', 2**32-1), Key('int32', -2**31),
                      Key('hash128', 'a'*32)]:
            self.assertEqual(read_value(enc(value), 0, key=True), (value, len(enc(value))))

    def test_invalid_hash_and_unknown_types_fail(self):
        for data in (b'\x05\x02zz', b'\x06\x00', b'\xff', b'\x07'):
            with self.subTest(data=data), self.assertRaises(ContentError):
                read_value(data, 0, key=True)

    def test_truncation_of_every_value_byte_fails(self):
        for value in (Key('string', 'name'), Key('uint32', 65536), Key('hash128', 'a'*32)):
            data = enc(value)
            for end in range(len(data)):
                with self.subTest(value=value, end=end), self.assertRaises(ContentError):
                    read_value(data[:end], 0, key=True)

    def test_negative_length_invalid_ascii_odd_utf16(self):
        for data in (b'\0'+struct.pack('<i', -1), b'\0'+struct.pack('<i', 1)+b'\xff',
                     b'\1'+struct.pack('<i', 1)+b'a'):
            with self.assertRaises(ContentError): read_value(data, 0, key=True)

    def test_inert_json_never_instantiates_assembly(self):
        a, c, raw = b'Untrusted.Assembly', b'Untrusted.Class', '{"m_Crc":42}'.encode('utf-16-le')
        encoded = b'\7'+bytes([len(a)])+a+bytes([len(c)])+c+struct.pack('<i', len(raw))+raw
        result, end = read_value(encoded, 0, key=False)
        self.assertEqual(result['data'], {'m_Crc': 42})
        self.assertEqual(end, len(encoded))
        with self.assertRaises(ContentError): read_value(encoded, 0, key=True)

    def test_json_duplicate_nonfinite_and_malformed_fail(self):
        for data in ('{"x":1,"x":2}', '{"n":NaN}', '{"n":Infinity}', '{"n":1e999}', '{'):
            with self.assertRaises(ContentError): strict_json(data)

    def test_reader_bad_offsets_counts_and_ranges(self):
        for offset in (-1, 10, True):
            with self.assertRaises(ContentError): Reader(b'a', offset)
        for count in (-1, 2**31-1):
            with self.assertRaises(ContentError): Reader(struct.pack('<i', count)).count()
        with self.assertRaises(ContentError): Reader(b'a').take(2)


class CatalogTests(unittest.TestCase):
    def load(self, source=None):
        return CompactCatalog(canonical(source or make_catalog()))

    def test_shared_dependencies_are_bucket_indices_not_entry_indices(self):
        cat = self.load()
        self.assertEqual(cat.lookup(GUID), [0])
        self.assertEqual(cat.locations[0]['dependencies'], [1, 3])
        self.assertEqual(cat.closure(0), [0, 1, 3])
        self.assertEqual(cat.closure(2), [1, 2, 3])
        self.assertEqual(cat.summary()['dependency_edges'], 4)

    def test_inert_extra_data(self):
        raw = b'{\x00}\x00'
        extra = b'\x07\x01A\x01C' + struct.pack('<i', len(raw)) + raw
        source = make_catalog(extra=extra)
        source = mutate_i32(source, 'm_EntryDataString', 4 + 4*4, 0)
        self.assertEqual(self.load(source).extra[0]['data'], {})

    def test_all_index_columns_validate(self):
        source = make_catalog()
        for column in (0, 1, 2, 4, 5, 6):
            with self.subTest(column=column), self.assertRaises(ContentError):
                self.load(mutate_i32(source, 'm_EntryDataString', 4+column*4, 9999))

    def test_negative_non_optional_indices_rejected(self):
        for col in (0, 1, 5, 6):
            with self.assertRaises(ContentError):
                self.load(mutate_i32(make_catalog(), 'm_EntryDataString', 4+col*4, -1))

    def test_counts_ranges_and_trailing_bytes_rejected(self):
        source = make_catalog()
        for name in ('m_KeyDataString', 'm_BucketDataString', 'm_EntryDataString'):
            for n in (-1, 0, 2**31-1):
                with self.subTest(name=name, n=n), self.assertRaises(ContentError):
                    self.load(mutate_i32(source, name, 0, n))
            bad = copy.deepcopy(source)
            bad[name] = base64.b64encode(base64.b64decode(bad[name])+b'\0').decode()
            with self.assertRaises(ContentError): self.load(bad)

    def test_invalid_key_offset_and_bucket_location(self):
        for name, offset, value in [('m_BucketDataString', 4, 0), ('m_BucketDataString', 4, 5),
                                    ('m_BucketDataString', 12, -1), ('m_BucketDataString', 12, 99)]:
            with self.assertRaises(ContentError): self.load(mutate_i32(make_catalog(), name, offset, value))

    def test_duplicate_key_and_locations_rejected(self):
        keys = [Key('string', 'dup'), Key('string', 'dup')]
        with self.assertRaises(ContentError): self.load(make_catalog(keys=keys, buckets=[[0], [1]]))
        buckets = [[0, 0], [0], [1, 3], [1], [2], [3]]
        with self.assertRaises(ContentError): self.load(make_catalog(buckets=buckets))

    def test_base64_missing_tables_and_resource_types_rejected(self):
        for name, value in [('m_KeyDataString', '*'), ('m_ProviderIds', 'wrong'),
                            ('m_InternalIds', [123]), ('m_resourceTypes', [{}])]:
            source = make_catalog(); source[name] = value
            with self.assertRaises(ContentError): self.load(source)
        source = make_catalog(); del source['m_EntryDataString']
        with self.assertRaises(ContentError): self.load(source)

    def test_dependency_expansion_is_bounded_before_allocation(self):
        with patch('tools.content_addressables.MAX_EDGES', 2), self.assertRaises(ContentError):
            self.load()

    def test_dependency_self_cycle_rejected(self):
        source = mutate_i32(make_catalog(), 'm_EntryDataString', 4+2*4, 0)
        with self.assertRaisesRegex(ContentError, 'Cyclic'): self.load(source)

    def test_dependency_multi_node_cycle_rejected(self):
        # driver -> bundles; first bundle -> driver GUID bucket.
        source = mutate_i32(make_catalog(), 'm_EntryDataString', 4+28+2*4, 0)
        with self.assertRaisesRegex(ContentError, 'Cyclic'): self.load(source)

    def test_missing_prefixes_are_valid_and_prefixes_expand(self):
        source = make_catalog(); source.pop('m_InternalIdPrefixes')
        self.assertEqual(self.load(source).locations[0]['internal_id'], 'Assets/Characters/driver.prefab')
        source['m_InternalIdPrefixes'] = ['Assets/Characters/']
        source['m_InternalIds'][0] = '0#driver.prefab'
        self.assertEqual(self.load(source).locations[0]['internal_id'], 'Assets/Characters/driver.prefab')

    def test_prefix_last_hash_rule_and_invalid_index(self):
        self.assertEqual(expand_internal_id(['abc'], '0#x'), 'abcx')
        self.assertEqual(expand_internal_id(['abc'], '0#x#y'), '0#x#y')
        self.assertEqual(expand_internal_id(['abc'], 'text#y'), 'text#y')
        self.assertEqual(expand_internal_id([], '0#x'), '0#x')
        for value in ('-1#x', '8#x'):
            with self.assertRaises(ContentError): expand_internal_id(['abc'], value)

    def test_file_source_is_hashed_and_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'catalog.json'; path.write_bytes(canonical(make_catalog()))
            self.assertEqual(CompactCatalog.load(path).source['bytes'], path.stat().st_size)
            link = Path(temp)/'link'; link.symlink_to(path)
            with self.assertRaises(ContentError): CompactCatalog.load(link)

    def test_size_bounds_and_not_object(self):
        for payload in (b'', b'[]', b'x' * (MAX_BYTES+1)):
            with self.assertRaises(ContentError): CompactCatalog(payload)


class ResolutionTests(unittest.TestCase):
    def setUp(self): self.cat = CompactCatalog(canonical(make_catalog()))

    def test_exact_guid_and_scene_resolve_fmod_separate(self):
        result = resolve_locations(self.cat, plan())
        self.assertEqual(result['resolved_locations'], 2)
        self.assertEqual(result['unresolved_requirements'], 1)
        self.assertEqual(result['requirements'][0]['match_kind'], 'exact-string-guid')
        self.assertEqual(result['requirements'][1]['match_kind'], 'exact-scene-key')
        self.assertEqual(result['requirements'][2]['reason'], 'fmod-event-not-an-addressables-location')
        for flag in ('object_bindings_complete', 'original_media_complete', 'original_source_recovered', 'godot_imported'):
            self.assertIs(result[flag], False)

    def test_bundle_hashes_are_actual_bytes_and_deduplicated(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ('driver', 'shared'):
                path = root/('StreamingAssets/aa/StandaloneOSX/'+name+'.bundle')
                path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b'authored-bundle-'+name.encode())
            result = resolve_locations(self.cat, plan(), data_root=root)
            self.assertEqual(result['verified_bundle_files'], 2)
            self.assertEqual(len(result['requirements'][0]['bundles'][0]['sha256']), 64)

    def test_missing_bundle_blocks_instead_of_guessing(self):
        with tempfile.TemporaryDirectory() as temp:
            result = resolve_locations(self.cat, plan(), data_root=Path(temp))
            self.assertEqual(result['resolved_locations'], 0)
            self.assertEqual(result['requirements'][0]['status'], 'blocked')

    def test_exact_scene_basename_fallback_only_scene_provider(self):
        source = make_catalog()
        kd = base64.b64decode(source['m_KeyDataString']).replace(b'SC_Test', b'aliasxx')
        source['m_KeyDataString'] = base64.b64encode(kd).decode()
        result = resolve_locations(CompactCatalog(canonical(source)), plan())
        self.assertEqual(result['requirements'][1]['match_kind'], 'exact-scene-provider-basename')
        self.assertEqual(result['resolved_locations'], 2)

    def test_scene_alias_pointing_to_asset_is_blocked(self):
        result = resolve_locations(self.cat, plan({'key':'s', 'kind':'scene', 'scene':'driver'}))
        self.assertIn('scene-key-does-not-select-scene-provider', result['requirements'][0]['blockers'])

    def test_ambiguous_guid_not_first_match(self):
        self.cat.buckets[Key('string', GUID)] = [0, 2]
        result = resolve_locations(self.cat, plan())
        self.assertEqual(result['requirements'][0]['reason'], 'ambiguous-locations')

    def test_missing_guid_and_similar_name_not_substituted(self):
        result = resolve_locations(self.cat, plan({'key':'x', 'kind':'addressable', 'guid':'0'*32}))
        self.assertEqual(result['requirements'][0]['reason'], 'missing-location')

    def test_subobject_preserved_not_claimed_bound(self):
        req = {'key':'x', 'kind':'addressable', 'guid': GUID, 'subobject':'Face'}
        result = resolve_locations(self.cat, plan(req))['requirements'][0]
        self.assertEqual(result['subobject_status'], 'requires-sprite-atlas-object-resolution')
        self.assertFalse(result['object_bound'])

    def test_invalid_manifest_duplicate_and_unknown_kind(self):
        for value in ({}, plan({'key':'x','kind':'unknown'}), plan({'key':'x','kind':'addressable','guid':'bad'}),
                      plan({'key':'x','kind':'scene','scene':'../evil'})):
            with self.assertRaises(ContentError): resolve_locations(self.cat, value)
        value = plan(); value['requirements'].append(value['requirements'][0])
        with self.assertRaises(ContentError): resolve_locations(self.cat, value)

    def test_network_tokens_and_traversal_are_not_followed(self):
        for value in ('https://example.com/a.bundle', '/a.bundle', RUNTIME_PATH+'../escape.bundle',
                      RUNTIME_PATH+'{Other}/a.bundle', RUNTIME_PATH+'a.bundle?x', RUNTIME_PATH+'a\\b.bundle'):
            with self.assertRaises(ContentError): bundle_relative_path(value)

    def test_bundle_source_symlink_is_blocked(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root/'StreamingAssets').symlink_to('/tmp')
            result = resolve_locations(self.cat, plan(), data_root=root)
            self.assertEqual(result['resolved_locations'], 0)

    def test_real_definitions_generate_17_requirements_without_fake_bindings(self):
        actual = make_requirements(ROOT/'work/assets/definitions/definitions.json')
        self.assertEqual(len(actual['requirements']), 17)
        self.assertEqual(sum(r['kind']=='addressable' for r in actual['requirements']), 12)
        result = resolve_locations(self.cat, actual)
        self.assertFalse(result['object_bindings_complete'])

    def test_output_deterministic_and_cli_no_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); cat=root/'catalog.json'; req=root/'plan.json'; output=root/'out.json'
            cat.write_bytes(canonical(make_catalog())); req.write_bytes(canonical(plan()))
            self.assertEqual(main(['--catalog',str(cat),'--requirements',str(req),'--output',str(output)]), 0)
            self.assertEqual(output.read_bytes(), canonical(resolve_locations(self.cat, plan())))
            with self.assertRaises(SystemExit):
                main(['--catalog',str(cat),'--requirements',str(req),'--output',str(output)])


if __name__ == '__main__': unittest.main()
