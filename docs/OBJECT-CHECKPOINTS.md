# Object-level and encrypted recovery checkpoints

This extends the bounded bundle inventory; it is not asset conversion, original
C# method-body recovery, or proof of a complete game. Keep all plaintext
checkpoints, decoded media, original binaries and private keys outside Git.

## What survives interruption

Each production worker now writes a private `partials/<bundle-key>` directory.
`object-journal.sqlite3` records complete objects in deterministic source order.
The journal binds the input bundle hash/size, serialized-file metadata, ordered
object identities/types, and the decoder recipe. Each record contains a digest
and the hashes/sizes of its own raw, typetree and supported decoded outputs.

Artifacts are flushed before committing their record. On reopening, every sealed
record and artifact is verified before reuse. The first uncommitted object is
retried; only that object's known orphan paths may be removed. A texture decoder
interrupted halfway through a texture starts that texture again, not every earlier
object. SQLite rollback journaling is retained in portable snapshots. This does
not claim arbitrary machine/power-loss durability or multiwriter support.

Decode errors are preserved as explicit object errors. A sealed record means
"this processing result was saved", not "this object is usable in Godot". Likewise,
a sealed bundle may contain recorded errors; the unchanged final catalog gate
requires all bundles and reports any object/reference errors as incomplete.

The global inventory lock excludes concurrent inventory/snapshot operations. Each
object journal additionally excludes a second worker. Source mutation, decoder
changes, reordered/missing objects, corrupted payloads, gaps, symlinks and altered
record digests reject reuse. The legacy unbatched path has no journal and retains
its existing catalog format.

## Local continuation

Install `tools/content-requirements.txt` in an isolated Python environment. Use the
same Python patch version and decoder recipe when restoring; the hosted recovery
workflow pins Python 3.13.5. `source_files` hashes the complete extracted Data tree,
including resource streams, catalogs and banks. Original source identity is checked
separately against the fixed DMG size and SHA-256.

```sh
python tools/recover_content.py inventory /private/Game.app/Contents/Resources/Data \
  /private/catalog --checkpoints /private/checkpoints \
  --bundle-timeout 120 --budget 1800 --progress-report /reports/progress.json
# An incomplete run exits nonzero. Continue the same private work explicitly:
python tools/recover_content.py inventory /private/Game.app/Contents/Resources/Data \
  /private/catalog --checkpoints /private/checkpoints --resume \
  --bundle-timeout 120 --budget 1800 --progress-report /reports/progress.json
```

A genuinely stuck object can still fail repeatedly. The correct response is to
inspect its cursor/raw bytes and fix that decoder, not mark it as successful.
Increasing a timeout is not evidence of repaired content.

## Encrypted transfer to a fresh workspace

`tools/content_checkpoints.py` reuses the existing recipient-only chunk transport
from `private_source.py`. It creates a temporary private tar, encrypts it, and
removes the temporary tar. Only state and bundle/partial payload directories are
eligible; neighboring key files and lock files are not exported. Restoration
accepts only bounded regular archive members, rejects links/traversal/duplicates,
checks the exact source/recipe, and verifies completed bundle receipts/payloads.
Partial journal validation happens against the actual Unity readers before reuse.

```sh
python tools/content_checkpoints.py seal /private/checkpoints \
  /private/recipient-public.pem /transfer/encrypted-checkpoints
python tools/content_checkpoints.py restore /transfer/encrypted-checkpoints \
  /private/recipient-private.pem /private/restored-checkpoints \
  --original /private/Game.app/Contents/Resources/Data
```

Keep the private key with the receiving owner, not in Git, logs or public
artifacts. Encryption prevents public disclosure and detects changed ciphertext;
it does not establish who produced an archive. Select a trusted producer run and
its exact artifact, rather than accepting an arbitrary public artifact. Retain
older private keys while their encrypted checkpoints are still needed.

The default archive ceiling is 40 GiB, including a separate expanded-size check.
Sealing/restoring needs temporary disk space for the tar and restored content.
These are bounded local file operations, not a streaming cloud database. Only
ciphertext directories are suitable for upload.

## Hosted workflow

`source-recovery.yml` now seals existing private checkpoints before its final
workspace cleanup, even when inventory returns an ordinary failure. The encrypted
artifact is `encrypted-checkpoints-<commit>-<run-attempt>`; the separately uploaded
progress reports contain metadata only. A hard runner loss or cancellation may
prevent the upload, so this is not an unconditional cloud-durability guarantee.

For a manual continuation, supply the trusted producer's `checkpoint_run_id` and
exact `checkpoint_artifact`. Both inputs and the protected repository secret
`RECOVERY_CHECKPOINT_PRIVATE_KEY` are required. That secret must contain the private
key matching the producer's public recipient key. It is written to a temporary
0600 file for restoration, removed on exit, and never committed. This implementation
does not create or configure the owner's secret automatically.

The source runner verifies and extracts the DMG again, restores the checkpoints,
then invokes inventory with `--resume`. A fresh default push still performs a
fresh recovery: it does not guess an archive, receiving key or producer run.

## Validation

```sh
python -m unittest tests.test_content_objects tests.test_content_checkpoints \
  tests.test_source_recovery -v
GODOT_TEST_BIN=/path/to/godot python -m unittest discover -s tests -v
```

Fixtures prove killed-child continuation, exact resumed/uninterrupted payloads,
complete and partial encrypted round trips, corruption rejection and source-runner
wiring. Production evidence is recorded separately in
[the object-resume status report](status/2026-10-04-object-resume.md). Neither a
fixture nor a restored checkpoint establishes that all original assets are decoded
or that any production scene has been integrated into Godot.
