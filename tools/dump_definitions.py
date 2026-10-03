#!/usr/bin/env python3
"""Dump EVERY ScriptableObject in the game's definition bundles to JSON.

`unity_defs_to_gdscript.py` exports one named profile of one class. The game's
whole data model -- karts, characters, tracks, boosts, pickups, stats, league
tiers, mode rules -- lives in the same 349 KB `definitions_*` bundle as one
`Atlas.Definitions.*` / `Atlas.Gameplay.*` ScriptableObject per concept. This
tool extracts all of it at once so the port has the complete game database
rather than one handling profile.

MonoBehaviours are anonymous without the MonoScripts bundle (a MonoBehaviour
stores a PPtr to its MonoScript, and only that bundle maps the PPtr back to a
class name), so it is loaded alongside.

PPtr fields (`{"m_FileID": 0, "m_PathID": 1234}`) are the interesting part:
most definitions point at other definitions and at meshes/materials/textures.
They are rewritten here as `{"ref": "<class>:<name>", ...}` so a kart's stats,
its handling profile and its wheel meshes are all visible in one JSON file
instead of as opaque integers.

Usage:
    dump_definitions.py <definitions.bundle> <out_dir> \\
        [--monoscripts <bundle>] [--also <bundle> ...] [--pretty]

Writes:
    <out_dir>/definitions.json      everything, one file
    <out_dir>/by_class/<Class>.json one file per ScriptableObject class
    <out_dir>/index.json            class -> [ids], and the shared name table
"""

from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import re
import sys

# From Resources/Data/globalgamemanagers. The AssetBundles have their Unity
# version string stripped by the IL2CPP build, so UnityPy has to be told.
FALLBACK_UNITY_VERSION = "2021.3.56f2"


def load_unitypy():
    """Import UnityPy on demand so --help works without the dependency."""
    try:
        import UnityPy
        import UnityPy.config
    except ImportError:
        sys.exit("missing UnityPy. Install with: "
                 "/opt/re-tools/venv/bin/pip install UnityPy")
    UnityPy.config.FALLBACK_UNITY_VERSION = FALLBACK_UNITY_VERSION
    return UnityPy


def unwrap(v):
    """UnityPy wraps scalars as {"m_Value": x} or {"Value": x} -- flatten.

    Unity's typetree names the wrapper `m_Value`, but fixed-size string fields
    such as `_id` (`FixedString64Bytes`) come back keyed `Value`. Both are
    single-key wrapper dicts carrying nothing else, so both are safe to
    flatten without losing structure.
    """
    if isinstance(v, dict) and len(v) == 1:
        k = next(iter(v))
        if k in ("m_Value", "Value"):
            return v[k]
    return v


def is_pptr(v) -> bool:
    return isinstance(v, dict) and set(v.keys()) >= {"m_FileID", "m_PathID"}


def resolve_tree(val, names):
    """Recursively rewrite PPtrs into readable refs.

    `names` maps (fileID, pathID) -> "Class:name" built from every asset in
    every bundle passed in, so a reference into another bundle still resolves.
    """
    val = unwrap(val)
    if is_pptr(val):
        key = (int(val["m_FileID"]), int(val["m_PathID"]))
        out = {"ref": names.get(key, "?"), "path": key[1]}
        # A pointer with an inline sub-object (rare but legal in Unity
        # serialization) carries its own data -- keep it.
        extra = {k: resolve_tree(x, names) for k, x in val.items()
                 if k not in ("m_FileID", "m_PathID")}
        if extra:
            out["inline"] = extra
        return out
    if isinstance(val, dict):
        return {k: resolve_tree(x, names) for k, x in val.items()}
    if isinstance(val, list):
        return [resolve_tree(x, names) for x in val]
    if isinstance(val, (bytes, bytearray)):
        return "<%d bytes>" % len(val)
    return val


def read_name(o, names, scripts):
    """Best-effort display name for an asset object."""
    try:
        if o.type.name == "MonoBehaviour":
            m = o.read()
            sp = getattr(m, "m_Script", None)
            cls = scripts.get(getattr(sp, "path_id", -1), "MonoBehaviour")
            tt = o.read_typetree()
            for k in ("_id", "_name", "m_Name", "_displayName"):
                v = tt.get(k)
                v = unwrap(v)
                if isinstance(v, str) and v:
                    return v, cls
            return "(unnamed)", cls
        d = o.read()
        for k in ("m_Name", "name"):
            v = getattr(d, k, None)
            if isinstance(v, str) and v:
                return v, o.type.name
    except Exception:
        pass
    return None, o.type.name


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("bundle", help="the definitions_* bundle")
    ap.add_argument("out_dir")
    ap.add_argument("--monoscripts")
    ap.add_argument("--also", action="append", default=[],
                    help="extra bundle to index names from (repeatable)")
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args(argv)

    UnityPy = load_unitypy()

    monos = args.monoscripts
    if not monos:
        hits = glob.glob(os.path.join(os.path.dirname(args.bundle),
                                      "*monoscripts*.bundle"))
        if not hits:
            sys.exit("no MonoScripts bundle given and none found next to it")
        monos = hits[0]

    # Phase 1: index every object in every bundle so PPtrs can be named.
    bundles = [monos, args.bundle] + args.also
    print("indexing %d bundle(s)..." % len(bundles))
    names = {}
    counts = collections.Counter()
    for bi, path in enumerate(bundles):
        try:
            env = UnityPy.load(path)
        except Exception as e:                      # noqa: BLE001
            print("  ! %s: %s" % (os.path.basename(path), e))
            continue
        scripts = {}
        for o in env.objects:
            if o.type.name == "MonoScript":
                try:
                    d = o.read()
                    scripts[o.path_id] = "%s.%s" % (d.m_Namespace, d.m_ClassName)
                except Exception:                  # noqa: BLE001
                    pass
        for o in env.objects:
            counts[o.type.name] += 1
            if o.type.name in ("MonoScript", "TextAsset", "Texture2D",
                               "StreamingInfo"):
                continue
            nm, cls = read_name(o, names, scripts)
            if nm:
                names[(bi, o.path_id)] = "%s:%s" % (cls, nm)
        del env
    print("  indexed %d objects, %d named" % (sum(counts.values()), len(names)))
    print("  types: %s" % ", ".join("%s=%d" % kv for kv in counts.most_common(12)))

    # Phase 2: dump every MonoBehaviour with a resolved class name.
    env = UnityPy.load(monos, args.bundle)
    scripts = {}
    for o in env.objects:
        if o.type.name == "MonoScript":
            try:
                d = o.read()
                scripts[o.path_id] = "%s.%s" % (d.m_Namespace, d.m_ClassName)
            except Exception:                      # noqa: BLE001
                pass

    by_class = collections.defaultdict(list)
    anon = 0
    for o in env.objects:
        if o.type.name != "MonoBehaviour":
            continue
        try:
            m = o.read()
            sp = getattr(m, "m_Script", None)
            cls = scripts.get(getattr(sp, "path_id", -1))
        except Exception:                          # noqa: BLE001
            cls = None
        if not cls:
            anon += 1
            continue
        try:
            tt = o.read_typetree()
        except Exception as e:                      # noqa: BLE001
            print("  ! typetree failed for %s: %s" % (cls, e))
            continue
        data = resolve_tree(tt, names)
        by_class[cls].append({
            "path_id": o.path_id,
            "id": unwrap(tt.get("_id")) or unwrap(tt.get("_name")),
            "data": data,
        })

    if not by_class:
        sys.exit("no MonoBehaviours resolved -- is the MonoScripts bundle right?")

    out_dir = args.out_dir
    os.makedirs(os.path.join(out_dir, "by_class"), exist_ok=True)
    indent = 2 if args.pretty else None

    index = {"source_bundle": os.path.basename(args.bundle),
             "unity_version": FALLBACK_UNITY_VERSION,
             "anonymous_monobehaviours": anon,
             "classes": {}, "object_counts": dict(counts)}
    total = 0
    for cls, objs in sorted(by_class.items()):
        short = cls.split(".")[-1]
        safe = re.sub(r"[^A-Za-z0-9_.]", "_", short)
        index["classes"][cls] = [o["id"] for o in objs]
        # One file per object, named by its _id -- these are the things the
        # port addresses by id at runtime (KartPhysicsHandlingDefault, etc).
        for o in objs:
            fname = "%s.json" % re.sub(r"[^A-Za-z0-9_.-]", "_", str(o["id"] or o["path_id"]))
            with open(os.path.join(out_dir, "by_class", "%s__%s" % (safe, fname)), "w") as fh:
                json.dump({"class": cls, "id": o["id"], "data": o["data"]},
                          fh, indent=indent, default=str)
        total += len(objs)

    with open(os.path.join(out_dir, "definitions.json"), "w") as fh:
        json.dump({"source_bundle": index["source_bundle"],
                   "unity_version": FALLBACK_UNITY_VERSION,
                   "classes": {c: [o for o in v] for c, v in by_class.items()}},
                  fh, indent=indent, default=str)
    with open(os.path.join(out_dir, "index.json"), "w") as fh:
        json.dump(index, fh, indent=indent, default=str)
    # The shared name table makes references resolvable in any later dump.
    with open(os.path.join(out_dir, "name_table.json"), "w") as fh:
        json.dump({"%d:%d" % k: v for k, v in names.items()}, fh, indent=None)

    print("wrote %d ScriptableObjects across %d classes -> %s"
          % (total, len(by_class), out_dir))
    print("  (%d anonymous MonoBehaviours skipped)" % anon)
    for cls, objs in sorted(by_class.items(), key=lambda kv: -len(kv[1]))[:40]:
        print("  %-58s %4d" % (cls, len(objs)))
    return 0


if __name__ == "__main__":
    sys.exit(main())