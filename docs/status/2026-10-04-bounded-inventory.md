# Bounded source-inventory increment — 2026-10-04

Code base: `067f98bcfbfbe329720715ad7fffddddbbf49e08`.
Concurrent upstream change preserved: `fc8c27fd13561326c191cfe291378d8ac9b7432b`.
Its source tree `9708afce686e0fcfcaf80030887ebcfc6aa1c2c3` was reproduced
exactly before applying this increment. Local baseline commits are offline
source snapshots, not a claim to have cloned the complete upstream history.

## Actual production evidence used

The supplied Drive file ID is `15gzBmVRHNf8PpJAH2Ri0ncDfjnsgdorb`.
The Drive connector confirmed the expected 1,190,250,225-byte file but rejected
the download because its 268,435,456-byte transfer limit is lower. Direct
container requests could not resolve the download host. No original DMG bytes
were materialized in this receiving runtime by those attempts.

Instead, the previous hosted production run was inspected:

- run `37189910146`, source commit `d7d4172b75f43048fb74edfe414c1886a2ff996d`;
- artifact `11298604939`, SHA-256
  `3b765e08ba2313a52b11aec7444593820b2b7a42716e175dd9626b87c9b02ad0`;
- `source_verified=true`, exact known DMG SHA-256
  `4720e9fbaabd6d39bceac362e271e24e9c08d8f549191f70bad34e6d5c28b6be`;
- `extracted=true`, 276 regular files, 1,596,079,924 extracted bytes;
- the retained file manifest lists 222 `.bundle` files and nine `.bank` files;
- native metadata header version 31, but `original_source_recovered=false`;
- `inventory_complete=false`, with `Command timed out; inspect inventory.log`.

The former inventory subprocess limit was 2,400 seconds. Its retained log
contained fallback-version warnings, not an object progress cursor. This is
evidence of an unbounded-work/progress-loss problem, not identification of a
specific slow decoder, a completed asset extraction, or original source recovery.
The downloaded artifact contains reports, not raw bundles or native method bodies.

## Implemented

The Unity decoder is factored into a shared single-bundle reader and unchanged-
schema global catalog finalizer. The new checkpoint path executes bundle workers
and finalization in separately bounded subprocesses. It stores sealed bundle
receipts, artifact/source hashes, decoder code hashes, version pins and progress.

Resume verifies sources, code/pins, receipts, object identities, artifact paths
and actual payload hashes before reuse. Missing or changed data fails. Completed
bundles are not decoded again. Unsealed attempts are not trusted. A POSIX lock
rejects concurrent writers, and the rename-before-ledger-update interruption
window is recoverable through receipt verification. Final catalog gates still
recompute cross-bundle references and refuse missing/substituted dependencies.

Timeouts and nonzero exits retain private logs and good checkpoints. Metadata-
only progress identifies the current bundle and last available object/type/phase;
worker exit codes are recorded without leaking exception payloads. SIGTERM and
worker-timeout handling terminate process groups instead of leaving active
helper processes behind. A real grandchild-heartbeat test covers that behavior.

The source orchestrator uses the bounded path by default and exposes per-bundle
and total execution budgets. Hosted CI retains progress metadata even without a
final catalog. Original media, checkpoint payloads and private worker logs are
still excluded from public artifacts. The ephemeral hosted workspace is still
cleaned, so cross-job checkpoint reuse is explicitly NOT claimed.

## Local tests

**146 Python tests passed**, including **30 new batch/process/orchestration
regressions**. The pinned-engine Android template component test was enabled,
not skipped. Tests cover stop/resume, byte-identical results against unbatched
inventory, source/code/cache tampering, concurrent locks, interrupted attempts,
finalizer failures, real child timeouts, descendant termination, safe metadata,
foreign output protection, CLI wiring and working-directory independence.

Positive decoder tests use authored reader fixtures. The original UnityPy/game-
bundle decoding path was not exercised here. Installing UnityPy 1.25.4 was
attempted but failed; no successful parser installation is claimed. Its public
API/pin and Python subprocess behavior were checked against primary references.

**483 Godot checks across all 16 suites passed.** The all-suite shell command
first reached the container tool's 45-second execution cap after eight complete
suites. The remaining suites were then executed in bounded individual/grouped
invocations with the same checked runner. This is a complete set of passing
suite logs, not a claim that the first shell invocation exited successfully.
Both six-kart three-lap clean/combat simulations, graphical screen-touch/menu
flow, physical race-to-save, and the authored GLB import checks passed.

Project import and a 360-frame headless frontend boot passed. All seven generated
recovered-data modules reproduced byte-for-byte. Screenshots were reviewed; the
game still renders engineering geometry. No runtime gameplay script changed.

## Commands

```sh
GODOT_TEST_BIN=/path/to/Godot_v4.7.2-stable_linux.x86_64 \
  python3 -m unittest discover -s tests -p 'test_*.py' -v
python3 scripts/checked_process.py --timeout 240 -- godot --headless --path port --import
GODOT=/path/to/godot bash scripts/test_godot.sh /path/to/verification
python3 scripts/checked_process.py --timeout 120 -- \
  godot --headless --path port --quit-after 360
```

Debug APK and test AAB export commands were attempted with the restored pinned
engine and local JDK. Both failed dependency preflight because SDK command-line
tools with `apkanalyzer` were unavailable. Release AAB failed the existing
owner-controlled signing-variable gate. **No APK, AAB, emulator or physical-device
result was produced by this increment.**

## Limitations and next real-input gate

This change does not implement new compressed/humanoid animation decoding,
shaders, scene conversion, FMOD decoding, native-method recovery or original
art integration. It does not claim faster production decoding or completion of
the game. `original_media_complete` and `godot_imported` remain false.

The changed code must be run against the real extracted DMG on a capable host.
Then use the per-bundle/object diagnostics to fix actual corpus failures and
complete the verified Arlen/Hank/kart dependency closure. Only real resource
import, rendering, audio and Android tests can advance those separate gates.

Publication is reported separately using the actual commit/push result and
remote readback; this report does not imply a successful GitHub push.
