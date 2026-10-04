"""Encrypted checkpoint round trips use tiny authored data, never original media."""
import io
import json
from pathlib import Path
import shutil
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from tests.content_fixture import bundle_fixture
from tools import content_batches as batches
from tools.content_unity import read_bundle, source_files
from tools.content_pipeline import ContentError, canonical, verify_catalog
from tools.content_checkpoints import seal_checkpoints, restore_checkpoints, _extract


class CheckpointArchiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        cls.private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        cls.public = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)

    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.base=Path(t.name)
        self.root=self.base/'input';self.out=self.base/'out';self.cp=self.base/'cp';self.enc=self.base/'encrypted'
        self.loader,self.decoder,self.objects=bundle_fixture(self.root)
        self.calls=[]
        def process(root,source,folder):
            self.calls.append(source['path'])
            return read_bundle(root,source,folder,loader=self.loader,mesh_decoder=self.decoder)
        self.process=process
        batches.inventory_batched(self.root,self.out,self.cp,max_bundles=1,_processor=process)

    def test_encrypted_roundtrip_on_a_new_host_resumes_completed_bundle(self):
        seal_checkpoints(self.cp,self.public,self.enc)
        shutil.rmtree(self.cp)
        restored=self.base/'new-host-cp'
        report=restore_checkpoints(self.enc,self.private,restored,expected_sources=source_files(self.root))
        self.assertEqual(report['completed_bundles'],1)
        result=batches.inventory_batched(self.root,self.out,restored,resume=True,_processor=self.process)
        self.assertEqual(result['status'],'indexed')
        self.assertEqual(self.calls,['assets.bundle','scene.bundle'])
        self.assertEqual(verify_catalog(self.out)['counts']['objects'],11)

    def test_uploaded_envelope_contains_no_plaintext_record_or_private_key(self):
        seal_checkpoints(self.cp,self.public,self.enc)
        for path in self.enc.rglob('*'):
            if path.is_file():
                content=path.read_bytes()
                for marker in (b'FixtureRoot',b'm_Vertices',b'PRIVATE KEY',b'assets.bundle'):
                    self.assertNotIn(marker,content)

    def test_ciphertext_corruption_never_publishes_partial_restore(self):
        seal_checkpoints(self.cp,self.public,self.enc)
        cipher=next(self.enc.glob('part-*/data.enc'));value=bytearray(cipher.read_bytes());value[-1]^=1;cipher.write_bytes(value)
        out=self.base/'restore'
        with self.assertRaises(Exception):restore_checkpoints(self.enc,self.private,out,expected_sources=source_files(self.root))
        self.assertFalse(out.exists())

    def test_wrong_recipient_is_rejected(self):
        seal_checkpoints(self.cp,self.public,self.enc)
        wrong=rsa.generate_private_key(public_exponent=65537,key_size=3072).private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption())
        out=self.base/'restore'
        with self.assertRaises(ValueError):restore_checkpoints(self.enc,wrong,out,expected_sources=source_files(self.root))
        self.assertFalse(out.exists())

    def test_source_or_decoder_change_rejects_archive(self):
        seal_checkpoints(self.cp,self.public,self.enc)
        with self.assertRaises(ContentError):restore_checkpoints(self.enc,self.private,self.base/'bad1',expected_sources=[])
        with patch('tools.content_checkpoints.recipe',return_value={'changed':True}),self.assertRaises(ContentError):
            restore_checkpoints(self.enc,self.private,self.base/'bad2',expected_sources=source_files(self.root))

    def test_existing_destination_is_preserved(self):
        seal_checkpoints(self.cp,self.public,self.enc)
        with self.assertRaises(ContentError):restore_checkpoints(self.enc,self.private,self.cp,expected_sources=source_files(self.root))
        self.assertTrue((self.cp/'state.json').exists())

    def test_unknown_neighbor_file_is_not_archived(self):
        (self.cp/'secret.pem').write_bytes(b'PRIVATE KEY')
        seal_checkpoints(self.cp,self.public,self.enc)
        out=self.base/'restored';restore_checkpoints(self.enc,self.private,out,expected_sources=source_files(self.root))
        self.assertFalse((out/'secret.pem').exists())

    def test_lock_prevents_snapshot_of_active_worker_tree(self):
        with batches.exclusive(self.cp), self.assertRaises(ContentError):seal_checkpoints(self.cp,self.public,self.enc)
        self.assertFalse(self.enc.exists())

    def test_symlink_in_private_tree_is_rejected(self):
        (self.cp/'secret').symlink_to('/etc/passwd')
        with self.assertRaises(ContentError):seal_checkpoints(self.cp,self.public,self.enc)
        self.assertFalse(self.enc.exists())

    def test_tar_traversal_symlink_and_duplicate_rejected(self):
        for mode in ('traversal','symlink','duplicate'):
            archive=self.base/(mode+'.tar');out=self.base/mode;out.mkdir()
            with tarfile.open(archive,'w') as tar:
                info=tarfile.TarInfo('../escape' if mode=='traversal' else 'a')
                if mode=='symlink':info.type=tarfile.SYMTYPE;info.linkname='/etc/passwd';tar.addfile(info)
                else:
                    info.size=1;tar.addfile(info,io.BytesIO(b'x'))
                    if mode=='duplicate':tar.addfile(info,io.BytesIO(b'x'))
            with self.subTest(mode=mode),self.assertRaises(ContentError):_extract(archive,out)
            self.assertFalse((self.base/'escape').exists())

    def test_completed_payload_corruption_blocks_sealing(self):
        file=next((self.cp/'bundles').glob('*/objects/*.bin'));file.write_bytes(b'broken')
        with self.assertRaises(ContentError):seal_checkpoints(self.cp,self.public,self.enc)
        self.assertFalse(self.enc.exists())

    def test_partial_object_journal_survives_encryption_and_fresh_workspace(self):
        source = next(s for s in source_files(self.root) if s['path'] == 'assets.bundle')
        folder = self.cp / 'partials' / batches.bundle_key(source)
        def interrupt(cursor):
            if cursor['phase'] == 'image':
                raise KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            read_bundle(self.root, source, folder, loader=self.loader,
                        mesh_decoder=self.decoder, progress=interrupt,
                        journal_recipe=batches.recipe())
        seal_checkpoints(self.cp, self.public, self.enc)
        shutil.rmtree(self.cp)
        restored = self.base / 'new-checkpoints'
        restore_checkpoints(self.enc, self.private, restored,
                            expected_sources=source_files(self.root))
        seen = []
        with patch.object(self.objects['assets.bundle'][0], 'get_raw_data',
                          side_effect=AssertionError('sealed object decoded twice')):
            result = read_bundle(self.root, source, restored/'partials'/batches.bundle_key(source),
                                 loader=self.loader, mesh_decoder=self.decoder,
                                 journal_recipe=batches.recipe(), progress=seen.append)
        self.assertEqual(sum(c['phase'] == 'reused' for c in seen), 3)
        self.assertEqual(len(result['objects']), 4)
        self.assertTrue(all(not o['errors'] for o in result['objects']))

    def test_restoration_does_not_claim_corrupt_partial_objects_are_reusable(self):
        source = next(s for s in source_files(self.root) if s['path'] == 'scene.bundle')
        folder = self.cp / 'partials' / batches.bundle_key(source)
        result = read_bundle(self.root, source, folder, loader=self.loader,
                             mesh_decoder=self.decoder, journal_recipe=batches.recipe())
        artifact = result['objects'][0]['artifacts'][0]['path']
        (folder/artifact).write_bytes(b'tampered')
        seal_checkpoints(self.cp, self.public, self.enc)
        restored = self.base / 'new-checkpoints'
        report = restore_checkpoints(self.enc, self.private, restored,
                                     expected_sources=source_files(self.root))
        self.assertEqual(report['status'], 'restored-not-catalog-verified')
        with self.assertRaises(ContentError):
            read_bundle(self.root, source, restored/'partials'/batches.bundle_key(source),
                        loader=self.loader, mesh_decoder=self.decoder, journal_recipe=batches.recipe())
