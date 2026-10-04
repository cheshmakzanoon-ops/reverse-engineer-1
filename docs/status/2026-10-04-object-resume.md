# Object-level continuation and encrypted checkpoints — 2026-10-04

Base: `5d90f21041c3594672a6e86ecacf9a776227de6b`, single `main`.
The preceding key-only commit enabled the existing encrypted source transfer.
This increment implements actual within-bundle continuation and portable private
checkpoints. It does not import original production content into the game.

## Real source is now present in the development runtime

The owner-provided Drive file was obtained through private-source workflow
`37228246821`. All six encrypted ZIP digests were checked, the matching private
recipient key decrypted the parts locally, and the reconstructed DMG matched:

- Size: 1,190,250,225 bytes.
- SHA-256: `4720e9fbaabd6d39bceac362e271e24e9c08d8f549191f70bad34e6d5c28b6be`.

The existing 7-Zip extraction path produced 276 application files totaling
1,596,079,924 bytes and 222 Unity bundles. The original universal Mach-O
`GameAssembly.dylib` and version-31 IL2CPP metadata were also hashed. These are
analysis inputs, not recovered original C# implementations. The original DMG
was not modified. No original pixels, mesh data, fonts, audio or key material
belongs in the public verification package.

## Production interruption and recovery evidence

Target: `map_racesmithsdreamworld_scenes_all_55c2ac0e72c14e09480332ee0d5ca96b.bundle`.
Bundle size: 39,196,611 bytes; SHA-256:
`e254fe6859817590b136eddd4000adf36d2085e5a547e359f440d8bb49fb1c35`.
This is the bundle at which the earlier hosted inventory exhausted a worker budget.

The first local decoder process was deliberately killed with SIGKILL after eight
seconds. Its SQLite journal retained 4,575 committed objects. A fresh real UnityPy
process reused those 4,575 objects, processed 6,989 additional objects, and finished
with 11,564 object records in 58.20 seconds. These are measured local results, not
mobile performance or a guaranteed decode-time budget.

Three object errors remain explicitly recorded: non-finite typetree values in one
MonoBehaviour (path ID 10, named Animation Track) and two ParticleSystems (path IDs
3109 and 3113). Their raw records remain preserved. The converter's finite-value
rules were not relaxed and these objects were not labelled successfully converted.
UnityPy used the repository's configured Unity-version fallback for stripped
version information; that fallback is not independent proof of the original engine
version. Complete catalog/scene conversion still requires resolving these issues.

The finished bundle's private checkpoint was then encrypted, restored into a new
workspace with the same recipe/source manifest, and independently verified:

| Production checkpoint check | Actual result |
|---|---:|
| Source manifest bundle count | 222 |
| Bundles in this round-trip sample | 1 |
| Object records restored | 11,564 |
| Artifact files hash/size verified | 23,224 |
| Recorded object errors retained | 3 |
| Private tar bytes | 124,559,360 |
| Encrypted parts | 1 |

The restored records matched the original batch byte-for-byte. The status remains
`restored-not-catalog-verified`. This proves a real production checkpoint round trip,
not completion of all 222 bundles. Hosted cross-run restoration was not exercised;
the optional owner-controlled receiving-key secret has not been configured here.

## Implemented behavior

`ObjectJournal` validates deterministic object order and complete artifact digests
before reusing records. Committed outputs survive a worker kill; an unfinished
object is retried. Exclusive locks prevent competing workers. The stable private
partial directory is promoted to a bundle receipt only after decoding finishes.
Unbatched output remains byte-identical and the existing final reference gates
remain unchanged.

`content_checkpoints.py` seals eligible checkpoint files with the existing
RSA-OAEP/AES-GCM transport, rejects unsafe archive entries, restores create-only
workspaces, and checks source/recipe/receipts. The hosted source workflow exposes
explicit trusted-run restoration inputs, seals available work before cleanup, and
uploads only encrypted payloads. See [commands and operational limits](../OBJECT-CHECKPOINTS.md).

## Validation record

**176 Python tests and 483 Godot checks across 16 suites pass locally.** Project
import, a 360-frame frontend boot, both clean/combat three-lap simulations,
graphical touch/menu flow, and seven byte-identical generated modules pass.
Modified workflow YAML and embedded shell blocks parse. New coverage includes 15 object-journal tests, 13 encrypted-checkpoint tests and two source-runner
integration tests. An existing grandchild-heartbeat test initially failed because
its one-second budget expired before the grandchild started on a loaded host. The
fixture now establishes a real child-start handshake before testing the unchanged
process-group timeout/termination behavior; it still fails if the grandchild does
not start or survives termination. No failed test was suppressed or marked skipped.

Reproduction from the repository root:

```sh
GODOT_TEST_BIN=/path/to/godot python -m unittest discover -s tests -v
python scripts/checked_process.py --timeout 240 -- godot --headless --path port --import
python scripts/checked_process.py --timeout 120 -- godot --headless --path port --quit-after 360
GODOT=/path/to/godot bash scripts/test_godot.sh verification
```

## Remaining gates

All 222 bundles still need a complete retained inventory and cross-reference
finalization. The three known typetree errors require evidence-based handling;
Addressables bindings, real Arlen/Hank/kart scene assembly, production materials,
compressed/humanoid animation, FMOD sound events, complete content/modes and native
behavior comparison remain incomplete. This increment changes recovery tools and
CI, not runtime gameplay. No new local Android export, emulator or physical-phone
result is claimed. The previously produced engineering APK is not a full port.
