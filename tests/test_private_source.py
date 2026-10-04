"""Encrypted development transfer tests; fixtures contain no game or key material."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


@unittest.skipUnless(importlib.util.find_spec('cryptography'), 'optional private-transfer dependency')
class PrivateSourceTests(unittest.TestCase):
    def setUp(self):
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives import serialization as s
        from tools import private_source
        self.tool=private_source
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.key=rsa.generate_private_key(public_exponent=65537,key_size=3072)
        self.public=self.key.public_key().public_bytes(s.Encoding.PEM,s.PublicFormat.SubjectPublicKeyInfo)
        self.private=self.key.private_bytes(s.Encoding.PEM,s.PrivateFormat.PKCS8,s.NoEncryption())
        self.source=self.root/'fixture';self.source.write_bytes(b'authored transfer test fixture'*20)
        self.sha=hashlib.sha256(self.source.read_bytes()).hexdigest();self.size=self.source.stat().st_size
        self.out=self.root/'sealed';self.restored=self.root/'restored'

    def seal(self):
        return self.tool.seal(self.source,self.public,self.out,expected_sha=self.sha,expected_size=self.size,chunk_size=128)

    def unseal(self):
        return self.tool.unseal(self.out,self.private,self.restored,expected_sha=self.sha,expected_size=self.size)

    def test_roundtrip_across_parts_and_no_plaintext_in_envelope(self):
        self.seal();self.unseal()
        self.assertEqual(self.source.read_bytes(),self.restored.read_bytes())
        for p in self.out.rglob('*'):
            if p.is_file():
                self.assertNotIn(b'authored transfer test fixture',p.read_bytes())
                self.assertNotIn(b'PRIVATE KEY',p.read_bytes())

    def test_corruption_does_not_publish_partial_source(self):
        self.seal();p=self.out/'part-01/data.enc';b=bytearray(p.read_bytes());b[-1]^=1;p.write_bytes(b)
        with self.assertRaises(Exception):self.unseal()
        self.assertFalse(self.restored.exists())

    def test_reordered_parts_fail_authentication_or_identity(self):
        self.seal();p=self.out/'part-00/metadata.json';m=json.loads(p.read_text());m['header']['index']=1;p.write_text(json.dumps(m))
        with self.assertRaises(Exception):self.unseal()
        self.assertFalse(self.restored.exists())

    def test_wrong_recipient_cannot_read(self):
        self.seal()
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives import serialization as s
        k=rsa.generate_private_key(public_exponent=65537,key_size=3072)
        self.private=k.private_bytes(s.Encoding.PEM,s.PrivateFormat.PKCS8,s.NoEncryption())
        with self.assertRaises(Exception):self.unseal()
        self.assertFalse(self.restored.exists())

    def test_existing_output_is_never_overwritten(self):
        self.seal();self.restored.write_bytes(b'preserve')
        with self.assertRaises(Exception):self.unseal()
        self.assertEqual(self.restored.read_bytes(),b'preserve')
        with self.assertRaises(Exception):self.seal()

    def test_missing_chunk_fails(self):
        self.seal();(self.out/'part-01/data.enc').unlink()
        with self.assertRaises(Exception):self.unseal()
        self.assertFalse(self.restored.exists())

    def test_wrong_source_hash_cannot_be_sealed(self):
        with self.assertRaises(Exception):
            self.tool.seal(self.source,self.public,self.out,expected_sha='0'*64,expected_size=self.size)
        self.assertFalse(self.out.exists())

    def test_different_encryptions_use_fresh_keys_and_nonces(self):
        self.seal();other=self.root/'other'
        self.tool.seal(self.source,self.public,other,expected_sha=self.sha,expected_size=self.size,chunk_size=128)
        self.assertNotEqual((self.out/'part-00/data.enc').read_bytes(),(other/'part-00/data.enc').read_bytes())


if __name__=='__main__':unittest.main()
