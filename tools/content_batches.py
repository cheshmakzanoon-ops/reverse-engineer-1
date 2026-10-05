"""Checkpointed Unity inventory. Media stays private; progress is metadata only.

Bundle workers and final assembly run in separate bounded processes. A timeout
retains completed, hash-checked batches; it never publishes a partial catalog.
This is Linux/POSIX tooling, not a sandbox for running an untrusted executable.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import signal
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Callable

# Standalone child invocation works even when the caller is outside the repo.
if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.content_pipeline import (AssetIndex, ContentError, UNITYPY_VERSION,
    UNITY_VERSION, canonical, object_id, safe_child, sha256_file, verify_catalog, verify_source)
from tools.content_unity import finish_catalog, read_bundle, source_files

SCHEMA = 1
CODE = Path(__file__).resolve().parent


def recipe() -> dict:
    """A changed decoder or format cannot reuse old work under the same pin."""
    return {'schema': SCHEMA, 'unity': UNITY_VERSION, 'unitypy': UNITYPY_VERSION,
            'python': platform.python_version(), 'code': {
                name: sha256_file(CODE / name) for name in
                ('content_batches.py', 'content_unity.py', 'content_pipeline.py', 'content_objects.py', 'content_textures.py')}}


def write_json(path: Path, value: dict, *, durable: bool = True) -> None:
    """Atomic diagnostic/checkpoint update; immutable final catalogs use rename."""
    safe_child(path.parent, path.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.'+path.name+'-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as output:
            output.write(canonical(value))
            output.flush()
            if durable: os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def read_json(path: Path) -> dict:
    safe_child(path.parent, path.name)
    value = json.loads(path.read_text())
    if not isinstance(value, dict): raise ContentError('Expected a checkpoint object')
    return value


def check_paths(*paths: Path) -> None:
    for path in paths:
        for part in (path, *path.parents):
            if part.is_symlink(): raise ContentError('Checkpoint paths cannot traverse symlinks')
    absolute = [p.resolve() for p in paths]
    for i, a in enumerate(absolute):
        for b in absolute[i+1:]:
            if a.is_relative_to(b) or b.is_relative_to(a):
                raise ContentError('Source, catalog and checkpoint directories must be separate')


@contextmanager
def exclusive(checkpoints: Path):
    if os.name != 'posix': raise ContentError('Checkpoint workers require POSIX process/lock support')
    import fcntl
    fd = os.open(checkpoints / '.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error: raise ContentError('Another inventory owns these checkpoints') from error
        yield
    finally:
        os.close(fd)


def run_bounded(command: list[str], log: Path, timeout: float) -> None:
    """Terminate the worker process group on timeout/cancellation, retaining logs."""
    if os.name != 'posix': raise ContentError('Bounded workers require POSIX process groups')
    if not math.isfinite(timeout) or timeout <= 0: raise ContentError('Worker timeout must be positive')
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open('xb') as output:
        process = subprocess.Popen(command, stdout=output, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        try:
            code = process.wait(timeout=timeout)
            if code: raise subprocess.CalledProcessError(code, command)
        except BaseException:
            # Even an exited group leader may have a child still running.
            try: os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError: pass
            try: process.wait(timeout=1)
            except subprocess.TimeoutExpired: pass
            try: os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError: pass
            process.wait()
            raise


def safe_cursor(value: dict) -> dict:
    """Only fixed diagnostic fields may cross from a private worker to reports."""
    result = {}
    if value.get('phase') in ('load', 'raw-object', 'typetree', 'mesh', 'image', 'decoded', 'reused'):
        result['phase'] = value['phase']
    if type(value.get('objects_done')) is int and value['objects_done'] >= 0:
        result['objects_done'] = value['objects_done']
    for field, pattern in [('object_id', r'[a-f0-9]{64}'), ('object_type', r'[A-Za-z][A-Za-z0-9_]{0,79}')]:
        if isinstance(value.get(field), str) and re.fullmatch(pattern, value[field]):
            result[field] = value[field]
    return result


def bundle_key(source: dict) -> str:
    return hashlib.sha256(source['path'].encode('utf-8')).hexdigest()


def validate_batch(folder: Path, source: dict, expected: dict | None = None) -> dict:
    receipt = safe_child(folder, 'batch.json')
    if expected is not None:
        verify_source(receipt, expected_sha256=expected['sha256'], expected_size=expected['bytes'])
    result = read_json(receipt)
    if result.get('schema') != SCHEMA or result.get('source') != source:
        raise ContentError('Batch source identity does not match the checkpoint plan')
    files, objects = result['files'], result['objects']
    idx = AssetIndex(files, objects)
    for file in files:
        if file['bundle'] != source['path'] or file['key'] != source['path']+'/'+file['name']:
            raise ContentError('Batch contains another bundle\'s serialized file')
    for record in objects:
        if record.get('id') != object_id(record['file'], record['path_id']):
            raise ContentError('Invalid checkpoint object identity')
        if record.get('references') != []:
            raise ContentError('Intermediate batch must not contain resolved global edges')
        roles = [a['role'] for a in record['artifacts']]
        if len(roles) != len(set(roles)):
            raise ContentError('Duplicate checkpoint artifact role')
        if not record['errors'] and not {'raw-object', 'typetree'} <= set(roles):
            raise ContentError('Checkpoint hides a missing raw record or typetree')
        for artifact in record['artifacts']:
            relative = artifact['path']
            # A worker may only publish artifacts belonging to this exact object.
            if relative not in {f'objects/{record["id"]}.bin', f'objects/{record["id"]}.json',
                                f'meshes/{record["id"]}.json', f'images/{record["id"]}.png'}:
                raise ContentError('Unexpected checkpoint artifact path')
            verify_source(safe_child(folder, relative), expected_sha256=artifact['sha256'],
                          expected_size=artifact['bytes'])
    if not idx.objects and not result['errors']:
        raise ContentError('Empty batch cannot report successful decoding')
    return result


def _positive(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ContentError(name+' must be finite and positive')


def _verify_inputs(root: Path, sources: list[dict]):
    if source_files(root) != sources:
        raise ContentError('Source files changed; do not resume a different dataset')


def _command(*args: str) -> list[str]:
    return [sys.executable, str(Path(__file__).resolve()), *args]


def finalize(root: Path, output: Path, checkpoints: Path) -> None:
    """Child process: merge sealed batches and apply the existing catalog gates."""
    state = read_json(checkpoints / 'state.json')
    if state['recipe'] != recipe(): raise ContentError('Decoder recipe changed before finalization')
    _verify_inputs(root, state['sources'])
    if output.exists() or output.is_symlink(): raise ContentError('Final catalog already exists')
    stage = Path(tempfile.mkdtemp(prefix='assemble-', dir=checkpoints))
    try:
        files, objects, errors = [], [], []
        for source in state['sources']:
            if source['kind'] != 'bundle': continue
            key = bundle_key(source)
            expected = state['completed'].get(key)
            if expected is None: raise ContentError('Finalization is missing a sealed bundle')
            folder = safe_child(checkpoints, 'bundles/'+key)
            value = validate_batch(folder, source, expected)
            files.extend(value['files']); objects.extend(value['objects']); errors.extend(value['errors'])
            for record in value['objects']:
                for artifact in record['artifacts']:
                    destination = safe_child(stage, artifact['path'])
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with destination.open('xb') as target, safe_child(folder, artifact['path']).open('rb') as source_file:
                        shutil.copyfileobj(source_file, target)
        catalog = finish_catalog(stage, state['sources'], files, objects, errors, synthetic=state['synthetic'])
        _verify_inputs(root, state['sources'])
        # Persist the hash before rename: a parent interrupted immediately after
        # publication can verify the completed catalog rather than re-decoding.
        final = {'output': str(output.resolve()), 'manifest': verify_source(stage / 'catalog.json'),
                 'status': catalog['status'], 'counts': catalog['counts']}
        write_json(checkpoints / 'final.json', final)
        output.parent.mkdir(parents=True, exist_ok=True)
        if output.exists(): raise ContentError('Final output appeared during assembly')
        stage.rename(output)
    finally:
        if stage.exists(): shutil.rmtree(stage)


def inventory_batched(root: Path, output: Path, checkpoints: Path, *, resume: bool = False,
                      bundle_timeout: float = 120, budget: float = 1800,
                      max_bundles: int | None = None, report_path: Path | None = None,
                      _processor: Callable | None = None) -> dict:
    """Resume only source/code/hash-compatible receipts; fixtures label themselves.

    Budget bounds decoder/finalizer execution after source/checkpoint integrity
    checks. It is not a disk-I/O wall-clock guarantee or production benchmark.
    """
    _positive(bundle_timeout, 'bundle_timeout'); _positive(budget, 'budget')
    if max_bundles is not None and (type(max_bundles) is not int or max_bundles <= 0):
        raise ContentError('max_bundles must be a positive integer')
    check_paths(root, output, checkpoints)
    if report_path is not None:
        check_paths(root, output, checkpoints, report_path)
    sources = source_files(root)
    plan = {'schema': SCHEMA, 'recipe': recipe(), 'sources': sources, 'synthetic': _processor is not None}
    existed = checkpoints.exists()
    if existed and not resume: raise ContentError('Checkpoints already exist; explicitly request --resume')
    if not existed and resume: raise ContentError('Cannot resume missing checkpoints')
    checkpoints.mkdir(parents=True, exist_ok=True, mode=0o700)
    with exclusive(checkpoints):
        state_path = checkpoints / 'state.json'
        if existed:
            state = read_json(state_path)
            if any(state.get(k) != v for k, v in plan.items()):
                raise ContentError('Source or decoder recipe changed; use fresh checkpoints')
        else:
            if output.exists(): raise ContentError('Use a new catalog output')
            state = {**plan, 'completed': {}}
            write_json(state_path, state)
        bundles = [s for s in sources if s['kind'] == 'bundle']
        known = {bundle_key(s) for s in bundles}
        if not set(state['completed']) <= known: raise ContentError('Unknown bundle in checkpoint ledger')
        report = {'schema': SCHEMA, 'status': 'incomplete', 'total_bundles': len(bundles),
                  'completed_bundles': 0, 'reused_bundles': 0, 'attempted_bundles': 0,
                  'source_status': 'synthetic-adapter-fixture' if _processor else 'hash-verified-inputs-not-complete-media',
                  'failures': [], 'original_media_complete': False, 'godot_imported': False}
        def emit(phase, **fields):
            report.update(phase=phase, **fields)
            report['completed_bundles'] = len(state['completed'])
            write_json(checkpoints / 'progress.json', report)
            if report_path: write_json(report_path, report)
        # Recover the rename-before-ledger-update crash window, but validate even
        # recorded receipts and every payload before allowing any cache reuse.
        for source in bundles:
            key = bundle_key(source); folder = safe_child(checkpoints, 'bundles/'+key)
            if key in state['completed'] or folder.exists():
                validate_batch(folder, source, state['completed'].get(key))
                state['completed'][key] = verify_source(folder / 'batch.json')
                report['reused_bundles'] += 1
        write_json(state_path, state)
        def completed_output(*, recheck_payloads=True):
            final = read_json(checkpoints / 'final.json')
            if final['output'] != str(output.resolve()): raise ContentError('Final output path does not match its receipt')
            verify_source(safe_child(output, 'catalog.json'), expected_sha256=final['manifest']['sha256'],
                          expected_size=final['manifest']['bytes'])
            cat = verify_catalog(output) if recheck_payloads else read_json(output / 'catalog.json')
            if cat['sources'] != sources or cat['status'] != final['status'] or cat['counts'] != final['counts']:
                raise ContentError('Final catalog does not match checkpoints')
            if len(state['completed']) != len(bundles): raise ContentError('Final catalog has unprocessed bundles')
            emit('finished', status=cat['status'], counts=cat['counts'])
            return report
        if output.exists():
            if not resume or not (checkpoints / 'final.json').exists():
                raise ContentError('Existing catalog is not owned by this completed checkpoint job')
            return completed_output()
        emit('ready')
        deadline = time.monotonic()+budget
        for source in bundles:
            key = bundle_key(source)
            if key in state['completed']: continue
            if time.monotonic() >= deadline or (max_bundles is not None and report['attempted_bundles'] >= max_bundles): break
            if _processor is not None:
                attempt = Path(tempfile.mkdtemp(prefix='attempt-', dir=checkpoints))
            else:
                # Stable private directory survives a killed worker. Its object
                # journal binds source, reader layout and exact decoder recipe.
                attempt = safe_child(checkpoints, 'partials/'+key)
                attempt.mkdir(parents=True, exist_ok=True)
            write_json(attempt / 'source.json', source)
            report['attempted_bundles'] += 1
            emit('decoding', active_bundle=source['path'])
            try:
                if _processor is not None:
                    result = _processor(root, source, attempt)
                    write_json(attempt / 'batch.json', {'schema': SCHEMA, 'source': source, **result})
                elif not (attempt / 'batch.json').exists():
                    run_bounded(_command('worker', str(root.resolve()), str(attempt.resolve())),
                                attempt / ('worker-'+str(time.time_ns())+'.log'), min(bundle_timeout, max(0.001, deadline-time.monotonic())))
                validate_batch(attempt, source)
                folder = safe_child(checkpoints, 'bundles/'+key)
                folder.parent.mkdir(parents=True, exist_ok=True)
                if folder.exists(): raise ContentError('Checkpoint bundle already exists')
                attempt.rename(folder)
                state['completed'][key] = verify_source(folder / 'batch.json')
                write_json(state_path, state)
            except (ContentError, OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                diagnostic = {'bundle': source['path'], 'reason': 'timeout' if isinstance(error, subprocess.TimeoutExpired) else type(error).__name__}
                if isinstance(error, subprocess.CalledProcessError):
                    diagnostic['exit_code'] = error.returncode
                cursor = attempt / 'cursor.json'
                if cursor.is_file() and not cursor.is_symlink():
                    value = read_json(cursor)
                    # Safe fixed field allowlist. The private worker log and raw
                    # attempt are intentionally never in the public report.
                    diagnostic['cursor'] = safe_cursor(value)
                report['failures'].append(diagnostic)
            emit('checkpointed')
        report.pop('active_bundle', None)
        _verify_inputs(root, sources)
        if len(state['completed']) != len(bundles):
            emit('paused', pending_bundles=[s['path'] for s in bundles if bundle_key(s) not in state['completed']])
            return report
        if time.monotonic() >= deadline:
            emit('awaiting-finalization')
            return report
        emit('finalizing')
        attempt = Path(tempfile.mkdtemp(prefix='finalize-', dir=checkpoints))
        try:
            run_bounded(_command('finalize', str(root.resolve()), str(output.resolve()), str(checkpoints.resolve())),
                        attempt / 'worker.log', max(0.001, deadline-time.monotonic()))
        except subprocess.SubprocessError as error:
            report['failures'].append({'phase': 'finalizing', 'reason': type(error).__name__})
            emit('awaiting-finalization')
            return report
        return completed_output(recheck_payloads=False)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    worker = commands.add_parser('worker'); worker.add_argument('root', type=Path); worker.add_argument('attempt', type=Path)
    finish = commands.add_parser('finalize'); finish.add_argument('root', type=Path); finish.add_argument('output', type=Path); finish.add_argument('checkpoints', type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == 'worker':
            source = read_json(args.attempt / 'source.json')
            result = read_bundle(args.root, source, args.attempt,
                                 progress=lambda value: write_json(args.attempt / 'cursor.json', value, durable=False),
                                 journal_recipe=recipe())
            write_json(args.attempt / 'batch.json', {'schema': SCHEMA, 'source': source, **result})
        else:
            finalize(args.root, args.output, args.checkpoints)
        return 0
    except (ContentError, ValueError, KeyError, OSError) as error:
        print('BATCH WORKER FAILED: '+type(error).__name__+': '+str(error), file=sys.stderr)
        return 1


if __name__ == '__main__': raise SystemExit(main())
