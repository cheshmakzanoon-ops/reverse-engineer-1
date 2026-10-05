#!/usr/bin/env python3
"""Resume catalog assembly from sealed bundles without changing their decoder recipe.

Private copied artifacts and resolved-edge chunks survive interruption. Every
invocation revalidates the original inputs and sealed receipts; the unchanged
verify_catalog gate recomputes all edges before publication. A cached edge is an
optimization, never authority. The legacy five-module decoder stays unchanged.
"""
from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Iterable
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time

if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.content_batches import (bundle_key, check_paths, exclusive, read_json,
                                  recipe, validate_batch, write_json, _positive)
from tools.content_continuation import controller_lock
from tools.content_pipeline import (AssetIndex, ContentError, SCHEMA, UNITY_VERSION,
    UNITYPY_VERSION, canonical, decode_raw_tree, safe_child, sha256_file,
    verify_catalog, verify_source)
from tools.content_textures import image_owner_evidence, OWNER_ERROR
from tools.content_unity import source_files

CHUNK_SIZE = 256


class BudgetPause(Exception):
    """Cooperative pause, not a validation error or completed catalog."""


def finalizer_recipe() -> dict:
    return {'schema': 1, 'code_sha256': sha256_file(Path(__file__)),
            'decoder': recipe(), 'edge_chunk_size': CHUNK_SIZE}


def _copy_artifact(source: Path, destination: Path, temporary: Path, artifact: dict) -> bool:
    """Publish an independent verified copy, never a hard link to checkpoint bytes.

    The deterministic temporary is outside the catalog. An interrupted copy is
    discarded, but a published copy is hash-checked and reused without rewriting.
    """
    expected = {'expected_sha256': artifact['sha256'], 'expected_size': artifact['bytes']}
    if destination.exists():
        verify_source(destination, **expected)
        return False
    safe_child(temporary.parent, temporary.name)
    temporary.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if temporary.exists():
        if not temporary.is_file():
            raise ContentError('Unexpected pending copy entry')
        temporary.unlink()
    try:
        with source.open('rb') as inp, temporary.open('xb') as out:
            shutil.copyfileobj(inp, out)
            out.flush()
            os.fsync(out.fileno())
        verify_source(temporary, **expected)
        # Atomic create-only publication. The link refers to our COPY, not source.
        os.link(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def _check_origin(catalog: dict, sources: list[dict], files: list[dict],
                  objects: Iterable[dict], errors: list[dict]) -> None:
    """A valid but unrelated/omitting catalog cannot satisfy the sealed plan."""
    if (catalog['sources'] != sources or catalog['files'] != sorted(files, key=lambda f: f['key'])
            or catalog['errors'] != errors or catalog.get('original_media_complete') is not False
            or catalog.get('godot_imported') is not False):
        raise ContentError('Published catalog differs from sealed inputs')
    actual = {r['id']: r for r in catalog['objects']}
    if len(actual) != len(catalog['objects']):
        raise ContentError('Published catalog has duplicate objects')
    for raw in objects:
        item = actual.pop(raw['id'], None)
        if item is None:
            raise ContentError('Published catalog has missing or duplicate objects')
        # Only these fields are added/recomputed by finalization.
        if ({k: v for k, v in item.items() if k not in ('references', 'errors', 'image_owners')}
                != {k: v for k, v in raw.items() if k not in ('references', 'errors', 'image_owners')}
                or item['errors'][:len(raw['errors'])] != raw['errors']):
            raise ContentError('Published catalog changed a decoded object')
    if actual:
        raise ContentError('Published catalog has unexpected objects')


def finalize_catalog(root: Path, output: Path, checkpoints: Path, workspace: Path, *,
                     resume: bool = False, budget: float = 1800,
                     report_path: Path | None = None, _observe=None) -> dict:
    """Finalize only a complete compatible sealed set; no Unity decoding occurs.

    Budget is cooperative between I/O units/chunks. The caller must also bound
    this process, especially the final unchanged full validator. Workspace must
    be private, outside source/checkpoints/output, and on output's filesystem.
    `_observe` is a test-only interruption hook and cannot authorize cache reuse.
    """
    _positive(budget, 'finalization budget')
    lock_path = checkpoints.with_name(checkpoints.name + '.continuation.lock')
    paths = [root, output, checkpoints, workspace, lock_path]
    if report_path is not None:
        paths.append(report_path)
    check_paths(*paths)
    if not checkpoints.is_dir():
        raise ContentError('Finalization requires existing sealed checkpoints')
    existed = workspace.exists()
    if existed and not resume:
        raise ContentError('Finalization workspace exists; explicitly request --resume')
    if not existed and resume:
        raise ContentError('Cannot resume a missing finalization workspace')
    started = time.monotonic()
    deadline = started + budget
    report = {'schema': 1, 'status': 'incomplete', 'phase': 'checking-inputs',
              'completed_bundles': 0, 'total_bundles': 0,
              'copied_artifacts': 0, 'reused_artifacts': 0, 'objects_resolved': 0,
              'reused_edge_chunks': 0, 'original_media_complete': False,
              'godot_imported': False, 'original_source_recovered': False}

    def emit(phase: str, **fields):
        report.update(phase=phase, **fields)
        report['elapsed_seconds'] = max(0.0, time.monotonic() - started)
        if report_path is not None:
            write_json(report_path, report)
        if _observe is not None:
            _observe(dict(report))

    def boundary():
        if time.monotonic() >= deadline:
            raise BudgetPause

    # Shares BOTH locks with the existing controller and standalone decoder.
    with controller_lock(checkpoints), exclusive(checkpoints):
        emit('checking-inputs')
        try:
            state_path = checkpoints / 'state.json'
            state_identity = verify_source(state_path)
            state = read_json(state_path)
            if state.get('schema') != 1 or state.get('recipe') != recipe():
                raise ContentError('Sealed decoder recipe is incompatible')
            if type(state.get('synthetic')) is not bool:
                raise ContentError('Unknown checkpoint source status')
            sources = source_files(root)
            if sources != state.get('sources'):
                raise ContentError('Original sources differ from sealed checkpoints')
            bundles = [s for s in sources if s['kind'] == 'bundle']
            if not bundles or set(state.get('completed', {})) != {bundle_key(s) for s in bundles}:
                raise ContentError('Finalization requires every source bundle to be sealed')
            report['total_bundles'] = len(bundles)
            plan = {'schema': 1, 'checkpoint_state': state_identity,
                    'finalizer': finalizer_recipe(), 'output': str(output.resolve())}
            owner = workspace / 'owner.json'
            if existed:
                if not workspace.is_dir() or read_json(owner) != plan:
                    raise ContentError('Finalization source, output or recipe changed')
            else:
                if output.exists() or output.is_symlink():
                    raise ContentError('Use a new catalog output')
                workspace.mkdir(parents=True, mode=0o700)
                write_json(owner, plan)
            output.parent.mkdir(parents=True, exist_ok=True)
            if workspace.stat().st_dev != output.parent.stat().st_dev:
                raise ContentError('Finalization workspace and output must share a filesystem')
            stage = safe_child(workspace, 'catalog')
            files, objects, errors = [], [], []
            # Keep only metadata in RAM. The original bundle bytes remain private.
            for source in bundles:
                boundary()
                key = bundle_key(source)
                folder = safe_child(checkpoints, 'bundles/' + key)
                value = validate_batch(folder, source, state['completed'][key])
                files.extend(value['files'])
                objects.extend(value['objects'])
                errors.extend(value['errors'])
                report['completed_bundles'] += 1
                emit('checking-batches')
            def sealed_objects():
                # Stream origin checks one receipt at a time during the final
                # verification pass, rather than retaining two complete catalogs.
                for source in bundles:
                    key = bundle_key(source)
                    path = safe_child(checkpoints, 'bundles/' + key + '/batch.json')
                    verify_source(path, expected_sha256=state['completed'][key]['sha256'],
                                  expected_size=state['completed'][key]['bytes'])
                    yield from read_json(path)['objects']

            if output.exists():
                del objects
                # Recover publication-before-report crash without replacing output.
                receipt = read_json(safe_child(workspace, 'ready.json'))
                legacy = read_json(safe_child(checkpoints, 'final.json'))
                if receipt != legacy or receipt.get('output') != str(output.resolve()):
                    raise ContentError('Existing output is not owned by this finalization')
                verify_source(safe_child(output, 'catalog.json'),
                              expected_sha256=receipt['manifest']['sha256'],
                              expected_size=receipt['manifest']['bytes'])
                emit('validating-published')
                catalog = verify_catalog(output)
                _check_origin(catalog, sources, files, sealed_objects(), errors)
                if catalog['status'] != receipt['status'] or catalog['counts'] != receipt['counts']:
                    raise ContentError('Published receipt disagrees with catalog')
                emit('finished', status=catalog['status'], counts=catalog['counts'])
                return report
            # Only finalization-owned lists are mutated. Share immutable raw
            # artifact metadata instead of duplicating the entire production set.
            index = AssetIndex(files, ({**r, 'references': list(r['references']),
                                       'errors': list(r['errors'])} for r in objects))
            stage.mkdir(exist_ok=True, mode=0o700)
            seen = set()
            emit('copying')
            for source in bundles:
                folder = safe_child(checkpoints, 'bundles/' + bundle_key(source))
                # Read already validated receipt; validate its digest again before reuse.
                verify_source(folder / 'batch.json',
                              expected_sha256=state['completed'][bundle_key(source)]['sha256'],
                              expected_size=state['completed'][bundle_key(source)]['bytes'])
                for record in read_json(folder / 'batch.json')['objects']:
                    for artifact in record['artifacts']:
                        boundary()
                        relative = artifact['path']
                        if relative in seen:
                            raise ContentError('Duplicate catalog artifact path')
                        seen.add(relative)
                        copied = _copy_artifact(safe_child(folder, relative), safe_child(stage, relative),
                                                safe_child(workspace, 'pending/copy.part'), artifact)
                        field = 'copied_artifacts' if copied else 'reused_artifacts'
                        report[field] += 1
                        if (report['copied_artifacts'] + report['reused_artifacts']) % 128 == 0:
                            emit('copying')
            # Only artifacts from sealed records may enter the published directory.
            for path in stage.rglob('*'):
                rel = path.relative_to(stage).as_posix()
                safe_child(stage, rel)
                if not path.is_dir() and rel not in seen and rel != 'catalog.json':
                    raise ContentError('Unexpected staged catalog entry')
            emit('resolving')
            ordered = sorted(index.objects)
            graph_identity = hashlib.sha256(canonical(plan)).hexdigest()
            for offset in range(0, len(ordered), CHUNK_SIZE):
                boundary()
                identities = ordered[offset:offset + CHUNK_SIZE]
                cache = safe_child(workspace, f'edges/{offset // CHUNK_SIZE:08d}.json')
                receipt_path = safe_child(workspace, f'edges/{offset // CHUNK_SIZE:08d}.receipt.json')
                if cache.exists() and receipt_path.exists():
                    receipt = read_json(receipt_path)
                    if receipt.get('graph_identity') != graph_identity:
                        raise ContentError('Cached graph belongs to different inputs')
                    verify_source(cache, expected_sha256=receipt['sha256'], expected_size=receipt['bytes'])
                    rows = read_json(cache)['rows']
                    if [r['id'] for r in rows] != identities:
                        raise ContentError('Cached graph has missing or unexpected objects')
                    report['reused_edge_chunks'] += 1
                else:
                    rows = []
                    for identity in identities:
                        record = index.objects[identity]
                        typed = next((a for a in record['artifacts'] if a['role'] == 'typetree'), None)
                        refs, missing = [], []
                        if typed is not None:
                            tree = decode_raw_tree(json.loads(safe_child(stage, typed['path']).read_text()))
                            refs, missing = index.references(identity, tree)
                        rows.append({'id': identity, 'references': refs, 'errors': missing})
                    write_json(cache, {'rows': rows})
                    write_json(receipt_path, {'graph_identity': graph_identity, **verify_source(cache)})
                for row in rows:
                    record = index.objects[row['id']]
                    record['references'] = row['references']
                    record['errors'].extend(row['errors'])
                report['objects_resolved'] += len(identities)
                emit('resolving')
            boundary()
            emit('font-ownership')
            for identity, owners in image_owner_evidence(stage, index).items():
                record = index.objects[identity]
                record['image_owners'] = owners
                if not owners:
                    record['errors'].append(dict(OWNER_ERROR))
            object_errors = sum(len(o['errors']) for o in index.objects.values())
            catalog = {'schema': SCHEMA, 'status': 'incomplete' if errors or object_errors else 'indexed',
                'source_status': 'synthetic-adapter-fixture' if state['synthetic'] else 'unity-bundle-bytes-read',
                'unity_version': UNITY_VERSION, 'unitypy_version': None if state['synthetic'] else UNITYPY_VERSION,
                'sources': sources, 'files': sorted(files, key=lambda f: f['key']),
                'objects': sorted(index.objects.values(), key=lambda o: o['id']), 'errors': errors,
                'counts': {'bundles': len(bundles), 'objects': len(index.objects),
                           'types': dict(sorted(Counter(o['type'] for o in index.objects.values()).items())),
                           'errors': len(errors) + object_errors},
                'original_media_complete': False, 'godot_imported': False,
                'limitations': ['Addressables GUIDs need catalog evidence, not filename matching.',
                                'Raw Animator/clip/LOD/VFX records do not imply converted runtime behavior.',
                                'Banks are hashed, not decoded as audio. Fonts/raw media are local-only.']}
            write_json(stage / 'catalog.json', catalog)
            boundary()
            emit('validating')
            # Deliberately unchanged: rehash every artifact and recompute every
            # pointer/numeric/texture-owner diagnostic, INCLUDING cached chunks.
            # Release construction indexes before the verifier loads the large
            # catalog again. The independent verifier remains authoritative.
            del index, catalog, objects, seen, ordered
            catalog = verify_catalog(stage)
            _check_origin(catalog, sources, files, sealed_objects(), errors)
            if source_files(root) != sources or verify_source(state_path) != state_identity:
                raise ContentError('Inputs changed during finalization')
            final = {'output': str(output.resolve()), 'manifest': verify_source(stage / 'catalog.json'),
                     'status': catalog['status'], 'counts': catalog['counts']}
            write_json(workspace / 'ready.json', final)
            write_json(checkpoints / 'final.json', final)
            emit('publishing')
            if output.exists() or output.is_symlink():
                raise ContentError('Catalog output appeared during finalization')
            stage.rename(output)
            emit('finished', status=catalog['status'], counts=catalog['counts'])
            return report
        except BudgetPause:
            previous = report['phase']
            emit('paused', interrupted_phase=previous, stop_reason='budget-exhausted')
            return report
        except BaseException as error:
            previous = report['phase']
            # No raw exception text, absolute paths or typetrees in public reports.
            report.update(status='incomplete', exception_type=type(error).__name__)
            emit('interrupted' if isinstance(error, (KeyboardInterrupt, SystemExit)) else 'failed',
                 interrupted_phase=previous)
            raise


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--checkpoints', type=Path, required=True)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--budget', type=float, default=1800)
    parser.add_argument('--progress-report', type=Path)
    args = parser.parse_args(argv)
    try:
        result = finalize_catalog(args.source, args.output, args.checkpoints, args.workspace,
                                  resume=args.resume, budget=args.budget, report_path=args.progress_report)
    except (ContentError, OSError, KeyError, TypeError, ValueError) as error:
        print('CATALOG FINALIZATION FAILED: ' + type(error).__name__, file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0 if result['phase'] == 'finished' and result['status'] == 'indexed' else 2


if __name__ == '__main__':
    raise SystemExit(main())
