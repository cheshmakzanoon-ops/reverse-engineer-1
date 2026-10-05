# Bounded same-job inventory continuation

The source runner now continues an incomplete inventory in the **same private
workspace** instead of abandoning it after one scheduling pass. This is scheduling
around the existing decoder, not a new decoder, a complete catalog, or imported
Godot content. Original raw files and private keys are never public report inputs.

## Reason for this increment

Original-source workflow `37308162343` on `eea54a4` paused after 192 of 222 bundles.
It recorded a timeout in the Emperor Zing arena bundle at a Transform typetree,
with 901 objects reported. The metadata artifact was downloaded and SHA-256 checked:

`6983312cc66ab35bf516e77e25b74239b733801dcbbf3c23503980a45e715233`

That observation shows a timeout/budget boundary, **not a diagnosed corrupt
Transform**. The earlier font-atlas fix is retained. Nothing here changes source
values, the decoder recipe, or the rules for accepting recovered objects.

## Command

From the repository root, with already extracted, authorized original data:

```sh
python tools/recover_content.py inventory /private/game/Contents/Resources/Data \
  /private/catalog --checkpoints /private/checkpoints \
  --continue-until-complete --budget 3600 --pass-budget 900 \
  --bundle-timeout 120 --max-bundle-timeout 480 --max-passes 8 \
  --progress-report /reports/inventory-progress.json \
  --continuation-report /reports/inventory-continuation.json
```

Use `--resume` on the first pass only when checkpoints already exist. Subsequent
same-job passes request resume explicitly. A fresh history report is required for
a new invocation; previous evidence is not overwritten. The old one-pass CLI
remains available by omitting `--continue-until-complete`. Single-pass
`--max-bundles` is deliberately incompatible with automatic continuation.

## Policy

The total budget is shared, not reset for each pass. Source/checkpoint integrity
I/O is still performed by the existing inventory implementation. That I/O cannot
be preempted by the scheduling loop; the source runner retains its separate outer
process timeout (`inventory-budget + 600` seconds). The workflow reserves further
headroom for encrypted checkpoint sealing and metadata retention. These numbers
are configured safety limits, not an extraction-time or performance claim.

Worker timeouts back off from the initial value to an explicit cap. Only
budget pauses and timeout failures are retried. Source mismatches, corrupt saved
payloads, unsupported worker failures, and nonzero finalizer exits stop the
controller. Once all bundles are sealed, the finalizer receives the remaining
shared budget instead of another small pass allocation. A finished catalog with
reference or decoding errors is terminal and stays `incomplete`.

A pass limit and consecutive no-progress limit bound attempts. Completed bundle
counts and timeout cursor object counts are **liveness hints, not validation**.
A cursor never authorizes reuse, skips a resource, or makes an incomplete catalog
successful. Every resumed pass still runs the existing source/recipe/receipt and
artifact verification. Every bundle must be accounted for before finalization.

A separate controller lock spans gaps between passes. The existing per-pass
inventory lock remains in place. Do not run a legacy inventory command against
a workspace owned by an active controller.

## Recipe compatibility and privacy

No file in the existing five-module decoder recipe changed. Compatible saved
objects therefore remain usable; the controller does not relabel old recipes.
Cross-run encrypted restoration still requires its existing owner-held receiving
key. Same-job continuation uses the private workspace directly and needs no
additional key or new public recipient rotation.

`inventory-continuation.json` contains policy, pass counters, approved failure
identities, elapsed time and the reason scheduling stopped. It contains no raw
objects, private worker logs, typetrees, original meshes, fonts or audio. The
source workflow's metadata allowlist includes this report before cleanup.

## Test scope

The continuation tests exercise the actual existing batch/finalizer code with
authored reader fixtures, plus real child-process timeout handling. One scale
fixture has 222 independent authored bundles, finishes across six passes, decodes
each bundle once and never publishes a partial catalog. A smaller resumed output
matches uninterrupted output byte-for-byte. Tampered saved artifacts still fail.

This fixture is **not the original game's 222-bundle inventory**. The modified
full source workflow has not yet supplied a production completion result. Full
cross-bundle validation, Addressables bindings, original scene conversion and
Godot integration remain separate gates.
