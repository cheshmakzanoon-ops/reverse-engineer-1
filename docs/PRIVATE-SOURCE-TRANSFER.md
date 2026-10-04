# Private development source transfer

The supplied original can be processed directly on a network-equipped host with
`recover_source.py`. For a development session without direct download access,
`private-source-session.yml` transfers only recipient-encrypted chunks through
short-lived GitHub artifacts. The raw original is verified before and after
sealing and is removed from the hosted runner. No original media is uploaded
unencrypted. The recipient private key never enters Git or the runner.

`tools/source-session-public.pem` is a public RSA recipient key for the current
receiving development session, not an Android signing key or a secret. A new
recipient must generate a fresh RSA 3072-bit or stronger key pair locally and
replace this public key. Keep the private key outside the repository. Artifacts
expire after one day; possession of the public key cannot decrypt them. No
permanent storage or cross-session private-key availability is promised.

`tools/private_source.py` uses cryptography 46.0.4: independently random AES-256
GCM keys/nonces per chunk, RSA-OAEP/SHA-256 key wrapping, authenticated source /
chunk identities, ciphertext hashes and a final exact original-size/SHA-256
check. Reordered, missing or corrupted chunks cannot publish a partial DMG.
Decryption publishes a mode-0600 file without overwriting an existing original.

Combine the six downloaded part directories and identical manifest.json in a
private directory, then run on the receiving host:

```sh
python tools/private_source.py unseal /private/encrypted-parts \
  /private/recipient.key /private/source.dmg
python tools/recover_content.py verify-dmg /private/source.dmg
```

The companion dependency artifact contains the Linux decoder executable with its
copyright notice and Python wheels, not game media. The source workflow remains
independent: downloading an original is not inventory, native-method recovery,
Godot art integration, or an Android runtime claim. The eight encryption tests
use authored byte fixtures. They require the optional cryptography dependency.
