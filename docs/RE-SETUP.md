# Reverse Engineering Setup

Toolchain documentation for this repo. Everything here was installed and verified
on **Ubuntu 22.04.5 LTS (x86_64)**.

The project is: **extract a macOS game from a `.dmg`, understand its code and
assets, then re-implement it in Godot 4 and ship it as an Android APK.**

```
macOS .dmg ──► extract ──► Mach-O / asset analysis ──► Godot 4 ──► Android APK
   dmg2img      hp*           Ghidra, rizin, LIEF          │         Android SDK
   bsdtar       unzip         macholib, llvm-*              │         NDK, build-tools
```

---

## ⚠️ Prerequisites — read this first

> **Every tool listed in this document is a prerequisite for working on this
> project. Any development environment used for this reverse-engineering work
> must have this toolchain fully installed and verified before analysis begins.**
>
> Install and verify in one step each:
>
> ```bash
> bash scripts/install-re-tools.sh
> bash scripts/verify-re-tools.sh
> ```
>
> `verify-re-tools.sh` must report **0 failed** before you start. A partially
> installed toolchain fails confusingly much later — a missing Android SDK only
> surfaces at export time, after hours of porting work.

The pipeline has four hard dependencies that are easy to miss:

1. **A JDK, not a JRE.** Ghidra 12 compiles `.java` scripts at runtime. Without
   the `jdk-headless` package, headless decompilation silently produces nothing.
2. **Android SDK + NDK + build-tools + export templates.** Godot cannot produce an
   APK without all three, plus a debug keystore.
3. **`clang`-class build tools** (cmake/ninja/scons) for building GDExtension and
   native modules.
4. **~12 GB of free disk.** Export templates are ~2 GB installed and the NDK is
   ~2.4 GB.

---

## Install

```bash
bash scripts/install-re-tools.sh    # needs root/sudo; idempotent
bash scripts/verify-re-tools.sh     # must report 0 failed
```

---

## What's installed

### Extraction (DMG / HFS+ / pkg)

| Tool | Location | Purpose |
|---|---|---|
| `dmg2img` | apt (1.6.7) | DMG → raw HFS+ disk image. **Cannot read LZFSE DMGs** — see below |
| `hpmount`, `hpcopy`, `hpumount` | apt (`hfsplus`) | Mount/read HFS+ images. **Note the `hp*` prefix** — there is no bare `hfsplus` command. |
| `bsdtar` (libarchive) | apt | Archive extraction incl. `cpio`, `xar` readers |
| `7z` / `7za` | apt (`p7zip-full`) | Zip, gzip, xar |
| `unzip` | apt | Standard zip |

### Binary / code analysis

| Tool | Location | Purpose |
|---|---|---|
| **Ghidra 12.1.4** | `/opt/ghidra` | Primary decompiler. Headless for scripts, GUI for manual work. |
| **OpenJDK 21** | apt | Ghidra 12.x requires JDK 21+. |
| **rizin 0.9.1** | `/opt/rizin` | Fast disassembly, scripting, binary diffing. |
| **LIEF 1.0** | venv | Mach-O/PE/ELF parsing, library introspection. |
| **macholib** | venv | Pure-Python Mach-O parsing (dylibs, load commands, fat binaries). |
| **llvm-objdump / llvm-readobj / llvm-nm** | apt (LLVM 14) | GNU binutils has poor Mach-O support; LLVM handles x86_64 + aarch64 Mach-O correctly. |
| **capstone** | venv | Disassembly from Python |
| **z3-solver** | venv | Constraint solving for key checks / serial validation |
| **construct** | venv | Declarative binary parsing for unknown formats |
| gdb-multiarch, ltrace, strace, qemu-user-static | apt | Dynamic analysis; run ARM/MIPS binaries on x86-64 |
| `pefile` | venv | PE parsing (for Unity IL2CPP side-by-side) |

### Engine / asset tooling

| Tool | Location | Purpose |
|---|---|---|
| **.NET SDK 8** | `/opt/dotnet` | Runs **AssetRipper** (Unity assets), **Il2CppDumper**, ILSpy. Cross-platform, so no Wine needed. |
| **Mono 6.8** + `monodis` | apt | Runs/decompiles MonoGame and other Mono/.NET game builds. |
| **Godot 4.7.2** | `/opt/godot` | Port target + Android export. |
| Godot export templates | `~/.local/share/godot/export_templates/4.7.2.stable` | **Required** to export; the editor alone cannot build an APK. |
| ffmpeg, ImageMagick | apt | Audio/video/texture conversion for imported assets |

### Android build

| Tool | Location | Purpose |
|---|---|---|
| Android SDK cmdline-tools | `/opt/android-sdk` | `sdkmanager`, `apkanalyzer`, `d8` |
| platform-tools (`adb`) | `/opt/android-sdk/platform-tools` | Device install/debug |
| platforms;android-35 | `/opt/android-sdk/platforms` | Compile against |
| build-tools;35.0.0 | `/opt/android-sdk/build-tools` | `aapt2`, `apksigner`, `zipalign` |
| ndk;29.0.14206865 | `/opt/android-sdk/ndk` | Native builds / GDExtension |
| cmake;3.22.1 | `/opt/android-sdk/cmake` | Godot's Android build requires it |
| debug keystore | `~/.android/debug.keystore` | Required to sign debug APKs |

---

## Workflow

### 1. Extract the DMG

```bash
mkdir -p ~/work/game && cd ~/work/game
cp /path/to/game.dmg .
dmg2img game.dmg game.img          # DMG -> raw HFS+ image
mkdir mnt && hpmount game.img mnt  # HFS+ image -> mountpoint
cp -a mnt/'Game.app' ./            # grab the bundle
hpumount mnt
```

If the DMG has a UDIF resource-fork wrapper that `dmg2img` can't fully unpack,
try `7z x game.dmg` first, or `binwalk game.dmg`.

> **Gotcha: `dmg2img` 1.6.7 cannot read LZFSE DMGs — and exits `0` anyway.**
> Modern DMGs compress with LZFSE. On those, `dmg2img` prints
> `Unsupported or corrupted block found`, writes a short mostly-zero file,
> and returns success, so a `&&` chain happily continues with garbage. Check
> the output size against the DMG, or use `tools/udif_extract.py`:
>
> ```bash
> /opt/re-tools/venv/bin/python tools/udif_extract.py --list game.dmg
> /opt/re-tools/venv/bin/python tools/udif_extract.py game.dmg game.img
> ```
>
> It parses the `koly`/`blkx`/`mish` tables itself. Two things it handles that
> naive parsers get wrong: all those integers are **big-endian**, and the
> descriptor table is *not* at a fixed offset inside the mish blob.
>
> **`hpmount` needs the `hfsplus` kernel module.** In containers without it,
> mount fails with `unknown filesystem type 'hfsplus'`; use 7-Zip ≥ 23.01
> (`7zz x game.dmg`), which reads both UDIF and HFS+ in userspace.

Inside a `.app` bundle, expect:

```
Game.app/Contents/
├── Info.plist           # bundle id, version, minimum macOS
├── MacOS/Game           # the Mach-O executable
├── Frameworks/          # dylibs (Unity, Mono, SDL, ...)
├── Resources/           # assets, Data/, *.bundle
└── _CodeSignature/      # code signature (ignore; we do not strip it)
```

### 2. Identify the engine — do this before anything else

```bash
file Game.app/Contents/MacOS/Game
strings Game.app/Contents/MacOS/Game | grep -iE 'unity|unreal|godot|monogame|gamemaker|cocos|sdl'
ls Game.app/Contents/Frameworks/
```

This determines your whole toolchain. See **Engine-specific notes** below.

### 3. Analyze

```bash
# Headless decompile of the main executable
/opt/ghidra/support/analyzeHeadless /tmp/ghidra-proj gameproj \
  -import Game.app/Contents/MacOS/Game \
  -scriptPath tools/ghidra-scripts \
  -postScript DecompileAll.java /tmp/game.c nolibs

# Mach-O specifics GNU binutils handles poorly
llvm-objdump -d --macho Game.app/Contents/MacOS/Game | less
lief.parse('Game.app/Contents/MacOS/Game').libraries      # in the venv
```

> **Gotcha: `analyzeHeadless` exits 0 even when the `-postScript` fails to
> compile.** Check the log for `ERROR` / `SCRIPT ERROR` and assert the output
> file is non-empty:
>
> ```bash
> grep -iE 'error|SCRIPT ERROR' ghidra.log
> test -s /tmp/game.c || echo "decompile produced nothing"
> ```

### 4. Port to Godot and export

```bash
godot --headless --path ~/work/port --import
godot --headless --path ~/work/port --export-debug "Android" build/game.apk
```

Godot project settings required for Android export:

```ini
[rendering]
textures/vram_compression/import_etc2_astc=true
```

Godot editor settings (`~/.config/godot/editor_settings-*.tres`) are pre-configured
by the installer with `java_sdk_path`, `android_sdk_path` and the debug keystore.

---

## Engine-specific notes

**Custom / native C++** — hardest. Decompile the Mach-O, then reimplement
behaviour in GDScript or a GDExtension. `tools/ghidra-scripts/DecompileAll.java`
plus rizin's `afl`/`pdf` are the workhorses.

**Unity (Mono)** — `Resources/unity default resources`, `Assembly-CSharp.dll`.
Use .NET tooling: `ilspycmd` or ILSpy for `Assembly-CSharp.dll`, which recovers
readable C# directly. Start there before touching the native binary.

**Unity (IL2CPP)** — the real difficulty. Code is compiled to C++, so you get
Mach-O plus a `global-metadata.dat`. Use **Il2CppDumper** (via the installed
.NET SDK) to recover type/method names, then feed that to Ghidra so the
decompilation reads like the original C#. **AssetRipper** recovers Unity assets.

**Unreal** — assets are in `.pak`/`.ucas`/`.utoc`; use **FModel** or **UModel**.
Gameplay is C++ in a monolithic binary; expect heavy Ghidra work.

**MonoGame / .NET** — easiest case. `monodis` or ILSpy on the game assembly
usually recovers most logic almost directly.

**Godot** — if the source game is already Godot, `PCK` files hold the assets and
GDScript can be decompiled with `gdsdecomp`. Porting is then mostly reassembly.

**Note on copy protection:** if the DMG is FairPlay-encrypted or the binary is
protected with a packer/DRM, this workflow does not apply and we won't circumvent
it. Packed-but-unencrypted binaries (`upx` is installed) are a different, tractable
problem.

---

## Python libraries

Isolated venv at `/opt/re-tools/venv` — never touches system Python, and fully
removable with `rm -rf /opt/re-tools/venv`.

```bash
/opt/re-tools/venv/bin/python -c "import lief; print(lief.__version__)"
```

---

## Environment variables

`/etc/profile.d/re-tools.sh` is sourced by login shells:

| Variable | Value |
|---|---|
| `GHIDRA_INSTALL_DIR` | `/opt/ghidra` |
| `RE_TOOLS_VENV` | `/opt/re-tools/venv` |
| `GODOT_BIN` | `/opt/godot/Godot_v4.7.2-stable_linux.x86_64` |
| `ANDROID_HOME` / `ANDROID_SDK_ROOT` | `/opt/android-sdk` |
| `ANDROID_NDK_HOME` | `/opt/android-sdk/ndk/29.0.14206865` |
| `DOTNET_ROOT` | `/opt/dotnet` |

Re-login or `source /etc/profile.d/re-tools.sh` after a fresh install.

---

## Runtime pinning

Versions are pinned at the top of `scripts/install-re-tools.sh`:

```bash
GHIDRA_VERSION="12.1.4"
RIZIN_VERSION="0.9.1"
GODOT_VERSION="4.7.2-stable"
ANDROID_CMDLINE_TOOLS="13114758"
ANDROID_PLATFORM="android-35"
ANDROID_BUILD_TOOLS="35.0.0"
ANDROID_NDK="29.0.14206865"
ANDROID_CMAKE="3.22.1"
DOTNET_CHANNEL="8.0"
```

Bump and re-run to upgrade. Re-run `verify-re-tools.sh` afterwards — Godot in
particular has bumped its JDK requirement across major versions.

---

## Repo layout

```
docs/RE-SETUP.md                 this file
scripts/install-re-tools.sh      idempotent toolchain installer
scripts/verify-re-tools.sh       smoke test: decompilation + real Android export
tools/udif_extract.py            UDIF/DMG → raw image; handles LZFSE DMGs
tools/ghidra-scripts/
  DecompileAll.java              headless post-script: decompile all functions
```

---

## Troubleshooting

**Ghidra decompiles nothing / `ClassNotFoundException`**
The `.java` script failed to compile — the real error is a few lines *above* that
message in the log. Godot/Ghidra 12 API notes: `Data` is in
`ghidra.program.model.listing` (not `.data`), `getReferencesFrom()` returns a
`Reference[]`, and `Symbol.getValue()` does not exist.

**`hfsplus: command not found`**
There is no bare `hfsplus` binary; use `hpmount` / `hpcopy` / `hpumount`.

**GNU `objdump` says `file format not recognized`**
That's expected for Mach-O. Use `llvm-objdump`.

**Godot: "A valid Java SDK path is required"**
`export/android/java_sdk_path` is empty in editor settings. The installer sets it;
check `~/.config/godot/editor_settings-*.tres`.

**Godot: "ETC2/ASTC texture compression is required"**
Add `textures/vram_compression/import_etc2_astc=true` under `[rendering]`.

**Godot: "Target folder does not exist"**
Godot won't create the export directory. `mkdir -p build` first.

**Export templates not found**
The directory must be named `<version>.stable` (e.g. `4.7.2.stable`) — *not* the
full `godot --version` string, which includes a build hash Godot doesn't use here.

**`.pkg` inside the DMG won't extract**
`xar` has no Linux package. `bsdtar` and `7z` both carry xar readers, but this is
unverified without a real macOS `.pkg`. If it fails, extract on a Mac and copy the
payload out.

**Out of memory during analysis**
Set `MAXMEM` in Ghidra's `support/launch.properties`, or bound a run with
`-analysisTimeoutPerFile <seconds>`.