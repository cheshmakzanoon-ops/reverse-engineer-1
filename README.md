# reverse-engineer-1

Reverse engineering workspace.

## Setup

Reverse-engineering work here needs **Ghidra** (which requires **JDK 21**), plus
rizin and a set of RE-oriented binutils and Python libraries. Install everything
with one command:

```bash
bash scripts/install-re-tools.sh
```

Then confirm the toolchain works:

```bash
bash scripts/verify-re-tools.sh
```

Full documentation, version pins, and headless-Ghidra usage:
**[docs/RE-SETUP.md](docs/RE-SETUP.md)**

## Decompile a binary to C

```bash
/opt/ghidra/support/analyzeHeadless /tmp/ghidra-proj myproj \
  -import ./target.bin \
  -scriptPath tools/ghidra-scripts \
  -postScript DecompileAll.java /tmp/target.c nolibs
```

Note that `analyzeHeadless` exits `0` even when the post-script fails to compile —
check the log for `ERROR` and confirm the output file is non-empty.

## Layout

```
docs/RE-SETUP.md                 toolchain docs, troubleshooting, gotchas
scripts/install-re-tools.sh      idempotent toolchain installer
scripts/verify-re-tools.sh       smoke test incl. a real decompilation
tools/ghidra-scripts/
  DecompileAll.java              headless post-script: decompile all functions
```

Decompiled output, rizin projects and extracted firmware are build artifacts and
are gitignored — regenerate them rather than committing them.