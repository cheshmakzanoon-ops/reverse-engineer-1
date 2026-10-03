#!/usr/bin/env python3
"""Extract a UDIF (.dmg) disk image to a raw disk image.

Why this exists: the packaged ``dmg2img`` on Ubuntu 22.04 is v1.6.7, which
predates LZFSE block support. On an LZFSE DMG it prints "Unsupported or
corrupted block found", writes a short mostly-zero file, and *still exits 0* --
so a scripted pipeline silently gets garbage. This reads the ``koly`` trailer
and the ``blkx``/mish tables directly and decompresses blocks with the
``lzfse`` module.

Usage:
    udif_extract.py game.dmg game.img
    udif_extract.py --list game.dmg

Exits non-zero on a malformed trailer or an unsupported block type, so it is
safe to chain with ``&&``.

Format notes (verified against Warped.Kart.Racers.v2.02.dmg):

* All ``koly`` and ``mish`` integers are **big-endian** ("network byte order").
* ``koly`` headerSize is 512; xmlOffset/xmlLength live at 216/224.
* A ``blkx`` entry's ``Data`` is a raw ``mish`` blob. Its descriptor table is
  located by searching for the offset at which the descriptors both fill the
  blob exactly (40 bytes each) and sum to the mish header's sectorCount --
  rather than assuming a fixed header size.
* mish descriptor (40 bytes, big-endian)::

      uint32 type            0x00000001 raw, 0x00000002 zero/ignore,
                             0x80000005 zlib, 0x80000007 LZFSE
      uint32 reserved
      uint64 outSector       sector offset relative to mish firstSector
      uint64 sectorCount     number of 512-byte sectors
      uint64 dataOffset      byte offset into the DMG data fork
      uint64 dataLength      compressed length in the data fork

  The decompressed size of a descriptor is ``sectorCount * 512``; the
  compressed stream for a whole descriptor is a single LZFSE frame.
"""

import plistlib
import struct
import sys

try:
    import lzfse
except ImportError:  # pragma: no cover
    sys.exit("udif_extract.py: missing 'lzfse' module. Install with: "
             "/opt/re-tools/venv/bin/pip install lzfse")

SECTOR = 512
MISH_SIG = b"mish"
MISH_MIN_HEADER = 72
DESC_SIZE = 40
ENDIAN = ">"

UDIF_ZERO = 0x0
UDIF_RAW = 0x1
UDIF_IGNORE = 0x2
UDIF_ADC = 0x4
UDIF_ZLIB = 0x5
UDIF_LZVN = 0x6
UDIF_LZFSE = 0x7
UDIF_LZO = 0x8

# The descriptor's 32-bit type has the compressed bit (0x80000000) and some
# padding bits set; the low nibble is the codec. Mask to the codec ID so the
# comparison is independent of which flags the writer happened to set.
TYPE_MASK = 0x0000000F

TYPE_NAMES = {
    UDIF_ZERO: "zero-fill",
    UDIF_RAW: "raw",
    UDIF_IGNORE: "ignore",
    UDIF_ADC: "ADC",
    UDIF_ZLIB: "zlib",
    UDIF_LZVN: "LZVN",
    UDIF_LZFSE: "LZFSE",
    UDIF_LZO: "LZO",
}


def die(msg):
    sys.exit("udif_extract.py: %s" % msg)


def read_koly(f, size):
    """Locate and parse the trailing koly block, auto-detecting endianness."""
    f.seek(-512, 2)
    raw = f.read(512)
    if raw[:4] != b"koly":
        die("missing 'koly' trailer - download looks truncated")
    # Apple writes UDIF scalars in network byte order, but some tools emit
    # little-endian. Pick whichever yields a self-consistent header.
    for endian in (">", "<"):
        header_size, = struct.unpack_from(endian + "I", raw, 8)
        xml_off, xml_len = struct.unpack_from(endian + "QQ", raw, 216)
        if header_size != 512:
            continue
        if 0 < xml_off and 0 < xml_len and xml_off + xml_len <= size:
            f.seek(0, 2)
            return endian, xml_off, xml_len
    die("could not interpret the koly header (tried big- and little-endian)")


def read_plist(f, xml_off, xml_len):
    f.seek(xml_off)
    try:
        return plistlib.loads(f.read(xml_len))
    except Exception as e:
        die("cannot parse DMG property list at %d: %s" % (xml_off, e))


def read_blkx(plist):
    rsrc = plist.get("resource-fork")
    if not rsrc:
        die("no 'resource-fork' in DMG property list")
    if isinstance(rsrc, (bytes, bytearray)):
        rsrc = plistlib.loads(bytes(rsrc))
    entries = rsrc.get("blkx")
    if not entries:
        die("no 'blkx' array in resource fork")
    out = []
    for entry in entries:
        mish = entry.get("Data")
        if not isinstance(mish, (bytes, bytearray)):
            die("blkx entry %r has no Data blob" % entry.get("Name"))
        out.append((entry, bytes(mish)))
    return out


def parse_mish(blob, endian=ENDIAN):
    """Return (first_sector, data_start, descriptors).

    ``descriptors`` is a list of (blk_type, out_sector, sector_count,
    data_offset, data_length). The descriptor table start is discovered rather
    than assumed: candidates are the offsets at which the 40-byte descriptors
    fill the blob exactly and their sector counts sum to the mish header's
    declared sectorCount. The table is searched from the *end* backwards,
    because the padding before the real table decodes as zero-sector
    zero-fill descriptors that do not disturb that sum -- the tightest
    (fewest-descriptor) candidate is the real one.
    """
    if blob[:4] != MISH_SIG:
        die("bad mish signature %r" % blob[:4])
    first_sector, sector_count, data_start = struct.unpack_from(
        endian + "QQQ", blob, 8)

    fallback = None
    for off in range(len(blob) - DESC_SIZE, MISH_MIN_HEADER - 1, -4):
        if (len(blob) - off) % DESC_SIZE:
            continue
        descs = []
        plausible = True
        for j in range((len(blob) - off) // DESC_SIZE):
            base = off + j * DESC_SIZE
            raw_type, = struct.unpack_from(endian + "I", blob, base)
            if raw_type == 0xFFFFFFFF:
                break  # end-of-table marker, not a real descriptor
            blk_type = raw_type & TYPE_MASK
            if blk_type not in TYPE_NAMES:
                plausible = False
                break
            out_sec, sec_cnt, data_off, data_len = struct.unpack_from(
                endian + "QQQQ", blob, base + 8)
            descs.append((blk_type, out_sec, sec_cnt, data_off, data_len))
        if not plausible:
            continue
        if sum(d[2] for d in descs) == sector_count:
            return first_sector, data_start, descs
        if fallback is None:
            fallback = descs
    if fallback is not None:
        return first_sector, data_start, fallback
    die("could not locate the mish descriptor table (%d bytes)" % len(blob))


def decompress(blk_type, raw, expect):
    if blk_type == UDIF_RAW:
        return raw
    if blk_type == UDIF_LZFSE:
        try:
            return lzfse.decompress(raw)
        except lzfse.error as e:
            die("LZFSE decompress failed: %s" % e)
    if blk_type == UDIF_ZLIB:
        import zlib
        return zlib.decompress(raw)
    die("unsupported block type 0x%08x (%s); this tool implements "
        "LZFSE/zlib/raw/zero"
        % (blk_type, TYPE_NAMES.get(blk_type, "?")))


def build_plan(blocks):
    """Flatten all blkx/mish descriptors into (out_off, fork_off, ...) tuples."""
    plan = []
    total = 0
    summary = []
    for entry, mish in blocks:
        first_sector, data_start, descs = parse_mish(mish)
        base = (first_sector * SECTOR) + data_start
        n_data = 0
        for blk_type, out_sec, sec_cnt, data_off, data_len in descs:
            out_len = sec_cnt * SECTOR
            out_off = base + out_sec * SECTOR
            plan.append((out_off, data_off, out_len, data_len, blk_type))
            if out_off + out_len > total:
                total = out_off + out_len
            if blk_type not in (UDIF_ZERO, UDIF_IGNORE):
                n_data += 1
        summary.append((entry, len(descs), n_data))
    return plan, total, summary


def main(argv):
    list_only = "--list" in argv[1:]
    args = [a for a in argv[1:] if not a.startswith("--")]
    if list_only and len(args) != 1:
        sys.exit(__doc__)
    if not list_only and len(args) != 2:
        sys.exit(__doc__)
    src = args[0]
    dst = None if list_only else args[1]

    with open(src, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        endian, xml_off, xml_len = read_koly(f, size)
        print("image : %s (%d bytes)" % (src, size))
        print("koly  : big-endian=%s xml=%d+%d" % (endian == ">", xml_off,
                                                   xml_len))
        blocks = read_blkx(read_plist(f, xml_off, xml_len))
        plan, total, summary = build_plan(blocks)

        print("output: %d bytes (%.2f GiB) in %d descriptors"
              % (total, total / (1 << 30), len(plan)))
        for entry, n_desc, n_data in summary:
            print("  %-34s descriptors=%-5d with-data=%d"
                  % ((entry.get("Name") or "?")[:34], n_desc, n_data))

        if list_only:
            return 0

        out = open(dst, "wb")
        out.truncate(total)
        written = 0
        try:
            for out_off, fork_off, out_len, data_len, blk_type in plan:
                if blk_type in (UDIF_ZERO, UDIF_IGNORE):
                    continue  # sparse: truncate() already zero-filled it
                f.seek(fork_off)
                raw = f.read(data_len)
                plain = decompress(blk_type, raw, out_len)
                if len(plain) > out_len:
                    plain = plain[:out_len]
                elif len(plain) < out_len:
                    plain += b"\0" * (out_len - len(plain))
                out.seek(out_off)
                out.write(plain)
                written += 1
                if written % 250 == 0:
                    print("  ... %d/%d" % (written, len(plan)), flush=True)
        finally:
            out.close()

    print("wrote %s: %d/%d data blocks, %d bytes"
          % (dst, written, len(plan), total))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))