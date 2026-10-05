"""Bounded same-job inventory retries; existing decoders remain authoritative.

This module schedules the existing checkpoint validator/decoder. Cursor counts
are liveness hints only: they never authorize reuse or promote an incomplete
catalog. Each resumed pass still validates source, recipe, receipts and payloads.
"""
from __future__ import annotations

from contextlib import contextmanager
import os
import stat
from pathlib import Path
import time

from .content_batches import (check_paths, inventory_batched, safe_cursor,
                              write_json, _positive)
from .content_pipeline import ContentError, safe_child


@contextmanager
def controller_lock(checkpoints: Path):
    """Serialize controllers across the gaps between per-pass decoder locks."""
    if os.name != 'posix':
        raise ContentError('Continuation requires POSIX file locks')
    import fcntl
    path = checkpoints.with_name(checkpoints.name + '.continuation.lock')
    check_paths(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ContentError('Continuation lock must be a regular file')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ContentError('Another continuation controller owns these checkpoints') from exc
        yield
    finally:
        os.close(fd)


def validate_policy(*, budget: float, pass_budget: float, bundle_timeout: float,
                    max_bundle_timeout: float, max_passes: int, stall_limit: int) -> None:
    for name, value in [('budget', budget), ('pass_budget', pass_budget),
                        ('bundle_timeout', bundle_timeout), ('max_bundle_timeout', max_bundle_timeout)]:
        _positive(value, name)
    if max_bundle_timeout < bundle_timeout:
        raise ContentError('max_bundle_timeout must be at least bundle_timeout')
    for name, value in [('max_passes', max_passes), ('stall_limit', stall_limit)]:
        if type(value) is not int or not 1 <= value <= 100:
            raise ContentError(name + ' must be an integer between 1 and 100')


def _pass_report(value: dict) -> dict:
    """Only metrics/identities from the worker report may enter shared history."""
    if not isinstance(value, dict) or value.get('schema') != 1:
        raise ContentError('Invalid inventory pass report')
    result = {'schema': 1, 'original_media_complete': False, 'godot_imported': False}
    for field in ('total_bundles', 'completed_bundles', 'reused_bundles', 'attempted_bundles'):
        number = value.get(field)
        if type(number) is not int or number < 0:
            raise ContentError('Invalid inventory pass counter: ' + field)
        result[field] = number
    if not 0 <= result['completed_bundles'] <= result['total_bundles'] or result['total_bundles'] < 1:
        raise ContentError('Inventory pass counts disagree')
    if value.get('phase') not in ('paused', 'awaiting-finalization', 'finished'):
        raise ContentError('Inventory pass did not reach a recognized boundary')
    if value.get('status') not in ('indexed', 'incomplete'):
        raise ContentError('Unknown inventory pass status')
    if value['status'] == 'indexed' and (value['phase'] != 'finished' or
                                        result['completed_bundles'] != result['total_bundles']):
        raise ContentError('Incomplete pass cannot claim an indexed catalog')
    result.update(phase=value['phase'], status=value['status'])
    result['source_status'] = value.get('source_status', 'unknown')
    if result['source_status'] not in ('synthetic-adapter-fixture', 'hash-verified-inputs-not-complete-media'):
        raise ContentError('Unknown inventory source status')
    failures = value.get('failures')
    if not isinstance(failures, list):
        raise ContentError('Inventory pass has no failure list')
    result['failures'] = []
    for failure in failures:
        if not isinstance(failure, dict):
            raise ContentError('Invalid worker failure report')
        reason = failure.get('reason')
        if reason not in ('timeout', 'TimeoutExpired', 'CalledProcessError', 'ContentError',
                           'OSError', 'ValueError', 'RuntimeError', 'FileNotFoundError', 'PermissionError'):
            reason = 'non-retryable-worker-error'
        item = {'reason': reason}
        if 'bundle' in failure:
            # Relative source identity, never a private worker log or payload.
            safe_child(Path('/continuation-source-identities'), failure['bundle'])
            item['bundle'] = failure['bundle']
        if failure.get('phase') == 'finalizing': item['phase'] = 'finalizing'
        if type(failure.get('exit_code')) is int: item['exit_code'] = failure['exit_code']
        if isinstance(failure.get('cursor'), dict): item['cursor'] = safe_cursor(failure['cursor'])
        result['failures'].append(item)
    if 'counts' in value:
        counts = value['counts']
        if not isinstance(counts, dict): raise ContentError('Invalid final catalog counters')
        result['counts'] = {key: counts[key] for key in ('bundles', 'objects', 'errors') if key in counts}
        if any(type(n) is not int or n < 0 for n in result['counts'].values()):
            raise ContentError('Invalid final catalog counters')
    if value['phase'] == 'finished':
        counts = result.get('counts', {})
        if result['completed_bundles'] != result['total_bundles'] or counts.get('bundles') != result['total_bundles']:
            raise ContentError('Finished catalog has unprocessed bundles')
        if value['status'] == 'indexed' and (counts.get('errors') != 0 or not counts.get('objects')):
            raise ContentError('Indexed catalog counters contradict its success')
    return result


def inventory_continued(root: Path, output: Path, checkpoints: Path, *,
                        resume: bool = False, bundle_timeout: float = 120,
                        budget: float = 3600, pass_budget: float = 900,
                        max_bundle_timeout: float = 480, max_passes: int = 8,
                        stall_limit: int = 2, report_path: Path | None = None,
                        continuation_report: Path | None = None) -> dict:
    """Continue budget/timeout pauses while retaining strict existing validators.

    `budget` is shared across passes, including time between passes. The existing
    decoder's source/checkpoint integrity I/O is not preemptible by this scheduler:
    the source runner retains a separate outer process timeout for that case.
    All sealed bundles grant the finalizer the remaining budget, not a small
    per-pass slice. No external key is needed to resume in the same workspace.
    """
    validate_policy(budget=budget, pass_budget=pass_budget, bundle_timeout=bundle_timeout,
                    max_bundle_timeout=max_bundle_timeout, max_passes=max_passes, stall_limit=stall_limit)
    lock_path = checkpoints.with_name(checkpoints.name + '.continuation.lock')
    paths = [root, output, checkpoints, lock_path]
    if report_path is not None: paths.append(report_path)
    if continuation_report is not None: paths.append(continuation_report)
    check_paths(*paths)
    if continuation_report is not None and continuation_report.exists():
        raise ContentError('Use a new continuation report; existing evidence is not overwritten')
    history = {'schema': 1, 'scope': 'same-job inventory scheduling, not content conversion',
               'status': 'incomplete', 'stop_reason': None, 'passes': [],
               'original_media_complete': False, 'godot_imported': False,
               'policy': {'budget': budget, 'pass_budget': pass_budget, 'bundle_timeout': bundle_timeout,
                          'max_bundle_timeout': max_bundle_timeout, 'max_passes': max_passes,
                          'stall_limit': stall_limit}}
    last = {'schema': 1, 'status': 'incomplete', 'phase': 'not-started',
            'original_media_complete': False, 'godot_imported': False}
    started = time.monotonic()
    deadline = started + budget
    previous_completed = 0
    object_highwater: dict[str, int] = {}
    stagnant = 0
    finalizing = False

    def emit(reason=None):
        history['status'] = last['status']
        history['stop_reason'] = reason
        history['elapsed_seconds'] = max(0.0, time.monotonic() - started)
        if continuation_report is not None: write_json(continuation_report, history)

    # This lock is distinct from the existing per-pass lock. Legacy callers still
    # have their own exclusion and must not share a controller's active workspace.
    with controller_lock(checkpoints):
        emit()
        try:
            for ordinal in range(max_passes):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    emit('budget-exhausted'); break
                allocation = remaining if finalizing else min(pass_budget, remaining)
                timeout = min(max_bundle_timeout, bundle_timeout * (2 ** ordinal), allocation)
                history['active_pass'] = {'number': ordinal + 1, 'budget': allocation,
                                          'bundle_timeout': timeout, 'resume': resume or ordinal > 0}
                emit()
                raw = inventory_batched(root, output, checkpoints, resume=resume or ordinal > 0,
                                        bundle_timeout=timeout, budget=allocation, report_path=report_path)
                last = _pass_report(raw)
                if ordinal and last['total_bundles'] != history['passes'][0]['total_bundles']:
                    raise ContentError('Source bundle count changed between passes')
                history['passes'].append({**history.pop('active_pass'), **last})
                emit()
                if last['phase'] == 'finished':
                    emit('catalog-finished'); break
                if any(f['reason'] not in ('timeout', 'TimeoutExpired') for f in last['failures']):
                    emit('non-retryable-failure'); break
                if last['completed_bundles'] < previous_completed:
                    raise ContentError('Completed bundle count regressed between passes')
                advanced = last['completed_bundles'] > previous_completed
                previous_completed = last['completed_bundles']
                for failure in last['failures']:
                    name = failure.get('bundle')
                    done = failure.get('cursor', {}).get('objects_done', 0)
                    if name and done > object_highwater.get(name, 0):
                        object_highwater[name] = done
                        advanced = True
                # The first pass establishes the observed prefix, not a stall.
                stagnant = 0 if advanced or ordinal == 0 else stagnant + 1
                finalizing = last['completed_bundles'] == last['total_bundles']
                # A timed-out finalizer gets the remaining shared budget once.
                # It does not pretend to make object progress during graph checks.
                if not finalizing and stagnant >= stall_limit:
                    emit('no-progress'); break
            else:
                emit('pass-limit')
        except BaseException as error:
            last = {**last, 'status': 'incomplete'}
            history['exception_type'] = type(error).__name__
            emit('interrupted' if isinstance(error, (KeyboardInterrupt, SystemExit)) else 'exception')
            raise
    return {**last, 'continuation': history}
