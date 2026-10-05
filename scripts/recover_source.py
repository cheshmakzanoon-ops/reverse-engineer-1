#!/usr/bin/env python3
"""Verify the owner's DMG, extract without executing it, and inventory real bundles.

The media workspace is deliberately separate from the shareable reports directory.
Only metadata, counts, hashes and diagnostics are published by the companion job.
This does not recover original C# method bodies or claim a playable Android port.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.content_pipeline import ContentError, DMG_BYTES, DMG_SHA256, canonical, sha256_file, verify_source


def drive_id(value: str) -> str:
    if not re.fullmatch(r'[A-Za-z0-9_-]{3,200}', value):
        raise ValueError('Supply a Drive file ID, not a URL or shell expression')
    return value


def run(command: list[str], log: Path, *, timeout: float = 1800) -> None:
    """No shell interpolation; preserve diagnostics and reject failures/timeouts."""
    from tools.content_batches import run_bounded
    try:
        run_bounded(command, log, timeout)
    except subprocess.TimeoutExpired as exc:
        raise ContentError(f'Command timed out; inspect {log.name}') from exc
    except subprocess.CalledProcessError as exc:
        raise ContentError(f'Command failed with exit {exc.returncode}; inspect {log.name}') from exc


def prepare_directories(workspace: Path, reports: Path) -> None:
    a, b = workspace.resolve(), reports.resolve()
    if a == b or a.is_relative_to(b) or b.is_relative_to(a):
        raise ContentError('Media workspace and shareable reports must be separate')
    if workspace.exists() or workspace.is_symlink() or reports.exists() or reports.is_symlink():
        raise ContentError('Use new workspace and report directories; existing evidence is never overwritten')
    workspace.mkdir(parents=True)
    reports.mkdir(parents=True)


def download_original(identity: str, workspace: Path) -> Path:
    """Public-link download. No browser cookies, OAuth tokens or TLS bypass."""
    import gdown
    partial = workspace / 'source.dmg.part'
    # Quiet avoids logging transient download URLs. Verification is independent
    # of the downloader's return status and checks the fixed known original.
    result = gdown.download(id=drive_id(identity), output=str(partial), quiet=True, use_cookies=False)
    if result is None:
        raise ContentError('Drive did not supply the original file; check link access or quota')
    verify_source(partial, expected_size=DMG_BYTES, expected_sha256=DMG_SHA256)
    original = workspace / 'source.dmg'
    partial.rename(original)
    return original


def extract_original(original: Path, extracted: Path, reports: Path, sevenzip: str) -> None:
    # Verified original contains an installer shortcut to /Applications outside
    # the game .app. Exclude only that entry; never enable unsafe link extraction
    # or treat a nonzero extractor exit as success.
    run([sevenzip, 'x', '-y', '-bsp0', '-bso0',
         '-x!Warped Kart Racers/Applications', '-o' + str(extracted), str(original)],
        reports / 'extraction.log')


def find_application(extracted: Path) -> Path:
    found = [p.parent.parent for p in extracted.rglob('Frameworks/GameAssembly.dylib')
             if p.is_file() and not p.is_symlink() and p.parent.parent.name == 'Contents'
             and p.parent.parent.parent.suffix == '.app']
    if len(found) != 1:
        raise ContentError(f'Expected one extracted application with GameAssembly.dylib; found {len(found)}')
    return found[0]


def extracted_manifest(root: Path) -> list[dict]:
    found = []
    for path in sorted(root.rglob('*')):
        if any(':com.apple.' in p or p.startswith('._') or p == '.DS_Store' for p in path.relative_to(root).parts):
            continue
        if path.is_symlink():
            raise ContentError('Extracted symlink is not followed: ' + path.relative_to(root).as_posix())
        if path.is_file():
            found.append({'path': path.relative_to(root).as_posix(), 'bytes': path.stat().st_size,
                          'sha256': sha256_file(path)})
    if not found:
        raise ContentError('Extraction contains no regular files')
    return found


def binary_metadata(content: Path) -> dict:
    binary = content / 'Frameworks/GameAssembly.dylib'
    metadata = content / 'Resources/Data/il2cpp_data/Metadata/global-metadata.dat'
    formats = {b'\xca\xfe\xba\xbe': 'universal-big-endian', b'\xbe\xba\xfe\xca': 'universal-little-endian',
               b'\xcf\xfa\xed\xfe': 'mach-o-64-little-endian', b'\xfe\xed\xfa\xcf': 'mach-o-64-big-endian'}
    for path in (binary, metadata):
        if not path.is_file() or path.is_symlink() or path.stat().st_size < 8:
            raise ContentError('Missing/truncated original analysis input: ' + path.name)
    with binary.open('rb') as stream:
        magic = stream.read(4)
    with metadata.open('rb') as stream:
        header = stream.read(8)
    if magic not in formats or struct.unpack('<I', header[:4])[0] != 0xFAB11BAF:
        raise ContentError('Original native/IL2CPP input headers do not match the expected formats')
    return {'native_format': formats[magic], 'metadata_version': struct.unpack('<I', header[4:])[0],
            'native_binary': verify_source(binary), 'metadata': verify_source(metadata),
            'original_source_recovered': False}


def inventory_summary(catalog: dict) -> dict:
    groups = Counter()
    candidates = []
    for obj in catalog.get('objects', []):
        for error in obj.get('errors', []):
            groups[(obj['type'], error.get('reason', 'unknown'))] += 1
        if re.search('arlen|hank|longhorn|landry', obj.get('name', ''), re.I):
            candidates.append({k: obj.get(k) for k in ('id', 'file', 'path_id', 'type', 'name', 'conversion')})
    return {'status': catalog['status'], 'source_status': catalog.get('source_status'),
            'counts': catalog.get('counts', {}), 'bundle_errors': catalog.get('errors', []),
            'error_groups': [{'type': kind, 'reason': reason, 'count': count}
                             for (kind, reason), count in sorted(groups.items(), key=lambda p: (-p[1], p[0]))],
            'candidates': sorted(candidates, key=lambda o: (o['type'], o['name'], o['id'])),
            'godot_imported': False, 'original_source_recovered': False, 'complete_game': False}


def inventory_command(content: Path, catalog: Path, checkpoints: Path, progress: Path,
                      *, bundle_timeout: float, budget: float, pass_budget: float = 900,
                      max_passes: int = 8, max_bundle_timeout: float = 480) -> list[str]:
    """Metadata-only report lives outside private batch payloads and worker logs."""
    from tools.content_continuation import validate_policy
    validate_policy(budget=budget, pass_budget=pass_budget, bundle_timeout=bundle_timeout,
                    max_bundle_timeout=max_bundle_timeout, max_passes=max_passes, stall_limit=2)
    return [sys.executable, str(ROOT / 'tools/recover_content.py'), 'inventory',
            str(content / 'Resources/Data'), str(catalog), '--checkpoints', str(checkpoints),
            '--bundle-timeout', str(bundle_timeout), '--budget', str(budget),
            '--progress-report', str(progress), '--continue-until-complete',
            '--pass-budget', str(pass_budget), '--max-passes', str(max_passes),
            '--max-bundle-timeout', str(max_bundle_timeout),
            '--continuation-report', str(progress.with_name('inventory-continuation.json'))]


def finalization_eligible(progress: dict) -> bool:
    """Only completed-bundle budget/timeouts may enter the dedicated finalizer.

    A decoder/integrity failure, partial bundle set or finalized-but-incomplete
    catalog remains terminal. The finalizer independently verifies all receipts.
    """
    if not isinstance(progress, dict):
        return False
    total = progress.get('total_bundles')
    completed = progress.get('completed_bundles')
    failures = progress.get('failures')
    return (progress.get('schema') == 1 and progress.get('status') == 'incomplete'
            and progress.get('phase') == 'awaiting-finalization'
            and type(total) is int and total > 0 and type(completed) is int
            and completed == total and isinstance(failures, list)
            and all(isinstance(f, dict) and f.get('phase') == 'finalizing'
                    and f.get('reason') in ('timeout', 'TimeoutExpired') for f in failures))


def finalization_command(content: Path, catalog: Path, checkpoints: Path,
                         workspace: Path, progress: Path, *, budget: float) -> list[str]:
    from tools.content_batches import _positive
    _positive(budget, 'finalization budget')
    command = [sys.executable, str(ROOT / 'tools/content_finalize.py'),
               str(content / 'Resources/Data'), str(catalog), '--checkpoints', str(checkpoints),
               '--workspace', str(workspace), '--budget', str(budget),
               '--progress-report', str(progress)]
    if workspace.exists():
        command.append('--resume')
    return command


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--drive-id', type=drive_id)
    source.add_argument('--dmg', type=Path)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--reports', type=Path, required=True)
    parser.add_argument('--sevenzip', default=shutil.which('7zz') or shutil.which('7z') or '7zz')
    parser.add_argument('--bundle-timeout', type=float, default=120)
    parser.add_argument('--inventory-budget', type=float, default=3600)
    parser.add_argument('--inventory-pass-budget', type=float, default=900)
    parser.add_argument('--inventory-max-passes', type=int, default=8)
    parser.add_argument('--max-bundle-timeout', type=float, default=480)
    parser.add_argument('--finalization-budget', type=float, default=1800,
                        help='Separate bounded catalog-finalization budget after every bundle is sealed')
    parser.add_argument('--restore-checkpoints', type=Path)
    parser.add_argument('--checkpoint-key', type=Path,
                        help='Private receiving key file outside Git; required with --restore-checkpoints')
    args = parser.parse_args(argv)
    report = {'schema': 1, 'source_commit': os.environ.get('GITHUB_SHA', 'local'), 'status': 'started',
              'source_verified': False, 'extracted': False, 'inventory_complete': False,
              'original_source_recovered': False, 'godot_imported': False, 'complete_game': False}
    prepared = False
    try:
        from tools.content_continuation import validate_policy
        validate_policy(budget=args.inventory_budget, pass_budget=args.inventory_pass_budget,
                        bundle_timeout=args.bundle_timeout, max_bundle_timeout=args.max_bundle_timeout,
                        max_passes=args.inventory_max_passes, stall_limit=2)
        from tools.content_batches import _positive
        _positive(args.finalization_budget, 'finalization budget')
        if bool(args.restore_checkpoints) != bool(args.checkpoint_key):
            raise ContentError('--restore-checkpoints and --checkpoint-key must be supplied together')
        prepare_directories(args.workspace, args.reports)
        prepared = True
        original = args.dmg if args.dmg else download_original(args.drive_id, args.workspace)
        report['source'] = verify_source(original, expected_size=DMG_BYTES, expected_sha256=DMG_SHA256)
        report['source_verified'] = True
        print('SOURCE_VERIFIED ' + json.dumps(report['source']), flush=True)
        extracted = args.workspace / 'extracted'
        extract_original(original, extracted, args.reports, args.sevenzip)
        content = find_application(extracted)
        manifest = extracted_manifest(content)
        report['analysis_inputs'] = binary_metadata(content)
        report['extracted'] = True
        report['extracted_files'] = len(manifest)
        report['extracted_bytes'] = sum(f['bytes'] for f in manifest)
        (args.reports / 'extracted-files.json').write_bytes(canonical(manifest))
        print('EXTRACTED ' + json.dumps({'files': len(manifest), 'bytes': report['extracted_bytes']}), flush=True)
        catalog_dir = args.workspace / 'content-catalog'
        checkpoints = args.workspace / 'content-checkpoints'
        if args.restore_checkpoints:
            if not args.checkpoint_key:
                raise ContentError('Restoring checkpoints requires the owner-held private key')
            from tools.content_checkpoints import restore_checkpoints
            from tools.content_unity import source_files
            restore_checkpoints(args.restore_checkpoints, args.checkpoint_key.read_bytes(), checkpoints,
                                expected_sources=source_files(content / 'Resources/Data'))
        # Child process bounds decoder failure and preserves its diagnostics.
        # The inventory itself keeps raw media OUTSIDE the report directory.
        error = None
        try:
            command = inventory_command(content, catalog_dir, checkpoints,
                                        args.reports / 'inventory-progress.json',
                                        bundle_timeout=args.bundle_timeout, budget=args.inventory_budget,
                                        pass_budget=args.inventory_pass_budget, max_passes=args.inventory_max_passes,
                                        max_bundle_timeout=args.max_bundle_timeout)
            if args.restore_checkpoints: command.append('--resume')
            run(command,
                args.reports / 'inventory.log', timeout=args.inventory_budget + 600)
        except ContentError as exc:
            error = str(exc)
        progress_path = args.reports / 'inventory-progress.json'
        if progress_path.is_file():
            report['inventory_progress'] = json.loads(progress_path.read_text())
        continuation_path = args.reports / 'inventory-continuation.json'
        if continuation_path.is_file():
            report['inventory_continuation'] = json.loads(continuation_path.read_text())
        # Decoding all original bundles exhausted the historical shared budget.
        # Do not re-decode them or mistake this boundary for a corrupt asset.
        # This separate process retains copies/edge chunks, uses its own budget,
        # and still invokes the unchanged full catalog validator before publish.
        if finalization_eligible(report.get('inventory_progress')):
            report['inventory_stage_error'] = error
            try:
                run(finalization_command(content, catalog_dir, checkpoints,
                                         args.workspace / 'content-finalization',
                                         args.reports / 'finalization-progress.json',
                                         budget=args.finalization_budget),
                    args.reports / 'finalization.log', timeout=args.finalization_budget + 120)
                error = None
            except ContentError as exc:
                error = str(exc)
            final_progress = args.reports / 'finalization-progress.json'
            if final_progress.is_file():
                report['finalization_progress'] = json.loads(final_progress.read_text())
        catalog_file = catalog_dir / 'catalog.json'
        if catalog_file.is_file():
            catalog = json.loads(catalog_file.read_text())
            summary = inventory_summary(catalog)
            (args.reports / 'inventory-summary.json').write_bytes(canonical(summary))
            # This index has object IDs/types/names, references and artifact HASHES,
            # not typetrees, pixels, meshes, audio, native code or font bytes.
            (args.reports / 'catalog-index.json').write_bytes(canonical(catalog))
            report['inventory'] = {k: summary[k] for k in ('status', 'source_status', 'counts')}
            report['inventory_complete'] = catalog['status'] == 'indexed' and error is None
        verify_source(original, expected_size=DMG_BYTES, expected_sha256=DMG_SHA256)
        if error:
            raise ContentError(error)
        if not report['inventory_complete']:
            raise ContentError('No complete verified content inventory was produced')
        report['status'] = 'inventoried-not-ported'
        return 0
    except (ContentError, OSError, ValueError, ImportError, subprocess.SubprocessError) as exc:
        report['status'] = 'failed'
        report['error'] = type(exc).__name__ + ': ' + str(exc)
        print('SOURCE RECOVERY FAILED: ' + report['error'], file=sys.stderr, flush=True)
        return 1
    finally:
        if prepared:
            (args.reports / 'recovery.json').write_bytes(canonical(report))
        print(json.dumps(report, indent=2), flush=True)


if __name__ == '__main__':
    raise SystemExit(main())
