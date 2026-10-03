# reverse-engineer-1

Reverse-engineering a macOS game from its `.dmg` and re-implementing it in
**Godot 4** for **Android**.

```
macOS .dmg ──► extract ──► Mach-O / asset analysis ──► Godot 4 ──► Android APK
```

## ⚠️ Prerequisites

**This toolchain is required. Any development environment used for this project
must have it fully installed and verified before any analysis work begins** — a
partial install fails late and confusingly (a missing Android SDK only surfaces
after hours of porting).

```bash
bash scripts/install-re-tools.sh    # idempotent; needs root/sudo, ~12 GB disk
bash scripts/verify-re-tools.sh     # must report 0 failed
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
tools/ghidra-scripts/
  DecompileAll.java              headless post-script: decompile all functions
```

Decompiled output, rizin projects, extracted bundles and APKs are build artifacts
and are gitignored — regenerate them rather than committing them.