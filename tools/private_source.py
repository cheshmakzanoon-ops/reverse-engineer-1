#!/usr/bin/env python3
"""Recipient-only transfer of the exact source DMG into a private development host.

Only encrypted chunks are suitable for artifact transport. The private recipient
key never leaves the receiving host. RSA-OAEP remains supported for existing
archives; X25519-HKDF is supported for short-lived reconstruction recipients.
This is not release signing or DRM removal.
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
X25519_LABEL=b'kart-private-source-x25519-v1'
X25519_PUBLIC_PREFIX=b'x25519:'
X25519_PRIVATE_PREFIX=b'x25519-private:'
X25519_SCHEME='x25519-hkdf-sha256-aesgcm-v1'
CHUNK=192*1024*1024


def crypto():
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    return serialization, rsa, AESGCM, padding.OAEP(
        mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=LABEL)


def xcrypto():
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    return hashes, X25519PrivateKey, X25519PublicKey, AESGCM, HKDF


def _decode_prefixed(value: bytes, prefix: bytes, label: str) -> bytes:
    stripped=value.strip()
    if not stripped.startswith(prefix):
        raise ContentError(f'{label} prefix is missing')
    try:
        raw=base64.b64decode(stripped[len(prefix):],validate=True)
    except Exception as exc:
        raise ContentError(f'Invalid {label} encoding') from exc
    if len(raw)!=32:
        raise ContentError(f'{label} must contain exactly 32 raw bytes')
    return raw


def _x25519_key(shared: bytes, header: dict) -> bytes:
    hashes,_,_,_,HKDF=xcrypto()
    return HKDF(algorithm=hashes.SHA256(),length=32,salt=None,
        info=X25519_LABEL+canonical(header)).derive(shared)


def seal(source: Path, public_pem: bytes, output: Path, *, expected_sha=DMG_SHA256,
         expected_size=DMG_BYTES, chunk_size=CHUNK) -> dict:
    verify_source(source,expected_sha256=expected_sha,expected_size=expected_size)
    if not 1 <= chunk_size <= CHUNK:raise ContentError('Invalid bounded chunk size')
    if output.exists() or output.is_symlink():raise ContentError('Use a new encrypted output directory')

    use_x25519=public_pem.strip().startswith(X25519_PUBLIC_PREFIX)
    serial,rsa,AESGCM,oaep=crypto()
    x_public=None
    rsa_public=None
    if use_x25519:
        _,_,X25519PublicKey,_,_=xcrypto()
        try:
            x_public=X25519PublicKey.from_public_bytes(
                _decode_prefixed(public_pem,X25519_PUBLIC_PREFIX,'X25519 public key'))
        except ValueError as exc:
            raise ContentError('Invalid X25519 public key') from exc
    else:
        rsa_public=serial.load_pem_public_key(public_pem)
        if not isinstance(rsa_public,rsa.RSAPublicKey) or rsa_public.key_size<3072:
            raise ContentError('RSA 3072+ recipient public key required')

    output.mkdir(parents=True)
    count=(expected_size+chunk_size-1)//chunk_size
    manifest={'schema':1,'sha256':expected_sha,'bytes':expected_size,
              'parts':count,'chunk_size':chunk_size}
    if use_x25519:manifest['scheme']=X25519_SCHEME

    with source.open('rb') as stream:
        for index in range(count):
            plain=stream.read(chunk_size)
            header={**manifest,'index':index,'part_bytes':len(plain)}
            nonce=os.urandom(12)
            if use_x25519:
                _,X25519PrivateKey,_,XAESGCM,_=xcrypto()
                ephemeral=X25519PrivateKey.generate()
                shared=ephemeral.exchange(x_public)
                secret=_x25519_key(shared,header)
                cipher=XAESGCM(secret).encrypt(nonce,plain,canonical(header))
                from cryptography.hazmat.primitives import serialization as xserial
                ephemeral_public=ephemeral.public_key().public_bytes(
                    xserial.Encoding.Raw,xserial.PublicFormat.Raw)
                key_record={'ephemeral_public':base64.b64encode(ephemeral_public).decode()}
            else:
                secret=AESGCM.generate_key(bit_length=256)
                cipher=AESGCM(secret).encrypt(nonce,plain,canonical(header))
                key_record={'wrapped_key':base64.b64encode(rsa_public.encrypt(secret,oaep)).decode()}
            folder=output/f'part-{index:02d}';folder.mkdir()
            (folder/'data.enc').write_bytes(cipher)
            (folder/'metadata.json').write_bytes(canonical({'header':header,
                'nonce':base64.b64encode(nonce).decode(),**key_record,
                'cipher_sha256':hashlib.sha256(cipher).hexdigest()}))
    verify_source(source,expected_sha256=expected_sha,expected_size=expected_size)
    (output/'manifest.json').write_bytes(canonical(manifest))
    return manifest


def unseal(directory: Path, private_pem: bytes, output: Path, *, expected_sha=DMG_SHA256,
           expected_size=DMG_BYTES) -> dict:
    if output.exists() or output.is_symlink():raise ContentError('Existing private source is never overwritten')
    manifest=json.loads(safe_child(directory,'manifest.json').read_text())
    if manifest.get('schema')!=1 or manifest.get('sha256')!=expected_sha or manifest.get('bytes')!=expected_size:
        raise ContentError('Encrypted transfer is not the expected original')
    size=manifest['chunk_size'];count=manifest['parts']
    if not isinstance(size,int) or not 1<=size<=CHUNK or count!=(expected_size+size-1)//size:
        raise ContentError('Invalid chunk layout')

    use_x25519=manifest.get('scheme')==X25519_SCHEME
    serial,rsa,AESGCM,oaep=crypto()
    x_private=None
    rsa_private=None
    if use_x25519:
        _,X25519PrivateKey,_,_,_=xcrypto()
        try:
            x_private=X25519PrivateKey.from_private_bytes(
                _decode_prefixed(private_pem,X25519_PRIVATE_PREFIX,'X25519 private key'))
        except ValueError as exc:
            raise ContentError('Invalid X25519 private key') from exc
    else:
        rsa_private=serial.load_pem_private_key(private_pem,password=None)
        if not isinstance(rsa_private,rsa.RSAPrivateKey):raise ContentError('RSA private key required')

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
                if use_x25519:
                    try:
                        ephemeral=base64.b64decode(record['ephemeral_public'],validate=True)
                    except Exception as exc:
                        raise ContentError('Invalid ephemeral X25519 public key encoding') from exc
                    if len(ephemeral)!=32:raise ContentError('Invalid ephemeral X25519 public key length')
                    _,_,X25519PublicKey,XAESGCM,_=xcrypto()
                    try:
                        peer=X25519PublicKey.from_public_bytes(ephemeral)
                        secret=_x25519_key(x_private.exchange(peer),header)
                        plain=XAESGCM(secret).decrypt(base64.b64decode(record['nonce'],validate=True),
                            path.read_bytes(),canonical(header))
                    except Exception as exc:
                        raise ContentError('X25519 chunk authentication failed') from exc
                else:
                    secret=rsa_private.decrypt(base64.b64decode(record['wrapped_key'],validate=True),oaep)
                    plain=AESGCM(secret).decrypt(base64.b64decode(record['nonce'],validate=True),
                        path.read_bytes(),canonical(header))
                if len(plain)!=header['part_bytes']:raise ContentError('Wrong decoded chunk length')
                stream.write(plain)
            stream.flush()
        result=verify_source(temp,expected_sha256=expected_sha,expected_size=expected_size)
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
