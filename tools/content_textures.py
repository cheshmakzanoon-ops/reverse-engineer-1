"""Evidence-only classification of empty font atlases, never placeholder pixels.

Zero dimensions alone are not an exemption from content validation. A matching
serialized Font or verified TMP_FontAsset owner is required by the final catalog.
Godot still needs an explicit font/atlas implementation; no image is manufactured.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
try:
    from .content_pipeline import ContentError, canonical, decode_raw_tree, safe_child
except ImportError:
    from content_pipeline import ContentError, canonical, decode_raw_tree, safe_child

EMPTY_CONVERSION = 'serialized-empty-image-not-renderable'
OWNER_ERROR = {'field': '', 'reason': 'image: serialized-empty texture has no verified font owner'}


def _integer(value, minimum: int = 0) -> bool:
    return type(value) is int and value >= minimum


def empty_texture_storage(tree: dict) -> dict | None:
    """Describe a narrowly verified empty serialization, without inferring use.

    Positive dimensions (and old adapters without dimension metadata) continue
    through the regular image decoder, where a missing image is still an error.
    A partially empty, contradictory or malformed declaration is not accepted.
    """
    if 'm_Width' not in tree and 'm_Height' not in tree:
        return None
    width, height = tree.get('m_Width'), tree.get('m_Height')
    if not _integer(width) or not _integer(height):
        raise ContentError('Texture dimensions must be nonnegative serialized integers')
    if width and height:
        return None
    if width != 0 or height != 0:
        raise ContentError('Partially empty texture dimensions are not a font atlas')
    data = tree.get('image data')
    stream = tree.get('m_StreamData')
    if (type(data) not in (bytes, bytearray, list) or len(data) != 0
            or not _integer(tree.get('m_CompleteImageSize')) or tree['m_CompleteImageSize'] != 0
            or not _integer(tree.get('m_ImageCount')) or tree['m_ImageCount'] not in (0, 1)
            or not _integer(tree.get('m_MipCount')) or tree['m_MipCount'] != 1
            or not _integer(tree.get('m_TextureFormat'), 1)
            or not isinstance(stream, dict) or type(stream.get('path')) is not str
            or stream['path'] != '' or not _integer(stream.get('offset')) or stream['offset'] != 0
            or not _integer(stream.get('size')) or stream['size'] != 0):
        raise ContentError('Empty texture has missing or conflicting serialized storage fields')
    return {'state': 'serialized-empty', 'dimensions': [0, 0], 'inline_bytes': 0,
            'complete_image_bytes': 0, 'stream': {'path': '', 'offset': 0, 'bytes': 0},
            'image_count': tree['m_ImageCount'], 'mip_count': tree['m_MipCount'],
            'texture_format': tree['m_TextureFormat'], 'runtime_image_available': False}


def image_owner_evidence(directory: Path, index) -> dict[str, list[dict]]:
    """Resolve ownership using file-scoped pointers and the source typetrees.

    Native fonts must embed source bytes and have no baked glyph rectangles.
    TMP owners additionally need the exact script class, dynamic population,
    clear-on-build policy, empty glyph tables and a retained source Font.
    Display names are not evidence. All unresolved references remain errors.
    """
    targets = {key: [] for key, r in index.objects.items() if 'image_storage' in r}
    if not targets:
        return targets
    trees, fonts = {}, {}

    def tree(key):
        if key not in trees:
            rec = index.objects[key]
            artifact = next((a for a in rec['artifacts'] if a['role'] == 'typetree'), None)
            if artifact is None:
                raise ContentError('Font owner has no retained typetree')
            trees[key] = decode_raw_tree(json.loads(safe_child(directory, artifact['path']).read_text()))
        return trees[key]

    def font_info(key):
        if key not in fonts:
            rec = index.objects[key]
            if rec['type'] != 'Font':
                return None
            value = tree(key)
            data = value.get('m_FontData')
            if type(data) is list:
                if any(type(n) is not int or not 0 <= n <= 255 for n in data):
                    return None
                data = bytes(data)
            if type(data) not in (bytes, bytearray) or not data:
                return None
            fonts[key] = ({'source_font': key, 'source_font_bytes': len(data),
                           'source_font_sha256': hashlib.sha256(data).hexdigest()},
                          value.get('m_CharacterRects') == [])
            # Real CJK font arrays can be large; retain digests, not a second font.
            trees.pop(key, None)
        return fonts[key]

    def resolve(owner, pointer):
        if not isinstance(pointer, dict):
            raise ContentError('Missing font relationship')
        target = index.resolve(index.objects[owner]['file'], pointer)
        if target is None:
            raise ContentError('Null font relationship')
        return target

    for owner, rec in sorted(index.objects.items()):
        if rec['type'] not in ('Font', 'MonoBehaviour'):
            continue
        edges = [e for e in rec.get('references', []) if e['asset'] in targets]
        if not edges:
            continue
        value = tree(owner)
        for edge in edges:
            evidence = None
            try:
                if rec['type'] == 'Font' and edge['field'] == '/m_Texture':
                    source = font_info(owner)
                    if source is not None and source[1] and resolve(owner, value.get('m_Texture')) == edge['asset']:
                        evidence = {'kind': 'embedded-font-atlas', **source[0]}
                elif rec['type'] == 'MonoBehaviour' and re.fullmatch(r'/m_AtlasTextures/\d+', edge['field']):
                    if (type(value.get('m_AtlasPopulationMode')) is not int or value['m_AtlasPopulationMode'] != 1
                            or type(value.get('m_ClearDynamicDataOnBuild')) not in (int, bool)
                            or value['m_ClearDynamicDataOnBuild'] != 1
                            or value.get('m_GlyphTable') != [] or value.get('m_CharacterTable') != []
                            or not _integer(value.get('m_AtlasWidth'), 1)
                            or not _integer(value.get('m_AtlasHeight'), 1)):
                        continue
                    script = resolve(owner, value.get('m_Script'))
                    if index.objects[script]['type'] != 'MonoScript':
                        continue
                    script_tree = tree(script)
                    if script_tree.get('m_ClassName') != 'TMP_FontAsset' or script_tree.get('m_Namespace') != 'TMPro':
                        continue
                    source = font_info(resolve(owner, value.get('m_SourceFontFile')))
                    if source is not None:
                        evidence = {'kind': 'tmp-dynamic-font-atlas', **source[0], 'script': script,
                                    'atlas_dimensions': [value['m_AtlasWidth'], value['m_AtlasHeight']]}
            except (ContentError, KeyError, TypeError):
                # No substitute owner is inferred from unresolved dependencies.
                continue
            if evidence is not None:
                targets[edge['asset']].append({'owner': owner, 'field': edge['field'], **evidence})
        trees.pop(owner, None)
    return {key: sorted(values, key=canonical) for key, values in targets.items()}


def verify_texture_record(record: dict, tree: dict) -> None:
    """Reject fabricated diagnostics/PNGs, including after manual catalog edits."""
    if record['type'] != 'Texture2D':
        if 'image_storage' in record or 'image_owners' in record:
            raise ContentError('Empty texture classification on a non-texture object')
        return
    try:
        expected = empty_texture_storage(tree)
    except ContentError as error:
        if ('image_storage' in record or 'image_owners' in record
                or not any(e['reason'] == 'image: '+str(error) for e in record['errors'])):
            raise ContentError('Malformed empty texture declaration is not a verified atlas') from error
        return
    if expected is None:
        if 'image_storage' in record or 'image_owners' in record:
            raise ContentError('Empty texture diagnostics disagree with source dimensions')
        return
    if canonical(record.get('image_storage')) != canonical(expected):
        raise ContentError('Empty texture diagnostics disagree with retained storage')
    if record.get('conversion') != EMPTY_CONVERSION or any(a['role'] == 'decoded-image' for a in record['artifacts']):
        raise ContentError('Serialized-empty texture cannot claim a decoded image')
