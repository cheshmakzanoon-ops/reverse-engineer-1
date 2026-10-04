#!/usr/bin/env python3
"""Legacy route/inspection extraction; use --production for validated content.

Production: extract_assets.py SOURCE_DIRECTORY NEW_OUTPUT --production
The production path uses scoped references and raw typetrees; unsupported
features remain explicit gates. Legacy output below stays byte-compatible.

Extract Unity AssetBundle content into formats a Godot rebuild can use.

The game ships 221 AssetBundles (844 MB) that hold everything visual: meshes,
textures, sprites, animation clips, and -- most valuable for a port -- the
scene-level MonoBehaviours that ARE the game's track logic. A map bundle
contains `Atlas.Geometry.SplineBehaviour`, `MainSplineKnotBehaviour`,
`PickupSpotBehaviour`, `RespawnLocationsBehaviour`, `HitVolumeBehaviour` and
`VirtualCamera`: the racing line, where pickups spawn, where you respawn, and
where the camera looks. None of that is in any code file; it is data attached
to GameObjects in a scene.

What gets written, per bundle:

  scene.json      the full GameObject/Transform hierarchy (names, parents,
                  local TRS, active flags) plus every MonoBehaviour typetree
                  keyed by class name and GameObject -- so the scene can be
                  rebuilt in any engine, not just inspected
  meshes/         .obj per mesh, materials as .mtl
  textures/       .png per Texture2D and Sprite

Meshes are written as OBJ rather than via UnityPy's own exporter because these
are Unity-skinned and LOD meshes whose exporter paths vary between releases;
a direct vertex/index/normal/uv dump is predictable and diffable.

Usage:
    extract_assets.py <bundle> <out_dir> [--monoscripts B] [--modes scene,meshes,textures]
    extract_assets.py --many "<glob>" <out_dir> [...]      # batch
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import struct
import sys

FALLBACK_UNITY_VERSION = "2021.3.56f2"


def load_unitypy():
    try:
        import UnityPy
        import UnityPy.config
    except ImportError:
        sys.exit("missing UnityPy. Install with: "
                 "/opt/re-tools/venv/bin/pip install UnityPy")
    UnityPy.config.FALLBACK_UNITY_VERSION = FALLBACK_UNITY_VERSION
    return UnityPy


def find(monoscripts, near):
    if monoscripts:
        return monoscripts
    hits = glob.glob(os.path.join(near or ".", "*monoscripts*.bundle"))
    if not hits:
        sys.exit("no MonoScripts bundle given and none found next to the input")
    return hits[0]


def load_scripts(path):
    """pathID -> 'Namespace.ClassName'. Without this every MonoBehaviour is
    anonymous, because it stores only a PPtr to its MonoScript."""
    UnityPy = load_unitypy()
    scripts = {}
    env = UnityPy.load(path)
    for o in env.objects:
        if o.type.name == "MonoScript":
            try:
                d = o.read()
                scripts[o.path_id] = "%s.%s" % (d.m_Namespace, d.m_ClassName)
            except Exception:                      # noqa: BLE001
                pass
    return scripts


def v3(t, k):
    x = t.get(k)
    if isinstance(x, dict) and "x" in x:
        return (x.get("x", 0.0), x.get("y", 0.0), x.get("z", 0.0))
    if isinstance(x, dict) and "m_X" in x:
        return (x.get("m_X", 0.0), x.get("m_Y", 0.0), x.get("m_Z", 0.0))
    if isinstance(x, (list, tuple)) and len(x) == 3:
        return (x[0], x[1], x[2])
    return (0.0, 0.0, 0.0)


def v4(t, k):
    x = t.get(k)
    if isinstance(x, dict) and "x" in x:
        return (x.get("x", 0.0), x.get("y", 0.0), x.get("z", 0.0), x.get("w", 0.0))
    if isinstance(x, dict) and "m_X" in x:
        return (x.get("m_X", 0.0), x.get("m_Y", 0.0), x.get("m_Z", 0.0),
                x.get("m_W", 1.0))
    if isinstance(x, (list, tuple)) and len(x) == 4:
        return tuple(x)
    return (0.0, 0.0, 0.0, 0.0)


def clean(v, ctx=None):
    """Make a typetree JSON-safe: unwrap scalars, shrink PPtrs and byte blobs.

    PPtr path IDs are rewritten through `ctx` when given, and this is not
    cosmetic. A bundle holds SEVERAL SerializedFiles -- a single map bundle
    here contains 16 -- each with its own path-ID space, so raw `m_PathID`
    values collide across them. Resolving globally (first file wins) silently
    mis-points cross-file references: Arlen Suburbs' racing-line root came back
    as GameObject 0 and its entire spline vanished. `ctx` is
    (by_file, current_file) so a `m_FileID: 0` pointer -- which means "in the
    same serialized file as this object" -- resolves in the right space.
    """
    v = unwrap(v)
    if isinstance(v, dict):
        if set(v.keys()) <= {"m_FileID", "m_PathID"}:
            raw = int(v.get("m_PathID", 0))
            if ctx is None:
                return raw
            by_file, cur = ctx
            return by_file.get(cur, {}).get(raw, raw)
        return {k: clean(x, ctx) for k, x in v.items()}
    if isinstance(v, list):
        return [clean(x, ctx) for x in v]
    if isinstance(v, (bytes, bytearray)):
        return None
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    return str(v)


def build_id_map(env):
    """Stable sequential uid per (serialized file, path id).

    Sequential because the consumer only needs an unambiguous key, and because
    it keeps scene.json diffable: the same object always gets the same number
    regardless of iteration order.

    Returns (uid_of_pair, path_by_file, collisions).
    """
    uid_of_pair = {}
    for n, o in enumerate(sorted(env.objects,
                                 key=lambda x: (x.assets_file.name, x.path_id))):
        uid_of_pair[(o.assets_file.name, o.path_id)] = n + 1
    path_by_file = {}
    for (fname, pid), uid in uid_of_pair.items():
        path_by_file.setdefault(fname, {})[pid] = uid
    # How many path IDs are shared between files. Recorded rather than assumed
    # away, because it is the reason the naive single-space map is wrong.
    seen = {}
    collisions = 0
    for fname, pids in path_by_file.items():
        for pid in pids:
            if pid in seen:
                collisions += 1
            seen[pid] = fname
    return uid_of_pair, path_by_file, collisions


def unwrap(v):
    if isinstance(v, dict) and len(v) == 1 and next(iter(v)) in ("m_Value", "Value"):
        return v[next(iter(v))]
    return v


def write_obj(obj, out_path, name):
    """Dump a Unity Mesh as Wavefront OBJ (+ MTL).

    Delegates to UnityPy's own exporter. This game's meshes do NOT store a
    `m_Vertices` array at all -- Unity 2018+ packs positions, normals and UVs
    into `m_VertexData` streams whose layout depends on `m_Channels`, so
    reading the obvious field yields zero vertices and a silently empty file.
    MeshHandler is what decodes that layout, so it is used rather than
    reimplemented.
    """
    try:
        from UnityPy.export.MeshExporter import export_mesh_obj
        mesh = obj.read()
        text = export_mesh_obj(mesh)
        if not text:
            return 0, 0
        with open(out_path, "w") as fh:
            fh.write("# Unity mesh %s from %s\n"
                     % (name, os.path.basename(obj.assets_file.name)))
            fh.write(text)
            fh.write("\nmtllib %s.mtl\n" % name)
        with open(out_path + ".mtl", "w") as fh:
            fh.write("newmtl %s\nKd 0.8 0.8 0.8\nKs 0.1 0.1 0.1\nd 1.0\n"
                     "illum 2\n" % name)
        nv = sum(1 for ln in text.splitlines() if ln.startswith("v "))
        nf = sum(1 for ln in text.splitlines() if ln.startswith("f "))
        return nv, nf
    except Exception as e:                          # noqa: BLE001
        return -1, str(e)


def extract_scene(env, scripts, out_path, bundle_name):
    """Full hierarchy + every MonoBehaviour typetree, keyed by class."""
    uid_of_pair, path_by_file, collisions = build_id_map(env)

    def uid(tt, key):
        return int(tt.get(key, {}).get("m_PathID", 0))

    def ures(uid_value, cur_file):
        return path_by_file.get(cur_file, {}).get(int(uid_value), 0)

    gos, transforms = {}, {}
    for o in env.objects:
        f = o.assets_file.name
        key = uid_of_pair.get((f, o.path_id))
        if o.type.name == "GameObject":
            try:
                tt = o.read_typetree()
            except Exception:                      # noqa: BLE001
                continue
            gos[key] = {
                "name": tt.get("m_Name", ""),
                "active": tt.get("m_IsActive", True),
                "layer": tt.get("m_Layer", 0),
                "tag": tt.get("m_TagString", ""),
                "components": [ures(c.get("component", {}).get("m_PathID", 0), f)
                               for c in (tt.get("m_Component") or [])],
            }
        elif o.type.name in ("Transform", "RectTransform"):
            try:
                tt = o.read_typetree()
            except Exception:                      # noqa: BLE001
                continue
            transforms[key] = {
                "go": ures(uid(tt, "m_GameObject"), f),
                "parent": ures(uid(tt, "m_Father"), f),
                "children": [ures(c.get("m_PathID", 0), f)
                             for c in (tt.get("m_Children") or [])],
                "pos": v3(tt, "m_LocalPosition"),
                "rot": v4(tt, "m_LocalRotation"),
                "scale": v3(tt, "m_LocalScale"),
                "type": o.type.name,
            }

    # MonoBehaviours, grouped by resolved class then by owning GameObject --
    # that is the shape a scene rebuild needs, and it is the only place track
    # spline/pickup/respawn data exists at all.
    behaviours = {}
    unresolved = 0
    for o in env.objects:
        if o.type.name != "MonoBehaviour":
            continue
        try:
            m = o.read()
            sp = getattr(m, "m_Script", None)
            cls = scripts.get(getattr(sp, "path_id", -1), "MonoBehaviour")
            tt = o.read_typetree()
        except Exception:                          # noqa: BLE001
            unresolved += 1
            continue
        if cls == "MonoBehaviour":
            unresolved += 1
        f = o.assets_file.name
        go = ures(uid(tt, "m_GameObject"), f)
        behaviours.setdefault(cls, []).append({
            "uid": uid_of_pair.get((f, o.path_id), 0),
            "game_object": go,
            "game_object_name": gos.get(go, {}).get("name", ""),
            "data": clean({k: v for k, v in tt.items()
                           if k not in ("m_GameObject", "m_Script", "m_Enabled")},
                          (path_by_file, f)),
        })

    # Mesh references with the name of the GameObject that carries them.
    meshes = []
    for o in env.objects:
        if o.type.name != "MeshFilter":
            continue
        try:
            tt = o.read_typetree()
        except Exception:                          # noqa: BLE001
            continue
        f = o.assets_file.name
        meshes.append({
            "uid": uid_of_pair.get((f, o.path_id), 0),
            "mesh": ures(uid(tt, "m_Mesh"), f),
            "game_object": ures(uid(tt, "m_GameObject"), f),
        })

    doc = {
        "bundle": bundle_name,
        "counts": {
            "serialized_files": len(path_by_file),
            "path_id_collisions": collisions,
            "game_objects": len(gos), "transforms": len(transforms),
            "monobehaviours": sum(len(v) for v in behaviours.values()),
            "unresolved_monobehaviours": unresolved,
            "meshes": len(meshes),
        },
        "game_objects": gos,
        "transforms": transforms,
        "behaviours": behaviours,
        "mesh_filters": meshes,
    }
    with open(out_path, "w") as fh:
        json.dump(doc, fh, default=str)
    return doc


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("bundle")
    ap.add_argument("out_dir")
    ap.add_argument("--monoscripts")
    ap.add_argument("--production", action="store_true",
                    help="use the strict inventory pipeline on an extracted source directory")
    ap.add_argument("--many", action="store_true",
                    help="treat `bundle` as a glob and process every match")
    ap.add_argument("--modes", default="scene,meshes,textures")
    ap.add_argument("--category", default="",
                    help="subdirectory under out_dir for this bundle")
    args = ap.parse_args(argv)

    if args.production:
        if args.many or args.monoscripts or args.category or args.modes != "scene,meshes,textures":
            ap.error("--production uses SOURCE_DIRECTORY NEW_OUTPUT without legacy export options")
        try:
            from .recover_content import main as content_main
        except ImportError:
            from recover_content import main as content_main
        return content_main(["inventory", args.bundle, args.out_dir])

    UnityPy = load_unitypy()
    modes = set(m.strip() for m in args.modes.split(",") if m.strip())

    bundles = (sorted(glob.glob(args.bundle)) if args.many
               else [args.bundle])
    bundles = [b for b in bundles if ":com.apple.provenance" not in b]
    if not bundles:
        sys.exit("no bundles matched %s" % args.bundle)

    scripts = load_scripts(find(args.monoscripts, os.path.dirname(bundles[0])))

    for path in bundles:
        base = os.path.basename(path)
        if base.endswith(".bundle"):
            base = base[:-len(".bundle")]
        out = os.path.join(args.out_dir, args.category, base) if args.category \
            else os.path.join(args.out_dir, base)
        os.makedirs(out, exist_ok=True)

        env = UnityPy.load(path)
        summary = {"bundle": base}

        if "scene" in modes:
            doc = extract_scene(env, scripts, os.path.join(out, "scene.json"), base)
            summary.update(doc["counts"])

        if "meshes" in modes:
            mdir = os.path.join(out, "meshes")
            os.makedirs(mdir, exist_ok=True)
            nv = nf = 0
            for o in env.objects:
                if o.type.name != "Mesh":
                    continue
                try:
                    m = o.read()
                    name = m.m_Name or ("mesh_%d" % o.path_id)
                except Exception:                  # noqa: BLE001
                    continue
                v, f = write_obj(o, os.path.join(mdir, "%s.obj" % name), name)
                if isinstance(f, str):
                    continue
                nv += max(v, 0)
                nf += f
            summary["mesh_vertices"] = nv
            summary["mesh_triangles"] = nf

        if "textures" in modes:
            tdir = os.path.join(out, "textures")
            os.makedirs(tdir, exist_ok=True)
            n = 0
            for o in env.objects:
                if o.type.name not in ("Texture2D", "Sprite"):
                    continue
                try:
                    d = o.read()
                    img = d.image
                    name = (getattr(d, "m_Name", "") or
                            "%s_%d" % (o.type.name.lower(), o.path_id))
                    img.save(os.path.join(tdir, "%s.png" % name))
                    n += 1
                except Exception:                  # noqa: BLE001
                    continue
            summary["textures"] = n

        print("%-58s %s" % (base[:58],
                            " ".join("%s=%s" % kv for kv in summary.items()
                                     if kv[0] != "bundle")))
    return 0


if __name__ == "__main__":
    sys.exit(main())