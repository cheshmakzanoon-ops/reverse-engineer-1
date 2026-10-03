# Reverse Engineering Setup

Toolchain documentation for this repo. Everything here was installed and verified
on **Ubuntu 22.04.5 LTS (x86_64)**.

## TL;DR

```bash
bash scripts/install-re-tools.sh    # ~2 GB disk, needs root/sudo
bash scripts/verify-re-tools.sh     # proves it works end to end
```

## What gets installed

| Tool | Location | Why |
|---|---|---|
| **Ghidra 12.1.4** | `/opt/ghidra` (symlink) → `/opt/ghidra_12.1.4_PUBLIC` | The decompiler. Headless (`analyzeHeadless`) for CI/scripts, GUI for manual work. |
| **OpenJDK 21** | apt (`openjdk-21-jdk-headless`) | Ghidra 12.x **requires** JDK 21+. Not optional, not `default-jre`. |
| **rizin 0.9.1** | `/opt/rizin/bin` → `/usr/local/bin` | Fast interactive disassembly, scripting, binary diffing. Static build from GitHub releases (not in apt). |
| **file, xxd** | apt | Format identification and hex inspection. |
| **binwalk** | apt (2.3.3) | Firmware/container extraction. |
| **patchelf, upx-ucl** | apt | ELF surgery and packing/unpacking. |
| **gdb, gdb-multiarch** | apt (12.1) | Dynamic analysis and debugging, multi-arch aware. |
| **qemu-user-static** | apt | Emulate ARM/MIPS binaries on x86-64 for dynamic analysis. |
| **ltrace, strace** | apt | Library and syscall tracing. |
| **binutils** (`objdump`, `readelf`, `nm`, `strings`) | apt | Baseline ELF/Mach-O/PE inspection. |
| **Python RE libs** | `/opt/re-tools/venv/bin/python` | `capstone` (disasm), `pefile` (PE), `lief` (multi-format parsing), `r2pipe` (drive rizin from Python), `pwntools`. |

### Why a venv

Python RE libraries live in an isolated venv at `/opt/re-tools/venv`, **not** in
system site-packages. This keeps the sandbox's Python intact and makes the set of
libraries explicit and removable:

```bash
rm -rf /opt/re-tools/venv   # full Python-side uninstall
```

Activate it with `source /opt/re-tools/bin/activate`-style PATH entry — the installer
writes `/etc/profile.d/re-tools.sh` exporting `GHIDRA_INSTALL_DIR`, `RE_TOOLS_VENV`,
and the venv's `PATH`. Re-login or `source /etc/profile.d/re-tools.sh`.

## Running Ghidra headless

`analyzeHeadless <project_dir> <project_name> -import <binary> -postScript <script>`

Decompile every function to C, skipping thunks/externals:

```bash
/opt/ghidra/support/analyzeHeadless /tmp/ghidra-proj myproj \
  -import ./target.bin \
  -scriptPath tools/ghidra-scripts \
  -postScript DecompileAll.java /tmp/target.c nolibs
```

- First arg pair is a **project location + name**, not a filename. Ghidra stores
  analysis state there; use a fresh dir per target or pass `-deleteProject`.
- The `nolibs` argument to `DecompileAll.java` skips thunks and external symbols.
  Drop it to include everything.
- The GUI (`/opt/ghidra/ghidraRun`) is available when you want interactive work.
  In a headless container use `support/analyzeHeadless`.

### The `DecompileAll.java` script

`tools/ghidra-scripts/DecompileAll.java` decompiles every function to one C-like
file and inlines the string literals each function references — usually the fastest
way to identify what an unknown function does.

> ### Gotcha: exit code 0 does not mean it worked
>
> `analyzeHeadless` **returns 0 even when the `-postScript` fails to compile.**
> Grep the output for `ERROR` / `SCRIPT ERROR`, and assert on the generated file:
>
> ```bash
> grep -iE 'error|SCRIPT ERROR' ghidra.log
> test -s /tmp/target.c || echo "decompile produced nothing"
> ```
>
> `scripts/verify-re-tools.sh` exists precisely because of this — it judges success
> by checking the decompiled output, not the exit status.

Java scripts are compiled on demand at runtime, so an API typo surfaces as a
`ClassNotFoundException` at runtime, not at install. The compile error is in the
log just above it — read upward.

## Quick triage commands

```bash
file ./target.bin                       # what format/arch is this?
readelf -h ./target.bin                 # ELF header
objdump -d ./target.bin | less          # full disassembly
nm -C ./target.bin | grep -i check      # find a symbol
strings -n 6 ./target.bin | less        # printable strings
rizin -qc 'aaa; afl' ./target.bin       # rizin: analyse and list functions
rizin -qc 'aaa; pdf @main' ./target.bin # rizin: print main
binwalk ./firmware.bin                  # scan / extract firmware
patchelf --print-rpath ./target.bin     # inspect ELF runtime paths
```

## Runtime pinning

The installer pins versions at the top of `scripts/install-re-tools.sh`:

```bash
GHIDRA_VERSION="12.1.4"
GHIDRA_BUILD="20260921"
RIZIN_VERSION="0.9.1"
```

Bump these and re-run to upgrade. Verify Ghidra still runs afterwards — major
versions have bumped the JDK requirement (Ghidra 10 → JDK 17, Ghidra 11+ → JDK 21).

## Repo layout

```
docs/RE-SETUP.md                 this file
scripts/install-re-tools.sh      idempotent toolchain installer
scripts/verify-re-tools.sh       smoke test, incl. a real Ghidra decompilation
tools/ghidra-scripts/
  DecompileAll.java              headless post-script: decompile all functions
```

Analysis outputs (decompiled sources, rizin projects, extracted firmware) are
build artifacts, not source. They are gitignored — regenerate them with the
commands above rather than committing them.

## Troubleshooting

**`Unable to determine JAVA_HOME` / Ghidra won't launch**
Ghidra 12 needs JDK 21. Check `java -version` and that `openjdk-21-jdk-headless`
is installed; the `jdk-headless` package is required (not just a JRE) because
Ghidra compiles `.java` scripts at runtime.

**Out of memory during analysis**
Set `MAXMEM` in `support/launch.properties` (Ghidra reads it), or constrain a
run with `-analysisTimeoutPerFile <seconds>`.

**Ghidra says the file format is unsupported**
The loader is missing. `file` and `readelf` first: plain ELF/PE/Mach-O are always
supported. Firmware blobs often need binwalk to unpack into a recognisable
container before Ghidra will load them.

**`ClassNotFoundException` after editing a `.java` script**
It failed to compile. The real error is a few lines above it in the log.
Ghidra 12's API notes: `Data` is in `ghidra.program.model.listing` (not
`.data`), `SymbolTable.getReferencesFrom()` returns a `Reference[]` (not an
iterator), and `Symbol.getValue()` does not exist.