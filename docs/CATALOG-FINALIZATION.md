# Resumable final catalog assembly

This is a raw-object catalog gate, not Addressables decoding, original C# source
recovery, original art conversion, or Android acceptance.

## Why a separate finalization pass exists

Original source run `37359017829` at `14e07e3` sealed all **222 bundles** but
exhausted its shared inventory budget during finalization. Its metadata artifact
`11368488005` has ZIP SHA-256
`fef3c1cb3ddf5fc2b31bad856e7fffa4eeff77e89624d52489fe502501208d16`.
The four recorded passes ended at 89, 199, 208, and 222 completed bundles. The
terminal phase was `awaiting-finalization`, with a finalizer `TimeoutExpired`;
there was no published complete catalog. Those counts do not establish that
all cross-file references resolve or that every media format was converted.

The existing decoder/checkpoint recipe is deliberately unchanged. The new
`tools/content_finalize.py` reads sealed receipts without rerunning UnityPy.
It uses a separate private assembly workspace, retains verified independent
copies and resolved-reference chunks, and finally runs the **unchanged full
catalog validator** before publishing the existing catalog schema.

## Run against an existing complete checkpoint set

Use the same Python version and five-module decoder recipe that created the
checkpoints. Restore encrypted raw checkpoints with the existing documented
owner-held key workflow when necessary. Do not overwrite or relabel a recipe.

```sh
python tools/content_finalize.py /absolute/original/Contents/Resources/Data \
  /absolute/new-catalog --checkpoints /absolute/private-checkpoints \
  --workspace /absolute/private-finalization --budget 1800 \
  --progress-report /absolute/reports/finalization-progress.json
```

After an interruption, repeat the exact command with `--resume`. The workspace
and output must share a filesystem. Source, checkpoint, output, workspace and
report paths must not overlap or traverse symlinks. Both existing decoder and
continuation locks prevent concurrent mutation of the same checkpoint set.

An interrupted copy is discarded; a completed copy is rehashed and reused without
rewriting. Copies are independent of checkpoint payloads. Reference-cache chunks
are tied to the exact checkpoint ledger and new finalizer code. Cached edges are
not authority: the final full validator recomputes all pointer edges and checks
artifact hashes, numeric diagnostics and empty-font ownership again. It rejects
forged or omitted references even if a cache receipt was edited to match.

Publication records both the legacy `final.json` and a private assembly receipt.
A crash immediately after publication is recoverable without replacing output.
A consistent catalog containing unresolved references remains `incomplete`;
it is retained for diagnosis and never becomes a successful extraction.

Exit statuses: **0** = finished indexed catalog; **2** = a budget pause or finished
incomplete catalog; **1** = validation/operational failure. Inspect `phase` and
`status`, not just the presence of a directory or a cached file.

## Source runner and CI

`scripts/recover_source.py --finalization-budget 1800` schedules this separate
pass only if all bundles are sealed and the prior boundary is awaiting
finalization with no non-timeout errors. Partial decoding and integrity errors
do not qualify. A successful child without an indexed catalog is still failure.
The original inventory-stage error remains in the report even after a later
successful finalization. Source recovery, Godot import and complete-game flags
are not conflated.

The workflow budgets 3,600 seconds for inventory plus 1,800 for dedicated
finalization. The job limit is 120 minutes to leave room for installation,
extraction, checkpoint sealing and upload. Progress and diagnostic logs are the
only new public artifacts; original payloads remain in the private workspace.

## Limits that remain explicit

The budget is cooperative between I/O units and reference chunks. Source/batch
validation and the final full catalog verifier still perform complete passes;
the caller must enforce an outer process timeout. The source runner does so.
A timeout in the final verifier does **not** cache a successful verification.

The existing encrypted-checkpoint tool transports raw bundle checkpoints, not
this separate assembly workspace. Cross-host restoration can reuse the raw
objects but starts a new assembly workspace. Keep a compatible private local
workspace to resume copied artifacts/reference chunks on the same host. Old
legacy `assemble-*` scratch directories are not trusted or reused.

Raw preservation still does not decode Addressables locations, compressed or
humanoid animation semantics, FMOD event/sample behavior, custom shaders, scene
collision or lighting. An `indexed` raw catalog is not a complete recovered game.

## Regression commands

```sh
python -m unittest tests.test_content_finalize tests.test_source_finalization -v
GODOT_TEST_BIN=/path/to/godot python -m unittest discover -s tests -v
GODOT=/path/to/godot bash scripts/test_godot.sh verification
```

The authored tests cover exact equality with legacy finalization, copy and graph
interruption, an actual SIGKILL/resume, legacy receipt compatibility, numeric and
font evidence, corruption, both locks, path isolation, publication recovery and
source-runner policy. Authored fixtures are not production extraction evidence.
