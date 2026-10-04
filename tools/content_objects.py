"""Transactional object checkpoints inside a bounded bundle worker.

Only complete objects are reusable. SQLite commits the record after its artifacts
are flushed. A killed decode retries that object, never all preceding objects.
This journal is private recovery data, not a renderability or completeness claim.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3

from .content_pipeline import ContentError, canonical, object_id, safe_child, verify_source


class ObjectJournal:
    def __init__(self, output: Path, source: dict, files: list[dict], layout: list[dict], recipe: dict):
        self.output = output
        self.layout = layout
        self.records: list[dict] = []
        self.db = None
        self.lock = None
        output.mkdir(parents=True, exist_ok=True)
        try:
            import fcntl
            lock_path = safe_child(output, '.object-journal.lock')
            self.lock = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            try:
                fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ContentError('Another worker owns this object journal') from exc
            path = safe_child(output, 'object-journal.sqlite3')
            expected = canonical({'schema': 1, 'source': source, 'files': files,
                                  'layout': layout, 'recipe': recipe}).decode()
            existed = path.exists()
            self.db = sqlite3.connect(path, timeout=0)
            self.db.execute('PRAGMA trusted_schema=OFF')
            self.db.execute('PRAGMA journal_mode=DELETE')
            self.db.execute('PRAGMA synchronous=FULL')
            if not existed:
                with self.db:
                    self.db.execute('CREATE TABLE metadata (value TEXT NOT NULL)')
                    self.db.execute('CREATE TABLE objects (ordinal INTEGER PRIMARY KEY, '
                                    'identity TEXT NOT NULL UNIQUE, record TEXT NOT NULL, digest TEXT NOT NULL)')
                    self.db.execute('INSERT INTO metadata VALUES (?)', (expected,))
            # Refuse triggers/views or a substituted schema before using the DB.
            schema = self.db.execute("SELECT type,name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
            if sorted(schema) != [('table', 'metadata'), ('table', 'objects')]:
                raise ContentError('Unexpected object journal schema')
            if self.db.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
                raise ContentError('Corrupt object journal')
            if self.db.execute('SELECT value FROM metadata').fetchall() != [(expected,)]:
                raise ContentError('Object journal source, layout or decoder changed')
            for ordinal, identity, text, digest in self.db.execute('SELECT * FROM objects ORDER BY ordinal'):
                if ordinal != len(self.records) or ordinal >= len(layout):
                    raise ContentError('Object journal has a gap or extra object')
                if hashlib.sha256(text.encode()).hexdigest() != digest:
                    raise ContentError('Object journal record hash mismatch')
                record = json.loads(text)
                if record.get('id') != identity:
                    raise ContentError('Object journal identity mismatch')
                self.validate(record, ordinal)
                self.records.append(record)
        except BaseException:
            self.close()
            raise

    @staticmethod
    def paths(identity: str) -> set[str]:
        return {f'objects/{identity}.bin', f'objects/{identity}.json',
                f'meshes/{identity}.json', f'images/{identity}.png'}

    def validate(self, record: dict, ordinal: int) -> None:
        expected = self.layout[ordinal]
        if any(record.get(key) != value for key, value in expected.items()):
            raise ContentError('Object journal record does not match source layout')
        if record['id'] != object_id(record['file'], record['path_id']) or record.get('references') != []:
            raise ContentError('Object journal contains invalid identity or resolved global edges')
        artifacts = record.get('artifacts')
        if not isinstance(artifacts, list) or not isinstance(record.get('errors'), list):
            raise ContentError('Invalid object checkpoint record')
        roles = {'raw-object': f'objects/{record["id"]}.bin',
                 'typetree': f'objects/{record["id"]}.json',
                 'decoded-mesh': f'meshes/{record["id"]}.json',
                 'decoded-image': f'images/{record["id"]}.png'}
        seen = set()
        for artifact in artifacts:
            role = artifact['role']
            if role in seen or roles.get(role) != artifact['path']:
                raise ContentError('Invalid object checkpoint artifact role/path')
            seen.add(role)
            verify_source(safe_child(self.output, artifact['path']),
                          expected_size=artifact['bytes'], expected_sha256=artifact['sha256'])
        if not record['errors'] and not {'raw-object', 'typetree'} <= seen:
            raise ContentError('Object checkpoint hides missing required artifacts')

    def prepare(self, ordinal: int) -> None:
        """Remove only orphan outputs of this uncommitted object after a crash."""
        if ordinal != len(self.records) or ordinal >= len(self.layout):
            raise ContentError('Objects must be checkpointed in source order')
        identity = self.layout[ordinal]['id']
        for relative in sorted(self.paths(identity)):
            path = safe_child(self.output, relative)
            if path.exists():
                if not path.is_file():
                    raise ContentError('Unexpected directory at object artifact path')
                path.unlink()

    def commit(self, record: dict) -> None:
        ordinal = len(self.records)
        self.validate(record, ordinal)
        for artifact in record['artifacts']:
            with safe_child(self.output, artifact['path']).open('rb') as stream:
                os.fsync(stream.fileno())
        text = canonical(record).decode()
        with self.db:
            self.db.execute('INSERT INTO objects VALUES (?,?,?,?)',
                            (ordinal, record['id'], text, hashlib.sha256(text.encode()).hexdigest()))
        self.records.append(record)

    def close(self) -> None:
        if self.db is not None:
            self.db.close()
            self.db = None
        if self.lock is not None:
            os.close(self.lock)
            self.lock = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
