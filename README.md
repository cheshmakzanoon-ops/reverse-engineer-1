# reverse-engineer-1

Reverse-engineering a macOS game from its `.dmg` and re-implementing it in
**Godot 4** for **Android**.

```
macOS .dmg ──► extract ──► Mach-O / asset analysis ──► Godot 4 ──► Android APK
```

## Current status

Target is **Warped Kart Racers v2.02** (`games/`), a Unity 2021.3 IL2CPP build.

| Stage | State |
|---|---|
| DMG extracted, bundle characterized | done |
| IL2CPP metadata recovered (`dump.cs`, 101 stub DLLs, 12,405 types) | done |
| C# source tree generated — 8,038 files / 866,535 lines | done |
| Kart handling model recovered (93 tuning fields, exact offsets) | done |
| Real tuning values extracted from the game's ScriptableObjects | done — 78 scalars + 5 curves |
| Core kart physics decompiled in Ghidra with real C# names | done (22 methods) |
| Godot 4 port + signed Android APK | done — `port/`, 55 MB, arm64-v8a + x86_64 |
| Art/audio assets, touch controls, other 5 handling profiles | **not done** |

### What "recovered source" means here

IL2CPP ships no C# — every method is compiled to ARM64 in `GameAssembly.dylib`.
What exists is the game's *API surface*, rebuilt as real `.cs` files:

```bash
bash tools/il2cpp_to_csharp.sh          # DummyDll -> work/analysis/csharp
```

That gives every class, inheritance chain, field name and offset, method
signature, enum and attribute — **including the developers' own `[Tooltip]` and
`[Header]` comments**. Method *bodies* are empty; those need Ghidra, and only
65 of 13,592 game methods have been decompiled.

So this is a complete specification rather than compilable source. That is the
right artifact for a port: rewrite bodies in GDScript from the signature, the
recovered data values, and targeted decompilation of the algorithms you need.

**Structure and the primary tuning values are both recovered.** The remaining
port-side numbers (`steer_speed`, `gliding_speed`, `top_speed`,
`top_acceleration`) are marked `# PORT-SIDE`: the original either folds them
into native code or never serializes them.

```bash
# prove the recovered values survived the trip into Godot
cd port && godot --headless --path . --script res://tests/test_recovered_tuning.gd
# PASS: 34 checks
```

Full write-up, including every recovered name and offset and an honest
limitations list: **[docs/RE-FINDINGS.md](docs/RE-FINDINGS.md)**.
Port specifics and build commands: **[port/README.md](port/README.md)**.

## ⚠️ Prerequisites

**This toolchain is required. Any development environment used for this project
must have it fully installed and verified before any analysis work begins** — a
partial install fails late and confusingly (a missing Android SDK only surfaces
after hours of porting).

```bash
bash scripts/install-re-tools.sh    # idempotent; needs root/sudo, ~12 GB disk
bash scripts/verify-re-tools.sh     # must report 0 failed
bash scripts/check-disk.sh          # free space + what can be reclaimed
```

`verify-re-tools.sh` does more than check binaries exist: it runs a real headless
Ghidra decompilation **and** a real Godot → Android APK export, asserting on the
produced artifacts.

Full documentation, workflow, and troubleshooting:
**[docs/RE-SETUP.md](docs/RE-SETUP.md)**

## What you get

- **Ghidra 12** + JDK 21 — decompilation (`analyzeHeadless` and GUI)
- **rizin**, LLVM binutils, LIEF, macholib, capstone, z3 — binary analysis
- **dmg2img**, `hpmount`, `bsdtar`, `7z` — DMG/HFS+/pkg extraction
- **.NET SDK** + **Mono** — AssetRipper, Il2CppDumper, ILSpy for Unity/.NET games
- **Godot 4.7** + export templates — the port target
- **Android SDK**, NDK, build-tools, CMake, debug keystore — APK export

## Quick start

```bash
# 1. Extract
dmg2img game.dmg game.img && mkdir mnt && hpmount game.img mnt

# Modern (LZFSE) DMGs: dmg2img 1.6.7 cannot decode them and still exits 0.
# Use tools/udif_extract.py instead — see docs/RE-SETUP.md.

# 2. Identify the engine — do this before anything else
strings mnt/'Game.app'/Contents/MacOS/Game | grep -iE 'unity|unreal|godot|monogame'

# 3. Decompile
/opt/ghidra/support/analyzeHeadless /tmp/ghidra-proj gameproj \
  -import mnt/'Game.app'/Contents/MacOS/Game \
  -scriptPath tools/ghidra-scripts \
  -postScript DecompileAll.java /tmp/game.c nolibs

# 4. Port and export
godot --headless --path ~/work/port --import
godot --headless --path ~/work/port --export-debug "Android" build/game.apk
```

Note that `analyzeHeadless` exits `0` even when the post-script fails to compile —
check the log for `ERROR` and confirm the output file is non-empty.

## Layout

```
docs/RE-SETUP.md                 toolchain docs, workflow, troubleshooting
scripts/install-re-tools.sh      idempotent toolchain installer
scripts/verify-re-tools.sh       smoke test incl. live decompile + APK export
scripts/check-disk.sh            free-space guard; non-zero when too tight
tools/udif_extract.py            UDIF/DMG → raw image; handles LZFSE DMGs
tools/ghidra-scripts/
  DecompileAll.java              headless post-script: decompile all functions
  DecompileIl2Cpp.java           decompile only game assemblies, with IL2CPP names
tools/macho_slice.py             carve one arch out of a universal (fat) Mach-O
tools/il2cpp_triage.py           query dump.cs: summary/ns/asm/type/find
tools/unity_defs_to_gdscript.py  recover ScriptableObject values from a bundle
tools/il2cpp_to_csharp.sh       rebuild the C# source tree from the stub DLLs
port/                            Godot 4 project + Android export preset
```

Decompiled output, rizin projects, extracted bundles and APKs are build artifacts
and are gitignored — regenerate them rather than committing them.