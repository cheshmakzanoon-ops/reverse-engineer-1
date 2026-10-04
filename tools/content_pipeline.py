"""Lossless inventory identities and requirements. No inferred production assets.

The legacy route extractor deliberately remains byte-compatible. This module is
its separate production-content path: integers retain precision, references keep
file scope, missing references are errors, and output names never use asset names.
"""
from __future__ import annotations
import base64
import hashlib
import json
import math
import os
import re
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

SCHEMA = 1
UNITY_VERSION = '2021.3.56f2'
UNITYPY_VERSION = '1.25.4'
DMG_SHA256 = '4720e9fbaabd6d39bceac362e271e24e9c08d8f549191f70bad34e6d5c28b6be'
DMG_BYTES = 1190250225

class ContentError(ValueError):
    """A fail-closed content gate, not a successful partial conversion."""


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)+'\n').encode('utf-8')


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as file:
        for block in iter(lambda: file.read(1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()


def verify_source(path: Path, *, expected_sha256: str | None = None, expected_size: int | None = None) -> dict:
    if not path.is_file() or path.is_symlink():
        raise ContentError(f'Original input is missing or is a symlink: {path}')
    with path.open('rb') as file:
        if file.read(128).startswith(b'version https://git-lfs.github.com/spec/v1'):
            raise ContentError('Original input is an LFS pointer, not game bytes; materialize its LFS object first')
    size = path.stat().st_size
    if size <= 0 or (expected_size is not None and size != expected_size):
        raise ContentError(f'Input size does not match: {path.name} ({size} bytes)')
    digest = sha256_file(path)
    if expected_sha256 is not None and digest != expected_sha256:
        raise ContentError(f'Input SHA-256 does not match: {path.name}')
    return {'bytes': size, 'sha256': digest}


def safe_child(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or '\\' in relative or ':' in relative:
        raise ContentError('Invalid relative resource path')
    p = PurePosixPath(relative)
    if p.is_absolute() or '..' in p.parts or '.' == relative:
        raise ContentError(f'Unsafe resource path: {relative}')
    current = root
    for part in p.parts:
        current = current / part
        if current.is_symlink():
            raise ContentError(f'Symlink resource is not allowed: {relative}')
    if not current.resolve().is_relative_to(root.resolve()):
        raise ContentError(f'Resource escapes output root: {relative}')
    return current


def encode_tree(value: Any) -> Any:
    """Preserve byte arrays and int64 values without lossy float/str fallbacks."""
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int):
        return {'$int64': str(value)} if abs(value) > 9007199254740991 else value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ContentError('Non-finite typetree number')
        return value
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {'$bytes': base64.b64encode(value).decode('ascii')}
    if isinstance(value, dict):
        if any(not isinstance(k, str) for k in value):
            # Unity typetrees normally have string fields; associative containers
            # with other keys are represented as explicit pairs, never str(key).
            return {'$pairs': [[encode_tree(k), encode_tree(v)] for k, v in value.items()]}
        if any(k in value for k in ('$bytes', '$int64', '$pairs', '$literal')):
            return {'$literal': [[k, encode_tree(v)] for k, v in sorted(value.items())]}
        return {k: encode_tree(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [encode_tree(v) for v in value]
    raise ContentError(f'Unsupported typetree value: {type(value).__name__}')


def decode_tree(value: Any) -> Any:
    if isinstance(value, dict):
        if set(value) == {'$bytes'}:
            return base64.b64decode(value['$bytes'], validate=True)
        if set(value) == {'$int64'}:
            return int(value['$int64'])
        if set(value) == {'$pairs'}:
            return {decode_tree(k): decode_tree(v) for k,v in value['$pairs']}
        if set(value) == {'$literal'}:
            return {k: decode_tree(v) for k,v in value['$literal']}
        return {k: decode_tree(v) for k,v in value.items()}
    return [decode_tree(v) for v in value] if isinstance(value, list) else value


def walk(value: Any, field: str = '') -> Iterable[tuple[str, Any]]:
    yield field, value
    if isinstance(value, dict):
        for key in sorted(value):
            yield from walk(value[key], field+'/'+key.replace('~','~0').replace('/','~1'))
    elif isinstance(value, (list, tuple)):
        for i, entry in enumerate(value):
            yield from walk(entry, field+'/'+str(i))


def exact_int(value: Any) -> int:
    if isinstance(value, bool) or not (isinstance(value, int) or isinstance(value, str) and re.fullmatch(r'-?\d+', value)):
        raise ContentError('Object/file ID must be an exact integer, not a float or boolean')
    return int(value)


def object_id(file_key: str, path_id: Any) -> str:
    return hashlib.sha256(canonical([file_key, str(exact_int(path_id))])).hexdigest()


def external_name(path: str) -> str:
    return path.replace('\\', '/').rstrip('/').rsplit('/', 1)[-1]


class AssetIndex:
    def __init__(self, files: Iterable[dict], objects: Iterable[dict]):
        self.files: dict[str, dict] = {}
        self.objects: dict[str, dict] = {}
        self.pairs: dict[tuple[str, int], str] = {}
        self.by_name: dict[str, list[str]] = {}
        for entry in files:
            f = dict(entry)
            if f['key'] in self.files:
                raise ContentError('Duplicate serialized-file identity: '+f['key'])
            self.files[f['key']] = f
            self.by_name.setdefault(external_name(f['name']), []).append(f['key'])
        for entry in objects:
            record = dict(entry)
            file = record['file']
            pid = exact_int(record['path_id'])
            if file not in self.files or pid == 0:
                raise ContentError('Invalid owning file or zero object ID')
            identity = object_id(file, pid)
            if identity in self.objects:
                raise ContentError('Duplicate object identity: '+identity)
            record.update(id=identity, path_id=str(pid))
            self.objects[identity] = record
            self.pairs[file, pid] = identity

    def resolve(self, file_key: str, ptr: dict) -> str | None:
        fid, pid = exact_int(ptr['m_FileID']), exact_int(ptr['m_PathID'])
        if pid == 0:
            return None
        if file_key not in self.files or fid < 0:
            raise ContentError('Invalid pointer owner or negative external file ID')
        target = file_key
        if fid:
            owner = self.files[file_key]
            externals = owner['externals']
            if fid > len(externals):
                raise ContentError(f'External file ID {fid} is outside {file_key} table')
            name = external_name(externals[fid-1])
            candidates = self.by_name.get(name, [])
            # A duplicate CAB name is not resolved by guessing the nearest bundle.
            # Retained GUID metadata can support a future verified resolver.
            if len(candidates) != 1:
                raise ContentError(f'{"ambiguous" if candidates else "missing"} external serialized file: {name}')
            target = candidates[0]
        result = self.pairs.get((target, pid))
        if result is None:
            raise ContentError(f'Missing object: {target} / {pid}')
        return result

    def references(self, identity: str, tree: dict) -> tuple[list[dict], list[dict]]:
        refs, errors = [], []
        file = self.objects[identity]['file']
        for field, value in walk(tree):
            if not isinstance(value, dict) or not {'m_FileID','m_PathID'} <= set(value):
                continue
            try:
                target = self.resolve(file, value)
                if target is not None:
                    refs.append({'field': field, 'asset': target, 'file_id': exact_int(value['m_FileID']), 'path_id': str(exact_int(value['m_PathID']))})
            except ContentError as e:
                errors.append({'field': field, 'reason': str(e)})
        return refs, errors

    def closure(self, roots: Iterable[str]) -> list[str]:
        pending, found = list(roots), set()
        while pending:
            key = pending.pop()
            if key in found:
                continue
            if key not in self.objects:
                raise ContentError('Missing closure root/dependency: '+key)
            record = self.objects[key]
            if record.get('errors'):
                raise ContentError('Unresolved source references/decode errors on '+key+': '+str(record['errors']))
            found.add(key)
            pending.extend(r['asset'] for r in record.get('references', []))
        return sorted(found)


def unbox(v: Any) -> Any:
    while isinstance(v, dict) and len(v)==1 and next(iter(v)) in ('Value','m_Value'):
        v = next(iter(v.values()))
    return v


def make_requirements(path: Path, *, track: str = 'Map_Race_ArlenSpeedway',
                      character: str = 'Character_KH_Hank', kart: str = 'Kart_KH_LandryLonghorner') -> dict:
    source = json.loads(path.read_text())
    definitions = {r['id']: (cls,r) for cls, entries in source['classes'].items() for r in entries}
    selected = [track, character, kart]
    expected = ('MapDefinition','CharacterDefinition','KartDefinition')
    for key, kind in zip(selected, expected):
        if key not in definitions or not definitions[key][0].endswith('.'+kind):
            raise ContentError(f'Unknown {kind}: {key}')
    # A kart's stock wheels are an explicit content dependency. Rewards/campaign
    # dependencies deliberately do not expand this slice into the whole game.
    wheels = definitions[kart][1]['data'].get('_stockWheel',{}).get('_refID')
    if wheels:
        matches = [k for k in definitions if k.casefold()==wheels.casefold()]
        if len(matches)!=1:
            raise ContentError('Unresolved stock-wheel definition: '+wheels)
        selected.append(matches[0])
    requirements=[]
    for key in sorted(selected):
        _, record = definitions[key]
        for field, value in walk(record['data']):
            requirement = None
            if isinstance(value, dict) and 'm_AssetGUID' in value:
                guid = value['m_AssetGUID']
                if guid:
                    if not re.fullmatch('[0-9a-fA-F]{32}', guid):
                        raise ContentError('Invalid Addressables GUID on '+key+field)
                    requirement={'kind':'addressable','guid':guid,'subobject':value.get('m_SubObjectName',''),'subobject_type':value.get('m_SubObjectType','')}
            elif isinstance(value, dict) and 'SceneName' in value:
                requirement={'kind':'scene','scene':value['SceneName']}
            elif isinstance(value,str) and value.startswith('event:/'):
                requirement={'kind':'fmod-event','event':value}
            if requirement:
                requirement.update(definition=key,field=field,status='unresolved')
                requirement['key']=hashlib.sha256(canonical([key,field])).hexdigest()
                requirements.append(requirement)
    return {'schema':SCHEMA,'status':'requirements-only','original_media_verified':False,
            'definitions_sha256':sha256_file(path),'definitions':sorted(selected),
            'requirements':sorted(requirements,key=lambda r:(r['definition'],r['field'])),
            'notes':['Definition references are requirements, not recovered media.',
                     'Addressables catalog mapping is required; names are not GUID evidence.',
                     'FMOD event identity is preserved; sound samples and event graphs remain separate gates.']}


def verify_catalog(directory: Path) -> dict:
    path=safe_child(directory,'catalog.json')
    catalog=json.loads(path.read_text())
    if catalog.get('schema')!=SCHEMA or catalog.get('status') not in ('indexed','incomplete'):
        raise ContentError('Unknown catalog schema or status')
    for record in catalog['objects']:
        if record.get('id') != object_id(record['file'],record['path_id']):
            raise ContentError('Invalid stable object identity')
    index=AssetIndex(catalog['files'],catalog['objects'])
    errors=len(catalog.get('errors',[]))+sum(len(o.get('errors',[])) for o in index.objects.values())
    expected_status='incomplete' if errors else 'indexed'
    if catalog['status']!=expected_status or not index.objects and not errors:
        raise ContentError('Catalog status disagrees with its actual decode/reference results')
    counts={'bundles':sum(s['kind']=='bundle' for s in catalog['sources']),
            'objects':len(index.objects),
            'types':dict(sorted(Counter(o['type'] for o in index.objects.values()).items())),
            'errors':errors}
    if catalog.get('counts')!=counts or counts['bundles']<1:
        raise ContentError('Catalog counts disagree with preserved source/object records')
    for record in index.objects.values():
        if record.get('id') != object_id(record['file'],record['path_id']):
            raise ContentError('Invalid stable object identity')
        for artifact in record.get('artifacts',[]):
            p=safe_child(directory,artifact['path'])
            verify_source(p,expected_sha256=artifact['sha256'],expected_size=artifact['bytes'])
        roles=[a['role'] for a in record.get('artifacts',[])]
        if len(roles)!=len(set(roles)):
            raise ContentError('Duplicate artifact role on '+record['id'])
        typed=next((a for a in record.get('artifacts',[]) if a['role']=='typetree'),None)
        if typed:
            tree=decode_tree(json.loads(safe_child(directory,typed['path']).read_text()))
            actual,missing=index.references(record['id'],tree)
            if record.get('references',[])!=actual:
                raise ContentError('Catalog edges disagree with preserved typetree on '+record['id'])
            if any(error not in record.get('errors',[]) for error in missing):
                raise ContentError('Catalog hides unresolved typetree pointers')
        elif not record.get('errors'):
            raise ContentError('Missing typetree without a recorded decode failure')
        for ref in record.get('references',[]):
            if ref['asset'] not in index.objects:
                raise ContentError('Dangling catalog reference: '+ref['asset'])
    return catalog


def resolve_requirements(plan: dict, catalog: dict, bindings: list[dict]) -> dict:
    """Apply explicit catalog bindings, with hashed source evidence, never names.

    Source catalogs may be compact/binary Addressables versions. Until a decoder
    is verified for this build, an analyst supplies exact key->asset bindings and
    a source file/hash. This is recorded as analyst-asserted, NOT auto-recovered.
    """
    index=AssetIndex(catalog['files'],catalog['objects'])
    known={r['key']:r for r in plan['requirements']}
    source_hashes={s['path']:s['sha256'] for s in catalog['sources']}
    mapped={}
    for binding in bindings:
        key=binding['key']
        evidence=binding.get('evidence',{})
        if key not in known or key in mapped or binding['asset'] not in index.objects:
            raise ContentError('Unknown/duplicate requirement or missing bound asset')
        if not evidence.get('source_key') or source_hashes.get(evidence.get('file'))!=evidence.get('sha256') or not evidence.get('sha256'):
            raise ContentError('Binding needs a catalog file/hash and exact source key')
        closure=index.closure([binding['asset']])
        mapped[key]={'asset':binding['asset'],'closure':closure,'evidence':evidence,'status':'analyst-bound-not-imported'}
    return {'schema':SCHEMA,'status':'bound-not-imported' if len(mapped)==len(known) else 'incomplete',
            'original_media_verified':False,'bindings':mapped,
            'unresolved':[r for r in plan['requirements'] if r['key'] not in mapped]}
