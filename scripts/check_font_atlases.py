#!/usr/bin/env python3
"""Verify the known empty atlases in the hash-locked original font bundle.

This is a targeted raw-storage/ownership gate, not complete asset recovery,
font rendering, or a complete two-bundle dependency graph. Private bundle and
font bytes are temporary; the report contains identifiers and digests only.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.content_batches import check_paths
from tools.content_pipeline import ContentError, canonical, safe_child, verify_source
from tools.content_unity import inventory, SceneReader
from tools.content_textures import EMPTY_CONVERSION

# Identities measured in the preceding verified extraction, relative to Data/.
SOURCE_INPUTS = (
    {'path': 'StreamingAssets/aa/StandaloneOSX/fonts_assets_all_e143ad6dd070c6b463520b5c462072e5.bundle',
     'bytes': 25309524,
     'sha256': '00147668c939824ec40abdce0e132eb6c70b31b45c13c7a2dce8e2ec83b820db'},
    {'path': 'StreamingAssets/aa/StandaloneOSX/f1262188c47284d550ee5cef921beff9_monoscripts_082fe319e80515f2379589a97f2b1caf.bundle',
     'bytes': 18932,
     'sha256': 'f925dfe53b9196827f470f8e822694587a55c1f3e431f6bf37f8e5027c08c65b'},
)
EXPECTED_EMPTY_IDS = frozenset({
    '-6978924017382949987', '-5184046834964197735', '-2298874796462745152',
    '1157173310028707644', '2531129067128397211', '8661133001235926989',
})


def check_atlases(data: Path, *, _loader=None, _mesh_decoder=None,
                  _sources=None, _expected_ids=None) -> dict:
    """Synthetic injection is labelled and never available through the CLI."""
    synthetic = any(x is not None for x in (_loader, _mesh_decoder, _sources, _expected_ids))
    sources = SOURCE_INPUTS if _sources is None else _sources
    expected = EXPECTED_EMPTY_IDS if _expected_ids is None else frozenset(_expected_ids)
    if not sources or not expected:
        raise ContentError('Expected sources and atlas identities are required')
    verified = []
    for source in sources:
        identity = verify_source(safe_child(data, source['path']),
                                 expected_sha256=source['sha256'], expected_size=source['bytes'])
        verified.append({'path': source['path'], **identity})
    with tempfile.TemporaryDirectory(prefix='kart-font-evidence-') as work:
        work = Path(work)
        selected, output = work/'sources', work/'catalog'
        check_paths(data, selected, output)
        for source in sources:
            target = safe_child(selected, source['path'])
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(safe_child(data, source['path']), target)
            verify_source(target, expected_sha256=source['sha256'], expected_size=source['bytes'])
        catalog = inventory(selected, output, loader=_loader, mesh_decoder=_mesh_decoder)
        # verify_catalog rechecks actual saved artifact bytes and ownership.
        reader = SceneReader(output)
        textures = [r for r in catalog['objects'] if r['type'] == 'Texture2D'
                    and any(r['file'].startswith(s['path']+'/') for s in sources[:1])]
        empty = [r for r in textures if 'image_storage' in r]
        if {r['path_id'] for r in empty} != expected or len(empty) != len(expected):
            raise ContentError('Original empty-atlas identities do not match the expected source set')
        evidence = []
        for record in sorted(empty, key=lambda r: int(r['path_id'])):
            if record['errors'] or not record.get('image_owners') or record['conversion'] != EMPTY_CONVERSION:
                raise ContentError('Empty atlas lacks verified raw storage and owner evidence')
            if {a['role'] for a in record['artifacts']} != {'raw-object', 'typetree'}:
                raise ContentError('Empty atlas has missing raw evidence or manufactured output')
            rejected = False
            try:
                reader.artifact(record['id'], 'decoded-image')
            except ContentError as error:
                rejected = 'no serialized pixels' in str(error)
            if not rejected:
                raise ContentError('Runtime image boundary accepted an empty atlas')
            evidence.append({
                'id': record['id'], 'file': record['file'], 'path_id': record['path_id'],
                'storage': record['image_storage'], 'owners': record['image_owners'],
                'raw_sha256': next(a['sha256'] for a in record['artifacts'] if a['role'] == 'raw-object'),
                'runtime_image_rejected': True,
            })
        # Reference errors from intentionally omitted shader/other bundles are
        # retained, not waived or described as successful catalog finalization.
        decoder_errors = [e for r in catalog['objects'] for e in r['errors']
                          if e['reason'].startswith(('decode:', 'image:', 'mesh:'))]
        if catalog['errors'] or decoder_errors:
            raise ContentError('Targeted bundle decoding still has recorded failures')
        for source in sources:
            verify_source(safe_child(data, source['path']),
                          expected_sha256=source['sha256'], expected_size=source['bytes'])
        return {
            'schema': 1, 'status': 'targeted-storage-verified',
            'source_status': 'synthetic-adapter-fixture' if synthetic else 'hash-verified-original-bundles',
            'sources': verified, 'objects': catalog['counts']['objects'],
            'empty_atlases': evidence, 'decoded_images': sum(
                a['role'] == 'decoded-image' for r in catalog['objects'] for a in r['artifacts']),
            'decoder_errors': 0, 'subset_catalog_status': catalog['status'],
            'subset_reference_errors': catalog['counts']['errors'],
            'original_media_complete': False, 'godot_imported': False,
            'original_source_recovered': False, 'font_rendering_verified': False,
        }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--data', type=Path, help='Already extracted original Data directory')
    source.add_argument('--drive-id', help='Owner-authorized source; fixed DMG hash still required')
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.report.exists() or args.report.is_symlink():
        parser.error('Use a new report path; existing evidence is never overwritten')
    if args.data:
        check_paths(args.data, args.report)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    try:
        if args.data:
            report = check_atlases(args.data)
        else:
            from scripts.recover_source import download_original, extract_original, find_application
            with tempfile.TemporaryDirectory(prefix='kart-font-source-') as work:
                work = Path(work)
                logs = work/'logs'; logs.mkdir()
                original = download_original(args.drive_id, work)
                extract_original(original, work/'extracted', logs,
                                 shutil.which('7zz') or shutil.which('7z') or '7zz')
                data = find_application(work/'extracted')/'Resources/Data'
                report = check_atlases(data)
        with args.report.open('xb') as stream:
            stream.write(canonical(report))
        print('FONT_ATLAS_GATE: targeted-storage-verified; no font rendering or complete catalog claim')
        return 0
    except (ContentError, OSError, ValueError, KeyError, TypeError) as error:
        # No raw typetrees, font bytes, private paths or decoder dumps in artifacts.
        report = {'schema': 1, 'status': 'failed', 'error_type': type(error).__name__,
                  'original_media_complete': False, 'godot_imported': False,
                  'original_source_recovered': False, 'font_rendering_verified': False}
        with args.report.open('xb') as stream:
            stream.write(canonical(report))
        print('FONT_ATLAS_GATE: failed ('+type(error).__name__+'); no success inferred', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
