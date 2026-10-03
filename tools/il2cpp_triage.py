#!/usr/bin/env python3
"""Triage an Il2CppDumper `dump.cs` into a navigable map of the game.

`dump.cs` is a flat, 800k-line file of recovered C# *declarations* -- every
class, method signature, field and RVA, but no method bodies (those live as
native ARM64 in GameAssembly.dylib).  It is the skeleton of the original
project, and finding anything in it by grep means fighting one flat namespace.

This parses it once into a structured index and answers questions about it:

  * which namespaces hold the actual game code (vs. Unity + BCL)
  * how many types, methods and fields each one contributes
  * what a given type's fields, methods and RVA offsets are
  * where gameplay concepts live, by name pattern

Note on assembly attribution: dump.cs lists every `// Image N: <asm>` in one
block at the top, so the file does not record which assembly each later type
came from.  Assembly-level queries therefore read DummyDll/*.dll metadata
instead (`asm --dlls <dir>`), rather than guessing here.

Usage:
    il2cpp_triage.py summary  <dump.cs>
    il2cpp_triage.py ns      <dump.cs> [--min-types 5]
    il2cpp_triage.py asm     --dlls <DummyDll-dir>
    il2cpp_triage.py type    <dump.cs> <TypeName>
    il2cpp_triage.py find    <dump.cs> <regex> [--kind class|method|field]
"""

from __future__ import annotations

import argparse
import collections
import os
import re
import subprocess
import sys

# `// Image 12: Newtonsoft.Json.dll - 8423`
RE_IMAGE = re.compile(r"^// Image (\d+): (.+?) - (\d+)\s*$")
# `// Namespace: Atlas.Karts` -- the global namespace prints a bare
# "// Namespace:" with nothing after it, so the space must be optional.
RE_NS = re.compile(r"^// Namespace:\s?(.*)$")
# trailing `// TypeDefIndex: 2628` appended to every type line
RE_TYPEDEF = re.compile(r"\s*//\s*TypeDefIndex:\s*\d+\s*$")
# `\t// RVA: 0x1077790 Offset: 0x1077790 VA: 0x1077790`
RE_RVA = re.compile(r"^//\s*RVA:\s*(0x[0-9A-Fa-f]+)\s+Offset:\s*(0x[0-9A-Fa-f]+)")
# `public class KartController : MonoBehaviour`
RE_TYPE = re.compile(
    r"^(?P<mods>(?:public|internal|private|protected|sealed|abstract|static|"
    r"partial|readonly|ref|unsafe|extern|new)\s+)*"
    r"(?P<kind>class|struct|enum|interface|delegate)\s+"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
    r"(?:\s*<[^>]*>)?"
    r"\s*(?::\s*(?P<bases>.*?))?\s*$"
)
# `public void Boost(float amount) { }`  /  `public void .ctor() { }`
RE_METHOD = re.compile(
    r"^(?P<attrs>(?:public|private|protected|internal|static|abstract|virtual|"
    r"override|extern|unsafe|new|sealed|readonly|const|partial)\s+)*"
    r"(?P<ret>[\w\.<>\[\],\?]+)\s+"
    r"(?P<name>\.?[A-Za-z_][A-Za-z0-9_]*)\s*"
    r"\((?P<args>[^)]*)\)\s*(?:\{.*\}|;)?\s*$"
)
# `private WibbleBehaviour _wibbleBehaviour; // 0x20`
# The trailing `; // 0x20` offset comment comes AFTER the semicolon.
RE_FIELD = re.compile(
    r"^(?P<attrs>(?:public|private|protected|internal|static|readonly|const|"
    r"volatile|abstract|virtual|override|new)\s+)*"
    r"(?P<type>[\w\.<>\[\],\?\s]+?)\s+"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*"
    r"(?P<init>=\s*[^;]*?)?\s*;?\s*"
    r"(?P<off>//\s*0x[0-9A-Fa-f]+)?\s*$"
)
# enum member, bare (`Left = 0,`) or typed
# (`public const BaseVisualBehaviour.Side Left = 0;`)
RE_ENUM_MEMBER = re.compile(
    r"^(?:(?:public|internal)\s+)?(?:const\s+)?"
    r"(?:[A-Za-z_][A-Za-z0-9_.]*\s+)?"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<value>[^,;]+?)\s*[,;]?$"
)
RE_ATTR = re.compile(r"^\[\w")
RE_SECTION = re.compile(r"^//\s*(Fields|Properties|Methods|Nested types|"
                        r"Static Fields|Static Properties|Events)\s*$")
# `12: KartVisualSettingsDefinition (flist=14, mlist=8, ...)` from monodis
RE_MONODIS_TYPEDEF = re.compile(r"^\s*\d+:\s")

KEYWORDS = {"if", "for", "while", "switch", "return", "new", "get", "set",
            "typeof", "sizeof", "checked", "unchecked", "lock", "using",
            "throw", "await", "base", "this", "in", "out", "ref", "value"}


class Method:
    __slots__ = ("name", "ret", "args", "attrs", "rva", "offset")

    def __init__(self, name, ret, args, attrs=""):
        self.name, self.ret, self.args, self.attrs = name, ret, args, attrs
        self.rva = None
        self.offset = None

    @property
    def sig(self):
        return "%s %s(%s)" % (self.ret, self.name, self.args)


class Field:
    __slots__ = ("name", "type", "attrs", "const", "offset")

    def __init__(self, name, ftype, attrs="", const=None, offset=None):
        self.name, self.type, self.attrs = name, ftype, attrs
        self.const, self.offset = const, offset


class Type:
    __slots__ = ("name", "kind", "namespace", "bases", "attrs",
                 "rva", "offset", "methods", "fields", "members")

    def __init__(self, name, kind, namespace, bases, attrs):
        self.name, self.kind, self.namespace = name, kind, namespace
        self.bases, self.attrs = bases, attrs
        self.rva = self.offset = None
        self.methods, self.fields, self.members = [], [], {}

    @property
    def full(self):
        return "%s.%s" % (self.namespace, self.name) if self.namespace else self.name

    @property
    def size(self):
        return len(self.methods) + len(self.fields) + len(self.members)

    def __repr__(self):
        return "<Type %s %s rva=%s>" % (self.kind, self.full,
                                        hex(self.rva) if self.rva else None)


class Index:
    def __init__(self):
        self.types = []
        self.by_name = {}
        self.by_full = {}

    # -- construction ----------------------------------------------------

    @classmethod
    def from_dump(cls, path, progress=False):
        idx = cls()
        ns = ""
        cur = None
        in_enum = False
        pending_rva = None

        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for lineno, raw in enumerate(fh):
                if progress and lineno and lineno % 250000 == 0:
                    sys.stderr.write("  ...%d lines\n" % lineno)
                    sys.stderr.flush()

                # dump.cs indents everything inside a type with tabs.
                s = raw.strip()

                if raw.startswith("// Image "):
                    continue
                if raw.startswith("// Namespace:"):
                    ns = RE_NS.match(raw.rstrip()).group(1)
                    cur, in_enum, pending_rva = None, False, None
                    continue

                # A type lays out as: signature line, '{', members, '}'.
                # Only '}' ends the current type -- the '{' follows the
                # signature we just consumed and must not clear it.
                if s == "{":
                    continue
                if s == "}":
                    cur, in_enum, pending_rva = None, False, None
                    continue

                if cur is None:
                    m = RE_TYPE.match(RE_TYPEDEF.sub("", s))
                    if m:
                        idx._add(Type(
                            m.group("name"), m.group("kind"), ns,
                            (m.group("bases") or "").strip(),
                            (m.group("mods") or "").strip()))
                        cur = idx.types[-1]
                        in_enum = cur.kind == "enum"
                    continue

                # ---------------- inside a type body ----------------
                if s.startswith("//"):
                    m = RE_RVA.match(s)
                    if m:
                        rva, off = int(m.group(1), 16), int(m.group(2), 16)
                        if cur.rva is None and not cur.methods:
                            cur.rva, cur.offset = rva, off
                        elif cur.methods:
                            cur.methods[-1].rva = rva
                            cur.methods[-1].offset = off
                        else:
                            pending_rva = (rva, off)
                    continue

                if RE_ATTR.match(s) or RE_SECTION.match(s) or not s:
                    continue

                if in_enum:
                    if s.startswith("public int value__"):
                        continue
                    m = RE_ENUM_MEMBER.match(s)
                    if m and m.group("name") not in KEYWORDS:
                        cur.members[m.group("name")] = m.group("value").strip()
                    continue

                m = RE_METHOD.match(s)
                if m and m.group("name").lstrip(".") not in KEYWORDS:
                    cur.methods.append(Method(
                        m.group("name"), m.group("ret"), m.group("args").strip(),
                        (m.group("attrs") or "").strip()))
                    if pending_rva:
                        cur.methods[-1].rva, cur.methods[-1].offset = pending_rva
                        pending_rva = None
                    continue

                m = RE_FIELD.match(s)
                if m and m.group("name") not in KEYWORDS:
                    off = int(m.group("off").split("0x")[1], 16) if m.group("off") else None
                    cur.fields.append(Field(
                        m.group("name"), m.group("type").strip(),
                        (m.group("attrs") or "").strip(),
                        (m.group("init") or "").strip() or None, off))

        return idx

    def _add(self, t):
        self.types.append(t)
        self.by_name.setdefault(t.name, []).append(t)
        self.by_full.setdefault(t.full, []).append(t)

    # -- queries ---------------------------------------------------------

    def find(self, pattern, kind=None):
        rx = re.compile(pattern, re.I)
        for t in self.types:
            if kind in (None, "class") and rx.search(t.name):
                yield t, "type", t.full
            if kind in (None, "method"):
                for me in t.methods:
                    if rx.search(me.name):
                        yield t, "method", "%s.%s()" % (t.full, me.name)
            if kind in (None, "field"):
                for f in t.fields:
                    if rx.search(f.name):
                        yield t, "field", "%s.%s" % (t.full, f.name)

    def game_types(self):
        """Non-Unity, non-BCL types -- the ported game's own code."""
        skip = re.compile(
            r"^(Unity|UnityEngine|System$|System\.|mscorlib|netstandard|Mono\.|"
            r"Microsoft\.|Newtonsoft|DOTween|DG\.|Cinemachine|FMOD|UniRx|"
            r"FlatBuffers|Autodesk|Il2Cpp|TMPro|RTLTMPro|Collecs|Properties\.|"
            r"MS\.|Internal)")
        return [t for t in self.types if not skip.match(t.namespace or "")]


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------

def bar(n, maxn, width=26):
    if maxn <= 0:
        return ""
    return "#" * max(1, int(round(width * n / maxn))) if n else ""


def cmd_summary(idx, args):
    game = idx.game_types()
    print("types total        : %d" % len(idx.types))
    print("game-code types    : %d  (non-Unity, non-BCL)" % len(game))
    print("  ...with RVA      : %d" % sum(1 for t in game if t.rva))
    print("  methods          : %d" % sum(len(t.methods) for t in game))
    print("  fields           : %d" % sum(len(t.fields) for t in game))
    print("  enum members     : %d" % sum(len(t.members) for t in game))
    print()
    cnt = collections.Counter(t.namespace for t in game)
    mx = cnt.most_common(1)[0][1] if cnt else 0
    print("Top 30 namespaces by type count:")
    for ns, n in cnt.most_common(30):
        print("  %-46s %5d %s" % (ns or "(global)", n, bar(n, mx)))


def cmd_ns(idx, args):
    game = idx.game_types()
    cnt = collections.Counter(t.namespace for t in game)
    rows = sorted(((n, c) for n, c in cnt.items() if c >= args.min_types),
                  key=lambda r: (-r[1], r[0]))
    mx = rows[0][1] if rows else 0
    for ns, n in rows:
        print("%-44s %5d %s" % (ns or "(global)", n, bar(n, mx)))


def count_typedefs(dll_path):
    """Count types in a .NET assembly via `monodis --typedef`.

    monodis prints one line per type as `12: Name (flist=..., ...)`, so the
    count is the number of numbered lines -- not `##########` separators.
    """
    out = subprocess.run(["monodis", "--typedef", dll_path],
                         capture_output=True, text=True)
    if out.returncode != 0:
        return 0
    return sum(1 for ln in out.stdout.splitlines() if RE_MONODIS_TYPEDEF.match(ln))


def cmd_asm(args):
    """Per-assembly type counts, read from the generated DummyDll metadata."""
    root = args.dlls
    if not os.path.isdir(root):
        print("not a directory: %s" % root, file=sys.stderr)
        return 1
    rows, total, failed = [], 0, 0
    for dll in sorted(os.listdir(root)):
        if not dll.lower().endswith(".dll"):
            continue
        n = count_typedefs(os.path.join(root, dll))
        if n == 0:
            failed += 1
        rows.append((n, dll))
        total += n
    rows.sort(reverse=True)
    mx = rows[0][0] if rows else 0
    for n, dll in rows:
        print("%-46s %6d %s" % (dll, n, bar(n, mx)))
    print("\n%d assemblies, %d types%s"
          % (len(rows), total,
             (", %d unreadable" % failed) if failed else ""))
    return 0


def cmd_type(idx, args):
    hits = idx.by_name.get(args.name) or idx.by_full.get(args.name)
    if not hits:
        print("no type named %r" % args.name, file=sys.stderr)
        return 1
    for t in hits:
        print("=" * 78)
        print("%s   [%s]" % (t.full, t.kind))
        if t.bases:
            print("  extends  : %s" % t.bases)
        if t.rva:
            print("  RVA      : 0x%X   offset 0x%X" % (t.rva, t.offset))
        if t.members:
            print("  -- %d enum members --" % len(t.members))
            for k, v in t.members.items():
                print("    %-40s = %s" % (k, v))
        if t.fields:
            print("  -- %d fields --" % len(t.fields))
            for f in t.fields:
                off = ("  +0x%X" % f.offset) if f.offset else ""
                cst = (" = %s" % f.const) if f.const else ""
                print("    %s%s %s %s%s" %
                      ((f.attrs + " ") if f.attrs else "", f.type, f.name, cst, off))
        if t.methods:
            print("  -- %d methods --" % len(t.methods))
            for me in t.methods:
                rva = ("0x%X" % me.rva) if me.rva else "-------"
                print("    %-10s %s   // %s" % (rva, me.sig, me.attrs))
        print()
    return 0


def cmd_find(idx, args):
    seen = 0
    for _t, kind, text in idx.find(args.pattern, args.kind):
        print("%-9s %s" % (kind, text))
        seen += 1
        if args.limit and seen >= args.limit:
            print("... (truncated at %d)" % args.limit)
            break
    print("\n%d match(es)" % seen)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("summary", help="overall stats")
    p.add_argument("dump")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(fn=cmd_summary)

    p = sub.add_parser("ns", help="namespaces by type count")
    p.add_argument("dump")
    p.add_argument("--min-types", type=int, default=5)
    p.set_defaults(fn=cmd_ns)

    p = sub.add_parser("asm", help="per-assembly counts from DummyDll metadata")
    p.add_argument("dump", nargs="?")
    p.add_argument("--dlls", required=True)
    p.set_defaults(fn=cmd_asm)

    p = sub.add_parser("type", help="dump one type")
    p.add_argument("dump")
    p.add_argument("name")
    p.set_defaults(fn=cmd_type)

    p = sub.add_parser("find", help="regex over type/method/field names")
    p.add_argument("dump")
    p.add_argument("pattern")
    p.add_argument("--kind", choices=["class", "method", "field"])
    p.add_argument("--limit", type=int, default=0)
    p.set_defaults(fn=cmd_find)

    args = ap.parse_args(argv)
    if args.cmd == "asm":
        return cmd_asm(args) or 0
    if getattr(args, "verbose", False):
        sys.stderr.write("parsing %s ...\n" % args.dump)
    idx = Index.from_dump(args.dump, progress=getattr(args, "verbose", False))
    return args.fn(idx, args) or 0


if __name__ == "__main__":
    sys.exit(main())