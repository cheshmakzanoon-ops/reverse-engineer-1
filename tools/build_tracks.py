#!/usr/bin/env python3
"""Compile extracted map scenes into compact, engine-usable track definitions.

`extract_assets.py` recovers each map's scene graph as raw JSON: every
GameObject, every Transform, every MonoBehaviour. That is complete but it is
not usable -- rebuilding a road means knowing, per track, where the control
points are in order, how wide the road is at each of them, where pickups
respawn, where you respawn when you fall off, and which surface each piece of
scenery is. All of that IS in the scene data, spread across a hierarchy and
cross-referenced by path ID. This resolves it.

The road geometry is fully determined by the recovered data:

  Atlas.Geometry.SplineBehaviour          _resolution, _curvature, _loop
      children = the ordered control points
  .MainSplineKnotBehaviour                _leftWidth / _rightWidth per point
      _branchOutKnots / _branchInKnots    alternate routes (shortcuts)

Position comes from the Transform chain walked to world space, so a spline
nested under offset parents still lands in the right place.

Everything else needed to make a track playable is extracted alongside:
pickup spots (with the box definition they roll), respawn locations, per-object
surface tags and their glide/boost triggers, camera FOV, and the map/minimap
bounds boxes.

Usage:
    build_tracks.py work/assets/scenes <out.json> [--gdscript out.gd]
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import os
import sys


def quat_to_matrix(q):
    """Unity (x, y, z, w) quaternion -> 3x3 rotation matrix, row-major."""
    x, y, z, w = q
    n = math.sqrt(x * x + y * y + z * z + w * w)
    if n == 0.0:
        return [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    x, y, z, w = x / n, y / n, z / n, w / n
    return [
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ]


IDENTITY = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]


def mmul(a, b):
    """3x3 matrix product."""
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)]
            for i in range(3)]


def mvec(m, v):
    return [sum(m[i][j] * v[j] for j in range(3)) for i in range(3)]


class Scene:
    def __init__(self, path):
        with open(path) as fh:
            self.doc = json.load(fh)
        self.gos = {int(k): v for k, v in self.doc["game_objects"].items()}
        self.xf = {int(k): v for k, v in self.doc["transforms"].items()}
        self._world = {}

    def world(self, uid):
        """World transform of a Transform, caching the walk up the hierarchy.

        Rotation is kept as a 3x3 matrix rather than a quaternion so parent and
        child compose by matrix product; re-quantising a composed matrix is
        lossy and was the source of a shape error here.
        """
        uid = int(uid)
        if uid in self._world:
            return self._world[uid]
        t = self.xf.get(uid)
        if t is None:
            return None
        pos = [float(t["pos"][0]), float(t["pos"][1]), float(t["pos"][2])]
        mat = quat_to_matrix([float(x) for x in t["rot"]])
        scale = [float(t["scale"][0]), float(t["scale"][1]), float(t["scale"][2])]
        parent = int(t["parent"] or 0)
        if parent and parent in self.xf:
            pw = self.world(parent)
            if pw:
                local = [pos[i] * scale[i] for i in range(3)]
                pos = [mvec(pw["mat"], local)[i] + pw["pos"][i] for i in range(3)]
                mat = mmul(pw["mat"], mat)
                scale = [pw["scale"][i] * scale[i] for i in range(3)]
        out = {"pos": pos, "mat": mat, "scale": scale}
        self._world[uid] = out
        return out

    def name_of(self, go_uid):
        return self.gos.get(int(go_uid or 0), {}).get("name", "")

    def transform_of_go(self, go_uid):
        """The Transform that belongs to a GameObject."""
        go_uid = int(go_uid or 0)
        go = self.gos.get(go_uid)
        if not go:
            return None
        for c in go.get("components", []):
            t = self.xf.get(int(c))
            if t and t.get("go") == go_uid:
                return int(c)
        return None


def ordered_children(scene, transform_uid, depth=0):
    """Depth-first walk of a Transform's children, preserving array order.

    Unity stores children in order and the game's splines rely on it, so this
    must not sort by name or path id.
    """
    if depth > 64 or not transform_uid:
        return []
    t = scene.xf.get(int(transform_uid))
    if not t:
        return []
    out = []
    for c in t["children"]:
        c = int(c)
        out.append(c)
        out.extend(ordered_children(scene, c, depth + 1))
    return out


def index_behaviours(scene):
    """(class, game_object_uid) -> behaviour data."""
    idx = {}
    for cls, entries in scene.doc["behaviours"].items():
        for e in entries:
            idx[(cls, int(e["game_object"]))] = e["data"]
    return idx


def index_behaviour_owners(scene):
    """behaviour uid -> GameObject uid.

    Needed because the map's root references (`_raceSplineRootBehaviour`,
    `_spawnLocationsBehaviour`, ...) point at *MonoBehaviours*, not at
    Transforms. Treating those uids as Transform ids silently yields nothing:
    the lookup misses and the whole spline comes back empty.
    """
    owner = {}
    for entries in scene.doc["behaviours"].values():
        for e in entries:
            owner[int(e["uid"])] = int(e["game_object"])
    return owner


# Spline knot behaviours are declared in the game's own assembly without a
# namespace, which is why they show up as ".MainSplineKnotBehaviour".
KNOT_MAIN = (".MainSplineKnotBehaviour", "MainSplineKnotBehaviour")
KNOT_PLAIN = (".KnotBehaviour", "KnotBehaviour")
SPLINE = "Atlas.Geometry.SplineBehaviour"
# The map's racing line. Its GameObject's CHILDREN are the alternative routes
# (shortcuts, alternate laps); each route's children are the ordered control
# points, each carrying a knot behaviour with the road width. The
# `Atlas.Geometry.SplineBehaviour` entries in the same bundle are decoration
# (ramps, loops) and are not the racing line.
SPLINE_ROOT = "Atlas.Gameplay.RaceSplineRootBehaviour"


def find_behaviour(idx, go_uid, names):
    for n in names:
        if (n, go_uid) in idx:
            return idx[(n, go_uid)]
    return None


def build_points(scene, idx, route_go):
    """Ordered control points under one route GameObject."""
    root = scene.transform_of_go(route_go)
    if root is None:
        return []
    pts = []
    # Direct children only. Each is one control point; descending further
    # would walk into prop hierarchies hanging off a knot and corrupt the line.
    for child in scene.xf.get(root, {}).get("children", []):
        t = scene.xf.get(int(child))
        if not t:
            continue
        go = int(t["go"])
        w = scene.world(int(child))
        if not w:
            continue
        knot = find_behaviour(idx, go, KNOT_MAIN) or find_behaviour(idx, go, KNOT_PLAIN)
        branches = []
        if knot:
            branches = [int(x) for x in (knot.get("_branchOutKnots") or []) if x]
        pts.append({
            "go": go,
            "name": scene.name_of(go),
            "pos": [round(v, 4) for v in w["pos"]],
            "rot": [round(v, 5) for row in w["mat"] for v in row],
            "left_width": round(float(knot.get("_leftWidth", 5.0)), 4) if knot else None,
            "right_width": round(float(knot.get("_rightWidth", 5.0)), 4) if knot else None,
            "branches": branches,
        })
    return pts


def bounds_of(scene, box_behaviour):
    """Centre + size of a Unity BoxCollider reference inside a typetree."""
    b = box_behaviour
    if not isinstance(b, dict):
        return None
    c = b.get("m_Center") or {}
    s = b.get("m_Size") or {}

    def triple(d):
        if isinstance(d, dict):
            return [round(float(d.get(k, 0.0)), 4) for k in ("x", "y", "z")]
        if isinstance(d, (list, tuple)) and len(d) == 3:
            return [round(float(x), 4) for x in d]
        return [0.0, 0.0, 0.0]

    return {"center": triple(c), "size": triple(s)}


def build_track(scene_path):
    scene = Scene(scene_path)
    idx = index_behaviours(scene)
    owner = index_behaviour_owners(scene)
    base = os.path.basename(os.path.dirname(scene_path))

    map_entries = []
    for cls in ("Atlas.Maps.MapBehaviour", "MapBehaviour"):
        map_entries += scene.doc["behaviours"].get(cls, [])
    if not map_entries:
        return None

    me = map_entries[0]["data"]
    spline_root_behaviour = int(me.get("_raceSplineRootBehaviour") or 0)
    spline_go = owner.get(spline_root_behaviour, 0)

    # Each child of RaceSplineRoot is one route variant; the longest is the
    # main line and the rest are shortcuts or alternate laps, chosen at runtime
    # from `_raceSplineProbabilityConfig`.
    root_behaviour = None
    for e in scene.doc["behaviours"].get(SPLINE_ROOT, []):
        if int(e["game_object"]) == int(spline_go):
            root_behaviour = e["data"]
            break

    route_config = (root_behaviour or {}).get("_raceSplineProbabilityConfig")
    root_tf = scene.transform_of_go(spline_go)
    splines = []
    for child in (scene.xf.get(root_tf, {}).get("children", []) if root_tf else []):
        t = scene.xf.get(int(child))
        if not t:
            continue
        pts = build_points(scene, idx, int(t["go"]))
        if not pts:
            continue
        # A race track's main spline is a closed loop, but the recovered last
        # point is NOT welded to the first -- Arlen Speedway's two ends sit
        # 46 units apart, which is one ordinary control-point gap at this
        # spacing. Testing for exact coincidence therefore reports every track
        # as open and drops the closing segment from the lap length. Judge it
        # against the track's own point spacing instead.
        loop = False
        if len(pts) >= 3:
            gap = math.dist(pts[0]["pos"], pts[-1]["pos"])
            mean_gap = sum(math.dist(pts[i]["pos"], pts[i + 1]["pos"])
                           for i in range(len(pts) - 1)) / (len(pts) - 1)
            loop = gap <= max(2.0 * mean_gap, 1.0)
        splines.append({
            "name": scene.name_of(int(t["go"])),
            "loop": loop,
            "point_count": len(pts),
            "points": pts,
        })
    splines.sort(key=lambda s: -s["point_count"])

    # Pickup spots: each names the PickupBox definition it rolls from.
    pickups = []
    for cls, entries in scene.doc["behaviours"].items():
        if "PickupSpotBehaviour" not in cls:
            continue
        for e in entries:
            d = e["data"]
            tf = scene.transform_of_go(e["game_object"])
            w = scene.world(tf) if tf else None
            pickups.append({
                "name": scene.name_of(e["game_object"]),
                "pos": [round(v, 4) for v in w["pos"]] if w else [0.0, 0.0, 0.0],
                "box": d.get("_pickupBoxDefinition", ""),
                "respawn_time": round(float(d.get("_boxRespawnTime", 1.0)), 4),
                "spawn_height": round(float(d.get("_spawnHeight", 0.0)), 4),
            })

    # Respawn locations. `_respawnLocations` holds TRANSFORM ids (verified: they
    # equal the RespawnLocations GameObject's own child transforms), not
    # GameObject ids -- resolving them as GameObjects silently collapses all 696
    # of Arlen Speedway's respawn points onto one position.
    respawns = []
    for cls, entries in scene.doc["behaviours"].items():
        if "RespawnLocationsBehaviour" not in cls:
            continue
        for e in entries:
            for uid in (e["data"].get("_respawnLocations") or []):
                uid = int(uid)
                w = scene.world(uid)
                if not w:
                    continue
                respawns.append({
                    "transform": uid,
                    "name": scene.name_of(scene.xf.get(uid, {}).get("go", 0)),
                    "pos": [round(v, 4) for v in w["pos"]],
                    "rot": [round(v, 5) for row in w["mat"] for v in row],
                })
    # Many are duplicates by design (the same corner is listed by each knot
    # that can respawn into it), so collapse on rounded position.
    seen = set()
    uniq = []
    for r in respawns:
        k = tuple(r["pos"])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(r)
    respawns = uniq

    # Surface tags: which grip/surface each piece of scenery counts as, plus
    # the glide/boost triggers. These drive the per-surface handling modifiers
    # recovered in KartPhysicsHandling.KartHandlingSurfaceTypes.
    surfaces = {}
    triggers = []
    for cls, entries in scene.doc["behaviours"].items():
        if "ObjectTagBehaviour" not in cls:
            continue
        for e in entries:
            d = e["data"]
            floor = int(d.get("_floorSurfaceType", 0) or 0)
            surfaces[floor] = surfaces.get(floor, 0) + 1
            flags = {k: bool(d.get(k, 0)) for k in
                     ("_isGlideTrigger", "_isBoostTrigger",
                      "_isHopBoostTrigger", "_isHopGravityTrigger")}
            if any(flags.values()):
                tf = scene.transform_of_go(e["game_object"])
                w = scene.world(tf) if tf else None
                triggers.append({
                    "name": scene.name_of(e["game_object"]),
                    "pos": [round(v, 4) for v in w["pos"]] if w else [0.0, 0.0, 0.0],
                    "surface": floor,
                    "can_hop_over": bool(d.get("_canHopOver", 0)),
                    "flags": flags,
                })

    cameras = []
    for cls, entries in scene.doc["behaviours"].items():
        if "VirtualCamera" not in cls:
            continue
        for e in entries:
            tf = scene.transform_of_go(e["game_object"])
            w = scene.world(tf) if tf else None
            cameras.append({
                "name": scene.name_of(e["game_object"]),
                "fov": round(float(e["data"].get("_fieldOfView", 60.0)), 4),
                "pos": [round(v, 4) for v in w["pos"]] if w else [0.0, 0.0, 0.0],
            })

    start = None
    for cls, entries in scene.doc["behaviours"].items():
        if cls != "Atlas.Geometry.TrackRegionBehaviour":
            continue
        for e in entries:
            if scene.name_of(e["game_object"]).lower() in ("start", "startline",
                                                            "start_line"):
                tf = scene.transform_of_go(e["game_object"])
                w = scene.world(tf) if tf else None
                start = {"name": scene.name_of(e["game_object"]),
                         "pos": [round(v, 4) for v in w["pos"]] if w else [0.0, 0.0, 0.0]}
    if start is None:
        # Fall back to the first control point of the main spline.
        if splines and splines[0]["points"]:
            start = {"name": "spline_start",
                     "pos": splines[0]["points"][0]["pos"]}

    # Track length along the main spline -- the number the lap timer and AI
    # speed targets need, and it is only knowable from the recovered points.
    # A loop's closing segment is included even though the recovered points do
    # not repeat the start; without it every lap is short by one gap.
    length = 0.0
    if splines:
        pts = splines[0]["points"]
        seq = list(zip(pts, pts[1:]))
        if splines[0]["loop"] and len(pts) > 2:
            seq.append((pts[-1], pts[0]))
        for a, b in seq:
            length += math.dist(a["pos"], b["pos"])

    return {
        "id": base,
        "is_battle": "battle" in base,
        "start": start,
        "track_length": round(length, 3),
        "laps": 3,
        "map_bounds": bounds_of(scene, me.get("_mapBoundsBox")),
        "minimap_bounds": bounds_of(scene, me.get("_minimapBoundsPlane")),
        "main_spline_root_go": spline_root_behaviour,
        "route_config": route_config or None,
        "splines": splines,
        "pickup_spots": pickups,
        "respawn_locations": respawns,
        "surface_histogram": dict(sorted(surfaces.items())),
        "triggers": triggers,
        "cameras": cameras,
        "counts": scene.doc["counts"],
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scenes_dir")
    ap.add_argument("out_json")
    args = ap.parse_args(argv)

    files = sorted(glob.glob(os.path.join(args.scenes_dir, "*", "scene.json")))
    if not files:
        sys.exit("no scene.json under %s -- run tools/extract_assets.py first"
                 % args.scenes_dir)

    tracks = []
    for f in files:
        t = build_track(f)
        if t is None:
            print("  skip %s (no MapBehaviour)" % os.path.basename(os.path.dirname(f)))
            continue
        tracks.append(t)
        print("%-52s spline=%4d pts  len=%7.1f  pickups=%3d  respawns=%3d  cams=%d"
              % (t["id"][:52], t["splines"][0]["point_count"] if t["splines"] else 0,
                 t["track_length"], len(t["pickup_spots"]),
                 len(t["respawn_locations"]), len(t["cameras"])))

    os.makedirs(os.path.dirname(args.out_json) or ".", exist_ok=True)
    with open(args.out_json, "w") as fh:
        json.dump({"tracks": tracks}, fh)
    print("\nwrote %s  (%d tracks, %.1f MB)"
          % (args.out_json, len(tracks), os.path.getsize(args.out_json) / 1e6))
    return 0


if __name__ == "__main__":
    sys.exit(main())