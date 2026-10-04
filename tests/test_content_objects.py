"""Authored reader fixtures; process-death recovery is exercised with a real child."""
from __future__ import annotations
import json
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tests.content_fixture import bundle_fixture
from tools.content_pipeline import ContentError, canonical, object_id
from tools.content_unity import read_bundle, source_files
from tools.content_objects import ObjectJournal
from tools.content_batches import recipe


class ObjectResumeTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name); self.root = self.base/'source'; self.out = self.base/'partial'
        self.loader, self.decoder, self.objects = bundle_fixture(self.root)
        self.source = next(s for s in source_files(self.root) if s['path'] == 'assets.bundle')
        self.pin = recipe()

    def run_bundle(self, **kw):
        return read_bundle(self.root, self.source, self.out, loader=self.loader,
                           mesh_decoder=self.decoder, journal_recipe=self.pin, **kw)

    def interrupted(self, phase='image'):
        def stop(value):
            if value['phase'] == phase: raise KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt): self.run_bundle(progress=stop)

    def test_interrupted_image_resumes_without_redecoding_sealed_objects(self):
        self.interrupted()
        # First three objects are sealed; the texture has orphan raw/tree outputs.
        db = sqlite3.connect(self.out/'object-journal.sqlite3')
        self.assertEqual(db.execute('SELECT count(*) FROM objects').fetchone()[0], 3); db.close()
        with patch.object(self.objects['assets.bundle'][0], 'get_raw_data', side_effect=AssertionError('decoded twice')):
            observed=[]; result=self.run_bundle(progress=observed.append)
        self.assertEqual(len(result['objects']),4)
        self.assertTrue(all(not r['errors'] for r in result['objects']))
        self.assertEqual(sum(v['phase']=='reused' for v in observed),3)
        self.assertEqual(result['objects'][-1]['conversion'],'decoded-image-not-bound')

    def test_resumed_records_and_payloads_equal_uninterrupted(self):
        self.interrupted(); resumed=self.run_bundle()
        other=self.base/'uninterrupted'
        expected=read_bundle(self.root,self.source,other,loader=self.loader,mesh_decoder=self.decoder)
        self.assertEqual(canonical(resumed),canonical(expected))
        for record in resumed['objects']:
            for artifact in record['artifacts']:
                self.assertEqual((self.out/artifact['path']).read_bytes(),(other/artifact['path']).read_bytes())

    def test_completed_retry_performs_no_object_decoding(self):
        self.run_bundle()
        def reject(*_): raise AssertionError('must not decode')
        for o in self.objects['assets.bundle']: o.get_raw_data=reject
        result=self.run_bundle()
        self.assertFalse(any(o['errors'] for o in result['objects']))

    def test_source_changes_reject_before_reuse(self):
        self.interrupted(); (self.root/'assets.bundle').write_bytes(b'different input')
        with self.assertRaises(ContentError): self.run_bundle()

    def test_recipe_changes_reject_reuse(self):
        self.interrupted(); self.pin={'changed':True}
        with self.assertRaisesRegex(ContentError,'decoder changed'): self.run_bundle()

    def test_reader_layout_changes_reject_reuse(self):
        self.interrupted(); self.objects['assets.bundle'].pop()
        with self.assertRaisesRegex(ContentError,'layout'): self.run_bundle()

    def test_corrupt_payload_rejects_reuse(self):
        self.interrupted(); (self.out / ('objects/'+object_id('assets.bundle/CAB-assets',7)+'.bin')).write_bytes(b'broken')
        with self.assertRaises(ContentError): self.run_bundle()

    def test_corrupt_record_rejects_reuse(self):
        self.interrupted()
        with closing(sqlite3.connect(self.out/'object-journal.sqlite3')) as db, db:
            db.execute("UPDATE objects SET record='{}' WHERE ordinal=0")
        with self.assertRaisesRegex(ContentError,'hash'): self.run_bundle()

    def test_gap_rejects_reuse(self):
        self.interrupted()
        with closing(sqlite3.connect(self.out/'object-journal.sqlite3')) as db, db: db.execute('DELETE FROM objects WHERE ordinal=0')
        with self.assertRaisesRegex(ContentError,'gap'): self.run_bundle()

    def test_symlink_is_never_cleaned_or_followed(self):
        self.interrupted()
        identity=object_id('assets.bundle/CAB-assets',10)
        path=self.out/f'objects/{identity}.bin'; path.unlink()
        outside=self.base/'keep';outside.write_bytes(b'keep')
        path.symlink_to(outside)
        with self.assertRaises(ContentError): self.run_bundle()
        self.assertEqual(outside.read_bytes(),b'keep')

    def test_corrupt_database_fails_instead_of_resetting(self):
        self.interrupted(); path=self.out/'object-journal.sqlite3';path.write_bytes(b'corrupt')
        with self.assertRaises(sqlite3.DatabaseError):self.run_bundle()
        self.assertEqual(path.read_bytes(),b'corrupt')

    def test_concurrent_worker_is_rejected(self):
        self.interrupted()
        with closing(sqlite3.connect(self.out/'object-journal.sqlite3')) as db, db:
            meta=json.loads(db.execute('SELECT value FROM metadata').fetchone()[0])
        with ObjectJournal(self.out,self.source,meta['files'],meta['layout'],self.pin):
            with self.assertRaisesRegex(ContentError,'Another worker'): self.run_bundle()

    def test_decode_error_is_retained_not_promoted(self):
        self.objects['assets.bundle'][0].get_raw_data=lambda: (_ for _ in ()).throw(ValueError('bad raw'))
        result=self.run_bundle(); self.assertTrue(result['objects'][0]['errors'])
        again=self.run_bundle();self.assertEqual(result,again)

    def test_real_child_exit_leaves_prior_objects_reusable(self):
        code='''
import os, sys
from pathlib import Path
from tests.content_fixture import bundle_fixture
from tools.content_unity import read_bundle, source_files
from tools.content_batches import recipe
root=Path(sys.argv[1]);out=Path(sys.argv[2])
loader,decoder,_=bundle_fixture(root)
source=next(s for s in source_files(root) if s['path']=='assets.bundle')
def progress(value):
    if value['phase']=='image':os._exit(37)
read_bundle(root,source,out,loader=loader,mesh_decoder=decoder,progress=progress,journal_recipe=recipe())
'''
        process=subprocess.run([sys.executable,'-c',code,str(self.root),str(self.out)],timeout=15)
        self.assertEqual(process.returncode,37)
        seen=[];result=self.run_bundle(progress=seen.append)
        self.assertEqual(sum(v['phase']=='reused' for v in seen),3)
        self.assertEqual(len(result['objects']),4)

    def test_new_decoder_file_is_in_recipe(self):
        self.assertIn('content_objects.py',recipe()['code'])
