#!/usr/bin/env python3
"""Carve a single architecture out of a universal ("fat") Mach-O binary.

Every binary in a modern .app bundle is a FAT_MAGIC container holding both an
x86_64 and an arm64 slice.  Analysis tools disagree about fat binaries:

  * Ghidra imports fat Mach-O, but reports the container rather than a program,
    and its auto-analysis runs twice.
  * Il2CppDumper expects one image and its RVA arithmetic assumes a single
    __TEXT segment starting at 0.
  * rizin picks the first slice by default with no way to say "arm64, please".

Both slices are also ~2x the disk of one slice, and a decompilation run writes
output proportional to the input.  Carving first makes every downstream step
cheaper and more predictable.

The fat header is trivial to parse directly, so this avoids depending on the
Mach-O bindings in LIEF/macholib, whose APIs differ between major versions.

Usage:
    macho_slice.py <input> <output> [--arch arm64|x86_64] [--list]

Exit codes:
    0 success, 1 usage/format error, 2 architecture not present
"""

from __future__ import annotations

import argparse
import struct
import sys

FAT_MAGIC = 0xCAFEBABE  # big-endian marker; stored as the bytes CA FE BA BE
FAT_MAGIC_64 = 0xCAFEBABF  # 64-bit offsets (same first three bytes + 0xBF)
FAT_CIGAM = 0xBEBAFECA  # byte-swapped (little-endian) marker

CPU_ARCH_ABI64 = 0x01000000
CPU_TYPE_X86_64 = CPU_ARCH_ABI64 | 7
CPU_TYPE_ARM64 = CPU_ARCH_ABI64 | 12

CPU_NAMES = {
    CPU_TYPE_X86_64: "x86_64",
    CPU_TYPE_ARM64: "arm64",
    7: "i386",
    12: "arm",
}

FAT_ARCH = struct.Struct(">iiIII")  # cputype, cpusubtype, offset, size, align
FAT_ARCH_64 = struct.Struct(">iiQQII")


def _u32(buf: int, off: int) -> int:
    return struct.unpack_from(">I", buf, off)[0]


def read_fat_header(data: bytes):
    """Return (magic, [(cputype, cpusubtype, offset, size, align), ...])."""
    if len(data) < 8:
        raise ValueError("file is too small to contain a fat header")
    magic = _u32(data, 0)
    nfat = _u32(data, 4)
    if magic == FAT_MAGIC or magic == FAT_MAGIC_64:
        order, entry = ">", (FAT_ARCH_64 if magic == FAT_MAGIC_64 else FAT_ARCH)
    elif magic == FAT_CIGAM:
        order, entry = "<", FAT_ARCH
    else:
        raise ValueError(
            "not a universal binary (magic 0x%08x); nothing to slice" % magic
        )

    table, pos = [], 8
    for _ in range(nfat):
        if pos + entry.size > len(data):
            raise ValueError("fat header claims %d slices but the table is truncated" % nfat)
        if order == ">":
            cputype, cpusubtype, offset, size, align = entry.unpack_from(data, pos)
        else:
            cputype, cpusubtype, offset, size, align = entry.unpack_from(data, pos)
            offset &= 0xFFFFFFFF
        table.append((cputype & 0xFFFFFFFF, cpusubtype, offset, size, align))
        pos += entry.size
    return magic, table


def arch_name(cputype: int) -> str:
    return CPU_NAMES.get(cputype, "cpu_0x%08x" % cputype)


def slice_path(path: str) -> str:
    head, _, tail = path.rpartition("/")
    return (head + "/" if head else "") + tail + ".arm64"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Carve one arch out of a fat Mach-O.")
    ap.add_argument("input")
    ap.add_argument("output", nargs="?")
    ap.add_argument(
        "--arch",
        default="arm64",
        choices=["arm64", "x86_64"],
        help="which slice to keep (default: arm64)",
    )
    ap.add_argument("--list", action="store_true", help="list slices and exit")
    args = ap.parse_args(argv)

    with open(args.input, "rb") as fh:
        data = fh.read()

    try:
        magic, table = read_fat_header(data)
    except ValueError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1

    width = "64-bit" if magic == FAT_MAGIC_64 else "32-bit"
    if args.list:
        print("%s: fat %s, %d slice(s)" % (args.input, width, len(table)))
        for cputype, cpusubtype, offset, size, align in table:
            print(
                "  %-8s subtype=0x%08x offset=0x%08x size=%9d (%.1f MB) align=2^%d"
                % (arch_name(cputype), cpusubtype, offset, size, size / 1048576.0, align)
            )
        return 0

    if not args.output:
        print("error: output path required unless --list is given", file=sys.stderr)
        return 1

    want = CPU_TYPE_ARM64 if args.arch == "arm64" else CPU_TYPE_X86_64
    for cputype, cpusubtype, offset, size, _align in table:
        if cputype == want:
            blob = data[offset : offset + size]
            if len(blob) != size:
                print(
                    "error: slice claims %d bytes but only %d are present"
                    % (size, len(blob)),
                    file=sys.stderr,
                )
                return 1
            with open(args.output, "wb") as out:
                out.write(blob)
            print(
                "wrote %s  (%s, %d bytes, %.1f MB)"
                % (args.output, args.arch, size, size / 1048576.0)
            )
            return 0

    print(
        "error: %s not present (have: %s)"
        % (args.arch, ", ".join(arch_name(t[0]) for t in table)),
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())