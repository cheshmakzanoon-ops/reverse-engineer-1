#!/usr/bin/env python3
"""Verify Arlen catalog locations against the exact owner-authorized source.

The source manifest digest comes from run 37359017829's checksum-verified
metadata artifact 11368488005. No original catalog, typetree, pixels, font,
native code, credentials or media bytes enter the public report.
"""
from __future__ import annotations
import argparse
import hashlib
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.content_addressables import CompactCatalog, resolve_locations
from tools.content_batches import check_paths
from tools.content_pipeline import ContentError, canonical, make_requirements, safe_child, verify_source
from scripts.recover_source import download_original, extract_original, extracted_manifest, find_application

CATALOG_PATH = 'StreamingAssets/aa/catalog.json'
CATALOG_BYTES = 1505032
CATALOG_SHA256 = '742d72cb89e182f97c3656fef22eac67a863da6cfd5a2a8940a5822a57f6b9be'
EXTRACTED_MANIFEST_SHA256 = '89ed0deffe5bad3a93e38b769037fe943071185c748e65fa9dd814291f0db25b'


def inspect_original(data: Path, report: dict) -> None:
    report['phase'] = 'verify-extracted-source'
    if data.name != 'Data' or data.parent.name != 'Resources' or data.parent.parent.name != 'Contents':
        raise ContentError('Expected original Contents/Resources/Data layout')
    manifest = extracted_manifest(data.parent.parent)
    digest = hashlib.sha256(canonical(manifest)).hexdigest()
    if digest != EXTRACTED_MANIFEST_SHA256:
        raise ContentError('Extracted application manifest does not match the verified original')
    report['extracted_source'] = {'manifest_sha256': digest, 'files': len(manifest)}
    catalog_path = safe_child(data, CATALOG_PATH)
    identity = verify_source(catalog_path, expected_sha256=CATALOG_SHA256, expected_size=CATALOG_BYTES)
    report['catalog_source'] = {'path': CATALOG_PATH, **identity}
    report['phase'] = 'parse-catalog'
    catalog = CompactCatalog.load(catalog_path)
    report['catalog_structure'] = catalog.summary()
    report['phase'] = 'resolve-locations'
    requirements = make_requirements(ROOT/'work/assets/definitions/definitions.json')
    result = resolve_locations(catalog, requirements, data_root=data)
    report['location_plan'] = result
    # Recheck every selected payload against the source manifest rather than
    # calling arbitrary bytes original merely because a filename matches.
    source_by_path = {item['path']: item for item in manifest}
    for requirement in result['requirements']:
        for bundle in requirement.get('bundles', []):
            source = source_by_path.get('Resources/Data/' + bundle.get('path', ''))
            if source is None or any(bundle.get(k) != source[k] for k in ('bytes', 'sha256')):
                raise ContentError('Resolved bundle identity does not match original source manifest')
    verify_source(catalog_path, expected_sha256=CATALOG_SHA256, expected_size=CATALOG_BYTES)
    report['source_status'] = 'hash-verified-original-application-and-catalog'
    report['phase'] = 'finished'
    report['status'] = ('original-catalog-locations-verified' if all(
        item['status'] == 'location-resolved-not-object-bound'
        for item in result['requirements'] if item['requirement']['kind'] != 'fmod-event')
        else 'incomplete-location-resolution')


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--data', type=Path)
    source.add_argument('--drive-id')
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.report.exists() or args.report.is_symlink():
        parser.error('Use a new report path; evidence is never overwritten')
    if args.data:
        check_paths(args.data, args.report)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    report = {'schema': 1, 'scope': 'original catalog location/bundle resolution, not serialized-object binding',
              'status': 'started', 'phase': 'source-acquisition', 'original_dmg_verified': False,
              'original_media_complete': False, 'original_source_recovered': False,
              'object_bindings_complete': False, 'godot_imported': False}
    try:
        if args.data:
            inspect_original(args.data, report)
        else:
            with tempfile.TemporaryDirectory(prefix='kart-addressables-source-') as work:
                work = Path(work)
                logs = work/'logs'; logs.mkdir()
                original = download_original(args.drive_id, work)
                report['original_dmg_verified'] = True
                extract_original(original, work/'extracted', logs,
                                 shutil.which('7zz') or shutil.which('7z') or '7zz')
                inspect_original(find_application(work/'extracted')/'Resources/Data', report)
        return int(report['status'] != 'original-catalog-locations-verified')
    except (ContentError, OSError, KeyError, TypeError, ValueError) as error:
        report['status'] = 'failed'
        report['error_type'] = type(error).__name__
        # Parser errors contain fixed diagnostic messages, not source payloads.
        if report['phase'] == 'parse-catalog' and isinstance(error, ContentError):
            report['diagnostic'] = str(error)[:200]
        return 1
    finally:
        with args.report.open('xb') as stream:
            stream.write(canonical(report))
        print('ADDRESSABLES_GATE: ' + report['status'] + '; phase=' + report['phase']
              + '; original code/media integration is not claimed')


if __name__ == '__main__':
    raise SystemExit(main())
