#!/usr/bin/env python3
"""Recipient-only transfer of the exact source DMG into a private development host.

Only encrypted chunks are suitable for public artifact transport. The private RSA
key never leaves the receiving host. This is not release signing or DRM removal.
"""
from __future__ import annotations
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import tempfile
try:
    from .content_pipeline import DMG_SHA256, DMG_BYTES, ContentError, canonical, verify_source, safe_child
except ImportError:
    from content_pipeline import DMG_SHA256, DMG_BYTES, ContentError, canonical, verify_source, safe_child

LABEL=b'kart-private-source-v1'
CHUNK=192*1024*1024


def crypto():
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    return serialization, rsa, AESGCM, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),algorithm=hashes.SHA256(),label=LABEL)


def seal(source: Path, public_pem: bytes, output: Path, *, expected_sha=DMG_SHA256,
         expected_size=DMG_BYTES, chunk_size=CHUNK) -> dict:
    verify_source(source,expected_sha256=expected_sha,expected_size=expected_size)
    if not 1 <= chunk_size <= CHUNK:raise ContentError('Invalid bounded chunk size')
    serial,rsa,AESGCM,oaep=crypto();public=serial.load_pem_public_key(public_pem)
    if not isinstance(public,rsa.RSAPublicKey) or public.key_size<3072:raise ContentError('RSA 3072+ recipient public key required')
    if output.exists() or output.is_symlink():raise ContentError('Use a new encrypted output directory')
    output.mkdir(parents=True)
    count=(expected_size+chunk_size-1)//chunk_size
    manifest={'schema':1,'sha256':expected_sha,'bytes':expected_size,'parts':count,'chunk_size':chunk_size}
    with source.open('rb') as stream:
        for index in range(count):
            plain=stream.read(chunk_size)
            header={**manifest,'index':index,'part_bytes':len(plain)}
            secret=AESGCM.generate_key(bit_length=256);nonce=os.urandom(12)
            cipher=AESGCM(secret).encrypt(nonce,plain,canonical(header))
            wrapped=public.encrypt(secret,oaep)
            folder=output/f'part-{index:02d}';folder.mkdir()
            (folder/'data.enc').write_bytes(cipher)
            (folder/'metadata.json').write_bytes(canonical({'header':header,
                'nonce':base64.b64encode(nonce).decode(),'wrapped_key':base64.b64encode(wrapped).decode(),
                'cipher_sha256':hashlib.sha256(cipher).hexdigest()}))
    verify_source(source,expected_sha256=expected_sha,expected_size=expected_size)
    (output/'manifest.json').write_bytes(canonical(manifest))
    return manifest


def unseal(directory: Path, private_pem: bytes, output: Path, *, expected_sha=DMG_SHA256,
           expected_size=DMG_BYTES) -> dict:
    if output.exists() or output.is_symlink():raise ContentError('Existing private source is never overwritten')
    serial,rsa,AESGCM,oaep=crypto();private=serial.load_pem_private_key(private_pem,password=None)
    if not isinstance(private,rsa.RSAPrivateKey):raise ContentError('RSA private key required')
    manifest=json.loads(safe_child(directory,'manifest.json').read_text())
    if manifest.get('schema')!=1 or manifest.get('sha256')!=expected_sha or manifest.get('bytes')!=expected_size:
        raise ContentError('Encrypted transfer is not the expected original')
    size=manifest['chunk_size'];count=manifest['parts']
    if not isinstance(size,int) or not 1<=size<=CHUNK or count!=(expected_size+size-1)//size:
        raise ContentError('Invalid chunk layout')
    output.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix='.private-source-',dir=output.parent)
    temp=Path(tmp)
    try:
        with os.fdopen(fd,'wb') as stream:
            for index in range(count):
                prefix=f'part-{index:02d}/'
                record=json.loads(safe_child(directory,prefix+'metadata.json').read_text())
                header={**manifest,'index':index,'part_bytes':min(size,expected_size-index*size)}
                if record['header']!=header:raise ContentError('Chunk identity/order does not match')
                path=safe_child(directory,prefix+'data.enc')
                verify_source(path,expected_sha256=record['cipher_sha256'],expected_size=header['part_bytes']+16)
                secret=private.decrypt(base64.b64decode(record['wrapped_key'],validate=True),oaep)
                plain=AESGCM(secret).decrypt(base64.b64decode(record['nonce'],validate=True),path.read_bytes(),canonical(header))
                if len(plain)!=header['part_bytes']:raise ContentError('Wrong decoded chunk length')
                stream.write(plain)
            stream.flush()
        result=verify_source(temp,expected_sha256=expected_sha,expected_size=expected_size)
        # Hard link is an atomic create-only publication on this filesystem.
        os.link(temp,output)
        return result
    finally:
        temp.unlink(missing_ok=True)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode',choices=['seal','unseal']);p.add_argument('source',type=Path)
    p.add_argument('key',type=Path);p.add_argument('output',type=Path)
    a=p.parse_args()
    result=(seal if a.mode=='seal' else unseal)(a.source,a.key.read_bytes(),a.output)
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
