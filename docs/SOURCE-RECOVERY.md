# Verified original-source intake

`source-recovery.yml` reads the owner's supplied Drive copy of the exact existing
DMG. It does not modify Drive or the LFS original. The input must match the
1,190,250,225-byte / `4720e9fbaabd6d39bceac362e271e24e9c08d8f549191f70bad34e6d5c28b6be`
source gate before an extractor is invoked. LFS pointers, HTML downloads and
truncation do not pass this gate.

The ordinary Drive connector's 256 MiB raw-file limit cannot deliver this file,
and the local implementation container cannot resolve download hosts. The
repository's opt-in/manual or source-tool-change-triggered GitHub runner is a
network-equipped execution route. It uses no Google credentials/browser cookies.
A public-link access or quota failure stops the run; it does not change sharing
permissions or substitute a different game version.

## Local reproduction on a network-equipped Linux host

Use Python 3.12, 7-Zip (`7zz` or `7z`) with DMG/HFS support and:

```sh
python -m venv .venv-content
.venv-content/bin/python -m pip install gdown==5.2.0 -r tools/content-requirements.txt
.venv-content/bin/python scripts/recover_source.py \
  --drive-id 15gzBmVRHNf8PpJAH2Ri0ncDfjnsgdorb \
  --workspace /absolute/new-private-media \
  --reports /absolute/new-diagnostics
```

An already downloaded original can be supplied with `--dmg /path/original.dmg`
instead. Inputs are never overwritten. Workspace/report paths must be new and
nonoverlapping. The CLI verifies the original before extraction and again after
inventory, hashes extracted regular files, checks native/IL2CPP headers, then
executes the strict existing bundle inventory. Neither the macOS native binary
nor code from its assets is executed.

The media workspace retains extracted inputs and per-object recovery products
for local analysis. The hosted job deletes its ephemeral copy at completion.
Only an explicit allowlist of logs and metadata is uploaded: input hashes,
filenames, object identifiers/types, references, artifact hashes, counts and
failure reasons. No original DMG, native binary, raw object, texture, mesh,
audio or font bytes are included in public artifacts. A full catalog index
without its media is diagnostic evidence, not a redistributable recovered game.

## Evidence boundaries

`source_verified`, `extracted`, `inventory_complete`, `godot_imported` and
`original_source_recovered` are independent. An incomplete inventory is retained
for repair but the process exits nonzero. A successful inventory remains
`inventoried-not-ported`. Actual source behavior, compressed animation decoding,
Addressables binding, audio, art integration and Android acceptance are not
promoted by this stage. Unit tests use authored fixtures, not original bundles.

The initial source-intake implementation adds 16 regressions for input selection,
header validation, deterministic extracted manifests, sidecar exclusion,
symlink rejection, output preservation, command failures/timeouts and honest
status/report separation. Production results must be attached to an actual run
and source commit; adding this workflow is not itself evidence of extraction.
