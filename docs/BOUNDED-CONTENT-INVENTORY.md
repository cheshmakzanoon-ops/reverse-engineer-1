# Bounded, resumable content inventory

This is recovery infrastructure, not a completed original-content Godot port.
The motivating source-recovery run (`37189910146`, source `d7d4172`) verified
and extracted the owner's DMG, then timed out in inventory after 2,400 seconds.
Its report had no completed catalog and its inventory log had no object cursor.
The timeout does not establish which decoder or object caused the delay.

## Use on an extracted, verified source

Keep the original, checkpoint directory, final catalog and shareable report
separate. The source, catalog and checkpoint directories must not contain each
other. The optional progress report must also be outside those directories.

```sh
python3 tools/recover_content.py inventory \
  /private/Game.app/Contents/Resources/Data \
  /private/content-catalog \
  --checkpoints /private/content-checkpoints \
  --bundle-timeout 120 --budget 1800 \
  --progress-report /reports/inventory-progress.json
```

Each bundle has its own decoder process and timeout. Final catalog assembly is
also a bounded subprocess. `--budget` limits decoder/finalizer execution after
integrity checking; it is not a filesystem-I/O wall-clock guarantee. There is
no per-process address-space limit and no performance improvement percentage
claimed. The current code still decodes full typetrees, meshes and images.

A limit, failed worker or unresolved source reference exits nonzero. A timeout
never turns a partially written bundle into a sealed checkpoint. Good bundles
remain reusable; later bundles can still be attempted within the remaining
budget. To perform a small first pass, add `--max-bundles 10`.

## Resume without repeating completed decoding

Retain the private workspace on the same host, then use the same source and
output paths with `--resume`:

```sh
python3 tools/recover_content.py inventory \
  /private/Game.app/Contents/Resources/Data \
  /private/content-catalog \
  --checkpoints /private/content-checkpoints --resume \
  --bundle-timeout 240 --budget 1800 \
  --progress-report /reports/inventory-progress.json
```

A changed timeout/budget is allowed. A changed source list/hash/size, Python
version, decoder implementation hash or Unity/UnityPy pin is not. Reuse also
checks sealed receipt hashes, object identities, ownership, artifact paths and
actual payload hashes. A tampered cache fails rather than silently resetting.
The transitive Python dependencies are still not a fully locked environment;
use a fresh checkpoint directory when replacing that environment.

Only one process may own a checkpoint directory. An advisory POSIX lock rejects
concurrent writers. Completed bundle directories are renamed into place before
the atomic ledger update; a restart can adopt a verified receipt left in that
small interruption window. Unsealed attempt directories are never reused.

The complete global reference graph is resolved only after every bundle has a
sealed result. All existing catalog validation remains in place. Bundles with
recorded decoder errors can be sealed for diagnostics, but their final catalog
is **incomplete**, not successful. Missing external objects are not substituted.
For supported fixture content the final catalog and payload files match the
legacy unbatched path byte-for-byte.

## Files and status meanings

Private checkpoint directory:

- `state.json`: source manifest, decoder recipe and sealed receipt hashes.
- `bundles/<source-path-hash>/`: raw records, typetrees, decoded media and receipt.
- `attempt-*` / `finalize-*`: private failed-attempt data and worker logs.
- `progress.json`: mutable metadata-only progress.
- `final.json`: final catalog hash, counts and output identity.

Failed attempts are retained to diagnose the decoder and may consume disk.
Delete only unsealed attempts after stopping the job and inspecting them, or
start with a fresh private workspace. Do not delete the sealed bundle folders
while expecting resume to work. Final assembly currently copies payloads, so
allow additional free space for both checkpoints and the final catalog.

Progress reports contain counts, bundle-relative names, failure classes and an
allowlisted object ID/type/phase cursor. They do not contain raw object bytes,
typetrees, image pixels, sound data, native code or private worker log output.
Progress is not itself evidence that all media or original methods are recovered.
`completed_bundles` means sealed worker results, including recorded decode errors.
`indexed` means that the final catalog passed its gates, not that it is Godot-
imported, faithful to native gameplay, or Android/device-tested.

SIGTERM sent to the inventory CLI is handled so the active bounded-worker context
can terminate its process group. Timeout tests also verify that a grandchild
heartbeat stops. Atomic updates use file flush/fsync and replacement; this is
not a filesystem power-loss durability or adversarial-host security guarantee.

## Hosted recovery

`scripts/recover_source.py` now uses this path by default. It exposes
`--bundle-timeout` and `--inventory-budget`, and includes progress in recovery
metadata even when no complete catalog exists. Its outer process timeout is
longer than the worker budget. Source and Android policies are unchanged.

The public workflow uploads only the metadata report. **It does not upload
checkpoint payloads or private worker logs**, and its final cleanup still removes
the ephemeral original workspace. Therefore this change does not claim resume
across separate hosted jobs. Cross-job reuse requires an explicitly authorized
private/encrypted transfer, not a plaintext public artifact or Actions cache.
The existing recipient key and transfer workflow are not changed here.

## Verify

```sh
python3 -m unittest tests.test_content_batches -v
GODOT_TEST_BIN=/path/to/godot python3 -m unittest discover -s tests -v
```

The new tests use authored interface fixtures and real subprocess/finalizer
execution. They are not evidence of decoding the original game bundles. No
new mesh, animation, shader, FMOD or native-code compatibility is claimed.

Primary API references used for the subprocess lifecycle:
https://docs.python.org/3.13/library/subprocess.html
https://pypi.org/project/UnityPy/1.25.4/
