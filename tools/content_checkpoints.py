"""Encrypted, portable inventory checkpoints. Never upload the plaintext tree.

The existing RSA-OAEP/AES-GCM chunk transport is reused; this is recovery storage,
not a new cryptographic protocol. An owner-retained private key is required for
restoration. Selecting a trusted producer run remains the caller's responsibility.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import tarfile
import tempfile

if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.content_batches import check_paths, exclusive, read_json, recipe, validate_batch, bundle_key
from tools.content_pipeline import ContentError, safe_child, verify_source
from tools.private_source import seal, unseal

MAX_ARCHIVE = 40 * 1024**3


def _members(root: Path):
    """Allow only recovery state/payloads, not arbitrary neighboring secrets."""
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root).as_posix()
        safe_child(root, relative)  # Reject links even in excluded directories.
        if path.is_dir(): continue
        if not path.is_file(): raise ContentError('Non-regular checkpoint entry')
        parts = PurePosixPath(relative).parts
        if relative == 'state.json' or (parts[0] in ('bundles', 'partials') and len(parts) >= 3
                                      and re.fullmatch('[a-f0-9]{64}', parts[1])):
            if path.name in ('.lock', '.object-journal.lock'): continue
            yield path, relative


def seal_checkpoints(checkpoints: Path, public_pem: bytes, output: Path) -> dict:
    check_paths(checkpoints, output)
    if not checkpoints.is_dir(): raise ContentError('Missing checkpoint directory')
    if output.exists(): raise ContentError('Encrypted checkpoint output already exists')
    with exclusive(checkpoints):
        state = read_json(checkpoints/'state.json')
        if state.get('recipe') != recipe(): raise ContentError('Checkpoint decoder recipe changed')
        by_key = {bundle_key(s): s for s in state['sources'] if s['kind'] == 'bundle'}
        if not set(state['completed']) <= set(by_key): raise ContentError('Unknown sealed bundle')
        for key, receipt in state['completed'].items():
            validate_batch(safe_child(checkpoints, 'bundles/'+key), by_key[key], receipt)
        # A temporary private tar is removed on both success and failure.
        with tempfile.TemporaryDirectory(prefix='checkpoint-seal-', dir=checkpoints.parent) as temp:
            archive = Path(temp)/'checkpoint.tar'
            with tarfile.open(archive, 'w') as tar:
                for path, relative in _members(checkpoints):
                    info = tarfile.TarInfo(relative)
                    info.size = path.stat().st_size; info.mode = 0o600
                    with path.open('rb') as stream: tar.addfile(info, stream)
            identity = verify_source(archive)
            if identity['bytes'] > MAX_ARCHIVE: raise ContentError('Checkpoint archive exceeds storage budget')
            return seal(archive, public_pem, output, expected_sha=identity['sha256'],
                        expected_size=identity['bytes'])


def _extract(archive: Path, output: Path) -> None:
    """Regular files only, create-only paths, bounded expanded size, no tar links."""
    seen = set(); total = 0
    with tarfile.open(archive, 'r:') as tar:
        for item in tar:
            path = safe_child(output, item.name)
            if not item.isfile() or item.name in seen or item.size < 0:
                raise ContentError('Unsupported or duplicate checkpoint archive entry')
            seen.add(item.name); total += item.size
            if total > MAX_ARCHIVE: raise ContentError('Expanded checkpoint exceeds storage budget')
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            stream = tar.extractfile(item)
            if stream is None: raise ContentError('Missing archive payload')
            with path.open('xb') as target:
                shutil.copyfileobj(stream, target)
            path.chmod(0o600)
            if path.stat().st_size != item.size: raise ContentError('Truncated checkpoint entry')
    if not seen: raise ContentError('Empty checkpoint archive')


def restore_checkpoints(encrypted: Path, private_pem: bytes, output: Path,
                        *, expected_sources: list[dict]) -> dict:
    check_paths(encrypted, output)
    if output.exists(): raise ContentError('Restoration never overwrites an existing checkpoint directory')
    manifest = read_json(safe_child(encrypted, 'manifest.json'))
    size = manifest.get('bytes')
    if type(size) is not int or not 1 <= size <= MAX_ARCHIVE:
        raise ContentError('Invalid checkpoint archive size')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='checkpoint-restore-', dir=output.parent) as temp:
        archive = Path(temp)/'checkpoint.tar'
        unseal(encrypted, private_pem, archive, expected_sha=manifest['sha256'], expected_size=size)
        stage = Path(temp)/'checkpoints'; stage.mkdir(mode=0o700)
        _extract(archive, stage)
        # Refuse unexpected members, including private keys and absolute paths.
        allowed = {relative for _, relative in _members(stage)}
        actual = {p.relative_to(stage).as_posix() for p in stage.rglob('*') if p.is_file()}
        if allowed != actual: raise ContentError('Unexpected file in checkpoint archive')
        state = read_json(stage/'state.json')
        if state.get('schema') != 1 or state.get('recipe') != recipe() or state.get('sources') != expected_sources:
            raise ContentError('Restored source/decoder identity does not match this run')
        by_key = {bundle_key(s): s for s in expected_sources if s['kind'] == 'bundle'}
        if not set(state['completed']) <= set(by_key): raise ContentError('Unknown restored bundle')
        for key, receipt in state['completed'].items():
            validate_batch(safe_child(stage, 'bundles/'+key), by_key[key], receipt)
        # Partial journals are verified against the actual reader layout by the
        # next worker, before any object can be reused. No success is inferred.
        if output.exists(): raise ContentError('Checkpoint destination appeared during restoration')
        os.rename(stage, output)
    return {'status': 'restored-not-catalog-verified', 'completed_bundles': len(state['completed']),
            'godot_imported': False, 'original_media_complete': False}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode', choices=['seal', 'restore'])
    p.add_argument('source', type=Path); p.add_argument('key', type=Path); p.add_argument('output', type=Path)
    p.add_argument('--original', type=Path, help='Extracted Data directory required for restoration')
    a = p.parse_args(argv)
    if a.mode == 'restore':
        if a.original is None: p.error('restore requires --original')
        from tools.content_unity import source_files
        report = restore_checkpoints(a.source, a.key.read_bytes(), a.output,
                                     expected_sources=source_files(a.original))
    else:
        report = seal_checkpoints(a.source, a.key.read_bytes(), a.output)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == '__main__': raise SystemExit(main())
