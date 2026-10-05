#!/usr/bin/env python3
"""Read compact JSON Addressables catalogs without executing Unity providers.

The seven-int entry layout and tagged key encoding follow Unity's package
source (ContentCatalogData/SerializationUtilities, reference tag 1.21.21).
This is not a claim about the target game's Addressables package version.
Locations identify assets/bundles, not serialized objects or converted media.
"""
from __future__ import annotations

import argparse
import base64
import binascii
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import struct
import sys
from typing import Any

if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.content_pipeline import ContentError, canonical, safe_child, verify_source

MAX_BYTES = 32 * 1024 * 1024
MAX_ITEMS = 1_000_000
MAX_EDGES = 2_000_000
RUNTIME_PATH = '{UnityEngine.AddressableAssets.Addressables.RuntimePath}/'
SCENE_TYPE = 'UnityEngine.ResourceManagement.ResourceProviders.SceneInstance'


def strict_json(payload: bytes | str) -> Any:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ContentError('Duplicate JSON property')
            result[key] = value
        return result
    def constant(_):
        raise ContentError('Non-finite JSON number')
    def number(text):
        value = float(text)
        if not math.isfinite(value):
            raise ContentError('Non-finite JSON number')
        return value
    try:
        return json.loads(payload, object_pairs_hook=pairs, parse_constant=constant, parse_float=number)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise ContentError('Invalid catalog JSON') from error


@dataclass(frozen=True)
class Key:
    # ASCII and UTF16 encode the same System.String type. Boxed numeric types
    # and Hash128 remain distinct from each other and from strings.
    kind: str
    value: str | int

    def report(self) -> dict:
        return {'type': self.kind, 'value': self.value}


class Reader:
    def __init__(self, payload: bytes, offset: int = 0):
        if type(offset) is not int or not 0 <= offset <= len(payload):
            raise ContentError('Catalog offset outside its buffer')
        self.data, self.offset = payload, offset

    def take(self, length: int) -> bytes:
        if type(length) is not int or length < 0 or length > len(self.data) - self.offset:
            raise ContentError('Truncated or invalid catalog range')
        result = self.data[self.offset:self.offset + length]
        self.offset += length
        return result

    def i32(self) -> int:
        return struct.unpack('<i', self.take(4))[0]

    def count(self, stride: int = 1) -> int:
        result = self.i32()
        if not 0 <= result <= min(MAX_ITEMS, (len(self.data) - self.offset) // stride):
            raise ContentError('Invalid catalog table count')
        return result

    def text(self, length: int, encoding: str) -> str:
        try:
            return self.take(length).decode(encoding, errors='strict')
        except UnicodeError as error:
            raise ContentError('Invalid catalog text encoding') from error

    def short_text(self) -> str:
        return self.text(self.take(1)[0], 'ascii')


def read_value(data: bytes, offset: int, *, key: bool) -> tuple[Any, int]:
    """Decode inert values only; never load an assembly or instantiate a type."""
    reader = Reader(data, offset)
    tag = reader.take(1)[0]
    if tag in (0, 1):
        value = Key('string', reader.text(reader.i32(), 'ascii' if tag == 0 else 'utf-16-le'))
    elif tag in (2, 3, 4):
        length, fmt, kind = {2: (2, '<H', 'uint16'), 3: (4, '<I', 'uint32'),
                             4: (4, '<i', 'int32')}[tag]
        value = Key(kind, struct.unpack(fmt, reader.take(length))[0])
    elif tag == 5:
        text = reader.short_text()
        if re.fullmatch('[0-9a-fA-F]{32}', text) is None:
            raise ContentError('Invalid Hash128 catalog key')
        value = Key('hash128', text.lower())
    elif tag == 7 and not key:
        assembly, name = reader.short_text(), reader.short_text()
        text = reader.text(reader.i32(), 'utf-16-le')
        value = {'type': 'inert-json-object', 'assembly': assembly, 'class': name,
                 'data': strict_json(text)}
    else:
        # The reference Type reader/writer have different representations. It
        # must not be guessed, and custom JSON objects are not supported keys.
        raise ContentError('Unsupported catalog object tag: ' + str(tag))
    return value, reader.offset


def _buffer(source: dict, name: str) -> bytes:
    value = source.get(name)
    if not isinstance(value, str) or len(value) > MAX_BYTES * 4 // 3 + 4:
        raise ContentError('Missing or oversized catalog buffer: ' + name)
    try:
        result = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as error:
        raise ContentError('Invalid catalog base64: ' + name) from error
    if len(result) > MAX_BYTES:
        raise ContentError('Oversized decoded catalog buffer')
    return result


def _strings(source: dict, name: str, *, optional: bool = False) -> list[str]:
    value = source.get(name)
    if optional and value is None:
        return []
    if (not isinstance(value, list) or len(value) > MAX_ITEMS or
            any(not isinstance(item, str) for item in value)):
        raise ContentError('Invalid catalog string table: ' + name)
    return value


def _at(table: list, index: int, name: str):
    if type(index) is not int or not 0 <= index < len(table):
        raise ContentError('Out-of-range catalog ' + name + ' index')
    return table[index]


def expand_internal_id(prefixes: list[str], value: str) -> str:
    """Match the reference LastIndexOf('#') rule, not a first-hash guess."""
    if not prefixes or '#' not in value:
        return value
    number, tail = value.rsplit('#', 1)
    if re.fullmatch(r'\s*[+-]?\d+\s*', number) is None:
        return value
    index = int(number)
    if not -(2**31) <= index < 2**31:
        return value  # C# int.TryParse would fail.
    return _at(prefixes, index, 'prefix') + tail


class CompactCatalog:
    def __init__(self, payload: bytes):
        if not isinstance(payload, bytes) or not 0 < len(payload) <= MAX_BYTES:
            raise ContentError('Catalog input is empty or oversized')
        source = strict_json(payload)
        if not isinstance(source, dict):
            raise ContentError('Expected compact JSON catalog object')
        self.source = {'bytes': len(payload), 'sha256': hashlib.sha256(payload).hexdigest()}
        providers = _strings(source, 'm_ProviderIds')
        internal_ids = _strings(source, 'm_InternalIds')
        prefixes = _strings(source, 'm_InternalIdPrefixes', optional=True)
        types = source.get('m_resourceTypes')
        if (not isinstance(types, list) or len(types) > MAX_ITEMS or any(
                not isinstance(t, dict) or not isinstance(t.get('m_AssemblyName'), str)
                or not isinstance(t.get('m_ClassName'), str) for t in types)):
            raise ContentError('Invalid catalog resource-type table')
        buckets = Reader(_buffer(source, 'm_BucketDataString'))
        key_data = _buffer(source, 'm_KeyDataString')
        extra_data = _buffer(source, 'm_ExtraDataString')
        entries = Reader(_buffer(source, 'm_EntryDataString'))
        bucket_count = buckets.count(8)
        key_reader = Reader(key_data)
        key_count = key_reader.count(1)
        if key_count != bucket_count or not key_count:
            raise ContentError('Key and bucket counts disagree or are empty')
        self.keys: list[Key] = []
        self.buckets: dict[Key, list[int]] = {}
        key_ranges = []
        for _ in range(bucket_count):
            offset = buckets.i32()
            if offset < 4:
                raise ContentError('Key offset points into table header')
            count = buckets.count(4)
            locations = [buckets.i32() for _ in range(count)]
            value, end = read_value(key_data, offset, key=True)
            if value in self.buckets or len(set(locations)) != len(locations):
                raise ContentError('Duplicate catalog key or bucket location')
            key_ranges.append((offset, end))
            self.keys.append(value)
            self.buckets[value] = locations
        if buckets.offset != len(buckets.data):
            raise ContentError('Trailing bucket data')
        end = 4
        for start, finish in sorted(key_ranges):
            if start != end:
                raise ContentError('Overlapping or unaccounted key bytes')
            end = finish
        if end != len(key_data):
            raise ContentError('Trailing key data')
        count = entries.count(28)
        if not count or len(entries.data) != 4 + 28 * count:
            raise ContentError('Invalid seven-int catalog entry table')
        self.locations: list[dict] = []
        extra_cache: dict[int, Any] = {}
        edge_count = 0
        for ordinal in range(count):
            internal, provider, dependency, dep_hash, extra, primary, resource = [entries.i32() for _ in range(7)]
            dep_key = None if dependency < 0 else _at(self.keys, dependency, 'dependency-key')
            dependencies = [] if dep_key is None else self.buckets[dep_key]
            edge_count += len(dependencies)
            if edge_count > MAX_EDGES:
                raise ContentError('Catalog dependency expansion exceeds supported budget')
            if extra >= 0 and extra not in extra_cache:
                extra_cache[extra] = read_value(extra_data, extra, key=False)[0]
            self.locations.append({
                'entry': ordinal,
                'internal_id': expand_internal_id(prefixes, _at(internal_ids, internal, 'internal-id')),
                'provider': _at(providers, provider, 'provider'),
                'resource_type': _at(types, resource, 'resource-type'),
                'primary_key': _at(self.keys, primary, 'primary-key').report(),
                'dependency_key': dep_key.report() if dep_key else None,
                'dependencies': list(dependencies), 'dependency_hash': dep_hash,
                'extra_offset': extra,
            })
        # Validate every bucket, including unused aliases. Extra objects remain
        # private/inert; only their digest and byte count enter summary evidence.
        for locations in self.buckets.values():
            for ordinal in locations:
                _at(self.locations, ordinal, 'location')
        self.extra = extra_cache
        self.buffer_hashes = {name: hashlib.sha256(data).hexdigest() for name, data in (
            ('key', key_data), ('bucket', buckets.data), ('entry', entries.data), ('extra', extra_data))}
        self._validate_graph()

    @classmethod
    def load(cls, path: Path) -> CompactCatalog:
        identity = verify_source(path)
        if identity['bytes'] > MAX_BYTES:
            raise ContentError('Oversized catalog file')
        result = cls(path.read_bytes())
        if result.source != identity:
            raise ContentError('Catalog changed while reading')
        return result

    def _validate_graph(self) -> None:
        # Iterative DFS, linear in stored graph edges; no Python recursion limit
        # or quadratic all-root traversal on a large production catalog.
        colors = bytearray(len(self.locations))
        for root in range(len(colors)):
            if colors[root]:
                continue
            stack = [(root, False)]
            while stack:
                node, leaving = stack.pop()
                if leaving:
                    colors[node] = 2
                    continue
                if colors[node] == 1:
                    raise ContentError('Cyclic catalog dependencies')
                if colors[node] == 2:
                    continue
                colors[node] = 1
                stack.append((node, True))
                stack.extend((dep, False) for dep in reversed(self.locations[node]['dependencies']))

    def lookup(self, text: str) -> list[int]:
        return list(self.buckets.get(Key('string', text), []))

    def closure(self, root: int) -> list[int]:
        _at(self.locations, root, 'root')
        found, pending = set(), [root]
        while pending:
            node = pending.pop()
            if node not in found:
                found.add(node)
                pending.extend(self.locations[node]['dependencies'])
        return sorted(found)

    def summary(self) -> dict:
        return {'format': 'compact-json-seven-int-entries', 'source': self.source,
                'keys': len(self.keys), 'locations': len(self.locations),
                'dependency_edges': sum(len(r['dependencies']) for r in self.locations),
                'buffer_sha256': self.buffer_hashes,
                'providers': sorted({r['provider'] for r in self.locations}),
                'key_types': sorted({k.kind for k in self.keys})}


def bundle_relative_path(internal_id: str) -> str:
    """Only the known local RuntimePath binding is supported; no network fetches.

    StandaloneOSX catalog RuntimePath is Data/StreamingAssets/aa. Absolute URLs,
    other property tokens and basename-only searches cannot establish provenance.
    """
    if not internal_id.startswith(RUNTIME_PATH):
        raise ContentError('Unsupported bundle runtime path')
    relative = 'StreamingAssets/aa/' + internal_id[len(RUNTIME_PATH):]
    if ('{' in relative or '}' in relative or '?' in relative or '#' in relative
            or not relative.endswith('.bundle')):
        raise ContentError('Unsupported bundle internal ID')
    safe_child(Path('/catalog-path-validation'), relative)
    return relative


def resolve_locations(catalog: CompactCatalog, plan: dict, *, data_root: Path | None = None) -> dict:
    """Resolve requirements to locations, never promote them to object bindings.

    SceneName is matched against an exact catalog key, or a unique SceneInstance
    internal scene basename. That is scene-load identity, not fuzzy art matching.
    Sprite subobjects and FMOD events explicitly remain separate recovery gates.
    """
    requirements = plan.get('requirements')
    if plan.get('schema') != 1 or not isinstance(requirements, list) or not requirements:
        raise ContentError('Invalid requirements manifest')
    results, seen, verified_bundles = [], set(), {}
    for req in requirements:
        if not isinstance(req, dict) or not isinstance(req.get('key'), str) or req['key'] in seen:
            raise ContentError('Invalid or duplicate requirement key')
        seen.add(req['key'])
        result = {'requirement': req, 'status': 'unresolved', 'object_bound': False}
        if req.get('kind') == 'fmod-event':
            result['reason'] = 'fmod-event-not-an-addressables-location'
            results.append(result)
            continue
        if req.get('kind') == 'addressable':
            guid = req.get('guid')
            if not isinstance(guid, str) or re.fullmatch('[0-9a-fA-F]{32}', guid) is None:
                raise ContentError('Invalid requirement GUID')
            matches, match_kind = catalog.lookup(guid), 'exact-string-guid'
            if len(matches) > 1:
                # A GUID bucket can expose both Texture2D and Sprite locations.
                # Use the original assembly-qualified requested type, not the
                # first bucket entry or a display-name/filename heuristic.
                candidates = [catalog.locations[n] for n in matches]
                result['candidate_locations'] = candidates
                qualified = req.get('subobject_type', '')
                parts = qualified.split(',', 1) if isinstance(qualified, str) else []
                # Atlas subobject types describe contained sprites, not the
                # parent atlas location. Such ambiguity needs a separate policy.
                has_atlas = any(c['resource_type']['m_ClassName'] == 'UnityEngine.U2D.SpriteAtlas'
                                for c in candidates)
                if (len(parts) == 2 and not has_atlas and
                        not req.get('field', '').endswith('/_atlasSpriteRef')):
                    requested = {'m_ClassName': parts[0].strip(), 'm_AssemblyName': parts[1].strip()}
                    typed = [c['entry'] for c in candidates if c['resource_type'] == requested]
                    result['requested_location_type'] = requested
                    if len(typed) == 1:
                        matches, match_kind = typed, 'exact-string-guid-and-qualified-type'
        elif req.get('kind') == 'scene':
            scene = req.get('scene')
            if not isinstance(scene, str) or not scene or any(c in scene for c in '/\\'):
                raise ContentError('Invalid scene requirement')
            matches, match_kind = catalog.lookup(scene), 'exact-scene-key'
            if not matches:
                matches = [loc['entry'] for loc in catalog.locations
                           if loc['resource_type']['m_ClassName'] == SCENE_TYPE
                           and PurePosixPath(loc['internal_id']).name == scene + '.unity']
                match_kind = 'exact-scene-instance-basename'
        else:
            raise ContentError('Unsupported requirement kind')
        if len(matches) != 1:
            result.update(reason='missing-location' if not matches else 'ambiguous-locations',
                          candidates=matches)
            results.append(result)
            continue
        entry = matches[0]
        location = catalog.locations[entry]
        dependency_entries = catalog.closure(entry)
        dependencies = [catalog.locations[n] for n in dependency_entries if n != entry]
        bundle_entries = [loc for loc in dependencies if loc['provider'].endswith('.AssetBundleProvider')]
        result.update(location=location, match_kind=match_kind,
                      dependency_entries=dependency_entries, bundles=[])
        blocked = []
        if req['kind'] == 'scene':
            # The verified original uses BundledAssetProvider even for its
            # SceneInstance entries. Location identity is not provider execution.
            if location['resource_type']['m_ClassName'] != SCENE_TYPE:
                blocked.append('scene-key-does-not-select-scene-instance')
            result['scene_runtime_provider_status'] = 'not-executed-or-ported'
        if not bundle_entries:
            blocked.append('no-bundle-dependency')
        for loc in bundle_entries:
            bundle = {'entry': loc['entry'], 'internal_id': loc['internal_id']}
            try:
                relative = bundle_relative_path(loc['internal_id'])
                bundle['path'] = relative
                if data_root is not None:
                    if relative not in verified_bundles:
                        verified_bundles[relative] = verify_source(safe_child(data_root, relative))
                    bundle.update(verified_bundles[relative])
            except ContentError:
                blocked.append('unsupported-or-missing-bundle-input')
            result['bundles'].append(bundle)
        if len(bundle_entries) != len(dependencies):
            blocked.append('non-bundle-dependency-needs-provider-policy')
        result.update(status='blocked' if blocked else 'location-resolved-not-object-bound',
                      blockers=sorted(set(blocked)),
                      subobject_status='requires-sprite-atlas-object-resolution' if req.get('subobject')
                                       else 'not-requested')
        results.append(result)
    resolved = sum(r['status'] == 'location-resolved-not-object-bound' for r in results)
    return {'schema': 1, 'status': 'catalog-location-plan', 'catalog': catalog.summary(),
            'definitions_sha256': plan.get('definitions_sha256'),
            'requirements': results, 'resolved_locations': resolved,
            'unresolved_requirements': len(results) - resolved,
            'verified_bundle_files': len(verified_bundles),
            'source_bundle_bytes_verified': (data_root is not None and bool(verified_bundles)
                and all(r['status'] == 'location-resolved-not-object-bound' for r in results
                        if r['requirement']['kind'] != 'fmod-event')),
            'object_bindings_complete': False, 'original_media_complete': False,
            'original_source_recovered': False, 'godot_imported': False}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', type=Path, required=True)
    parser.add_argument('--requirements', type=Path, required=True)
    parser.add_argument('--data', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    from tools.content_batches import check_paths
    paths = [args.data, args.output] if args.data else [args.catalog, args.requirements, args.output]
    check_paths(*paths)
    if args.output.exists() or args.output.is_symlink():
        parser.error('Use a new output path')
    try:
        result = resolve_locations(CompactCatalog.load(args.catalog),
                                   strict_json(args.requirements.read_bytes()), data_root=args.data)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('xb') as output:
            output.write(canonical(result))
        print('CATALOG_LOCATIONS: ' + str(result['resolved_locations']) + '; object bindings remain unverified')
        # FMOD is a separate gate, but all Unity requirements must resolve.
        return int(any(r['status'] != 'location-resolved-not-object-bound'
                       for r in result['requirements'] if r['requirement']['kind'] != 'fmod-event'))
    except (ContentError, OSError, KeyError, TypeError) as error:
        print('CATALOG_LOCATIONS: failed (' + type(error).__name__ + ')', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
