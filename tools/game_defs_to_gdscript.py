#!/usr/bin/env python3
"""Compile every recovered ScriptableObject into typed GDScript data files.

`dump_definitions.py` recovers the game's whole data model as JSON. JSON is
good for reading and diffing but a Godot port wants it as compile-time
constants: no file IO at runtime, no JSON.parse cost on a phone, and the
GDScript type checker validates every field name against the port's own
classes. So this converts the recovered definitions into `const` dictionaries
split by domain, plus real curve helpers.

Definitions cross-reference each other by string id (`_refID`), not by
pointer, so the emitted dictionaries stay relational and readable -- a kart
names its season, its wheels and its season entry; a pickup table names the
usables it can roll. No ids need rewriting.

Output (into --out, default port/scripts/data):
    game_db.gd          the index: id -> {class, domain} for every definition
    handling.gd         the 6 KartPhysicsHandling profiles + surface modifiers
    catalog.gd          karts, characters, maps, wheels, antennas, gliders
    pickups.gd          pickup tables/boxes, usables, projectiles, boosts
    modes.gd            arcade modes, campaigns, seasons, chapters, events
    misc.gd             everything else, so nothing recovered is left behind

Usage:
    game_defs_to_gdscript.py <definitions.json> [--out DIR] [--pretty]
"""

from __future__ import annotations

import argparse
import collections
import json
import math
import os
import re
import sys

# Where each ScriptableObject class lands. Anything unlisted goes to misc.gd.
DOMAINS = [
    ("handling", ("KartPhysicsHandling",)),
    ("catalog", (
        "KartDefinition", "CharacterDefinition", "MapDefinition",
        "WheelsDefinition", "AntennaDefinition", "GliderDefinition",
        "LoadoutSetDefinition", "StatsDefinition", "RaceStatsDefinition",
    )),
    ("pickups", (
        "PickupTableDefinition", "PickupBoxDefinition", "PickupLimitsDefinition",
        "BoostDefinition", "BoostUsableDefinition", "NormalShieldUsableDefinition",
        "InHandUsableVisualsDefinition", "EliminationPickupsDefinition",
        "ProjectileDefinition", "RicochetProjectileDefinition",
        "ProximityHomingProjectileDefinition", "GoldenTurdProjectileDefinition",
        "Usable", "UsableDefinition",
    )),
    ("modes", (
        "ArcadeModeDefinition", "ArcadeCollectorEventDefinition",
        "ArcadeTimeTrialEventDefinition", "ArcadeShooterEventDefinition",
        "CampaignDefinition", "SeasonDefinition", "ChapterDefinition",
        "LeagueDefinition", "LeagueMessageDefinition", "FTUEStageDefinition",
        "GameContextDefinition", "ModifierDefinition", "OverchargeProfile",
        "RaceKartAIDefinition", "BattleKartAIDefinition",
        "FrontEndScreen3DSceneDefinition",
    )),
]
_DOMAINS = collections.OrderedDict((d, tuple(n.split(".")[-1] for n in nams))
                                   for d, nams in DOMAINS)

# Unity object header fields: present on every MonoBehaviour, no game data.
HEADER_FIELDS = ("m_GameObject", "m_Script", "m_Enabled")

# Curve typetrees carry no field named "Value"/"m_Value" at the top level, so
# they are recognised structurally instead of by name.
def is_curve(v) -> bool:
    return isinstance(v, dict) and "m_Curve" in v


def num(v) -> str:
    """A GDScript float literal that round-trips without float32 noise."""
    f = float(v)
    if math.isnan(f) or math.isinf(f):
        return "NAN" if math.isnan(f) else ("INF" if f > 0 else "-INF")
    r = round(f, 6)
    if r == int(r) and abs(r) < 1e15:
        return "%.1f" % r
    s = ("%.6f" % r).rstrip("0").rstrip(".")
    return s if s else "0.0"


def ident(s: str) -> str:
    """Definition id -> a safe GDScript dictionary key."""
    s = re.sub(r"[^A-Za-z0-9_]", "_", str(s))
    return s if re.match(r"[A-Za-z_]", s) else "_" + s


def emit(val, ind: str) -> str:
    """Render a recovered value as GDScript source."""
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, (int, float)):
        return num(val)
    if isinstance(val, str):
        return '"%s"' % val.replace("\\", "\\\\").replace('"', '\\"')
    if val is None:
        return "null"
    if is_curve(val):
        # Emitted as raw keyframes, NOT make_curve(...): a const initializer has
        # to be a constant expression and a function call is not one. The port
        # builds the Curve on demand via `GameDB.curve(def)` instead, which also
        # avoids constructing several hundred curves at load time for data that
        # most runs never touch.
        keys = val.get("m_Curve") or []
        rows = ", ".join(
            "[%s, %s, %s, %s, %s, %s]" % (
                num(k.get("time", 0.0)), num(k.get("value", 0.0)),
                num(k.get("inSlope", 0.0)), num(k.get("outSlope", 0.0)),
                num(k.get("inWeight", 0.0)), num(k.get("outWeight", 0.0)))
            for k in keys)
        return '{"__curve__": [%s]}' % rows
    if isinstance(val, dict):
        if "ref" in val and set(val) <= {"ref", "path", "inline"}:
            if "inline" in val:
                return "{\"ref\": %s, \"path\": %d, \"inline\": %s}" % (
                    emit(val["ref"], ind), int(val["path"]),
                    emit(val["inline"], ind))
            return "{\"ref\": %s, \"path\": %d}" % (emit(val["ref"], ind),
                                                    int(val["path"]))
        if not val:
            return "{}"
        inner = ",\n".join('%s"%s": %s' % (ind + "\t", k, emit(v, ind + "\t"))
                           for k, v in val.items())
        return "{\n%s\n%s}" % (inner, ind)
    if isinstance(val, list):
        if not val:
            return "[]"
        inner = ",\n".join(ind + "\t" + emit(x, ind + "\t") for x in val)
        return "[\n%s\n%s]" % (inner, ind)
    return '"%s"' % str(val).replace('"', '\\"')


PRELUDE = '''## GENERATED by tools/game_defs_to_gdscript.py -- do not edit by hand.
##
## @@COUNT@@ recovered ScriptableObjects from the Unity AssetBundle
## `@@BUNDLE@@`, Unity @@VERSION@@.
##
## These are the game's OWN data, read out of its shipped ScriptableObjects.
## Regenerate with:
##   /opt/re-tools/venv/bin/python tools/dump_definitions.py \\
##     "<definitions bundle>" work/assets/definitions --pretty
##   /opt/re-tools/venv/bin/python tools/game_defs_to_gdscript.py \\
##     work/assets/definitions/definitions.json
##
## Definitions cross-reference each other by id, not by pointer, so these
## dictionaries stay relational: a kart names its season and wheels, a pickup
## table names the usables it can roll. Look a reference up through
## `GameDB.resolve(id)`.

extends RefCounted


## A recovered Unity AnimationCurve, as its raw keyframes wrapped in a
## `__curve__` marker.
##
## Two reasons it is not a live `Curve`: a `const` initialiser must be a
## constant expression and cannot call `make_curve`, and several hundred curves
## exist in the data while a run touches only a handful. `GameDB.curve(value)`
## builds the live Curve on demand.
const CURVE := {"__curve__": [[0.0, 0.0, 0.0, 0.0, 0.3333, 0.3333], [1.0, 1.0, 0.0, 0.0, 0.3333, 0.3333]]}


## True if `value` is recovered curve data rather than an ordinary array/dict.
static func is_curve(value) -> bool:
\treturn value is Dictionary and value.has("__curve__")


## Build a Godot Curve from recovered Unity keyframes.
## Each key is [time, value, inSlope, outSlope, inWeight, outWeight].
##
## CRITICAL: Godot clamps every added point to the curve's min/max domain and
## min/max value, which BOTH default to 0..1. These curves live in absolute
## game units (time to 47, values to 40), so the ranges are widened BEFORE
## adding points -- otherwise every keyframe silently collapses onto the
## corner and the whole curve is lost with no error at all.
static func make_curve(points: Array) -> Curve:
\tvar c := Curve.new()
\tif points.is_empty():
\t\treturn c
\tvar t_lo := INF
\tvar t_hi := -INF
\tvar v_lo := INF
\tvar v_hi := -INF
\tfor p in points:
\t\tvar a := p as Array
\t\tt_lo = minf(t_lo, float(a[0]))
\t\tt_hi = maxf(t_hi, float(a[0]))
\t\tv_lo = minf(v_lo, float(a[1]))
\t\tv_hi = maxf(v_hi, float(a[1]))
\tvar tspan := maxf(t_hi - t_lo, 0.001)
\tc.min_domain = t_lo - tspan * 0.02
\tc.max_domain = t_hi + tspan * 0.02
\tvar vspan := maxf(v_hi - v_lo, 0.001)
\tc.min_value = v_lo - vspan * 0.1
\tc.max_value = v_hi + vspan * 0.1
\tfor p in points:
\t\tvar a2 := p as Array
\t\tc.add_point(Vector2(float(a2[0]), float(a2[1])), float(a2[2]),
\t\t\t\tfloat(a2[3]), Curve.TANGENT_FREE, Curve.TANGENT_FREE)
\treturn c


## Evaluate a recovered curve, falling back to `fallback` outside its domain.
static func sample_curve(points: Array, t: float, fallback: float = 0.0) -> float:
\tif points.is_empty():
\t\treturn fallback
\tvar c := make_curve(points)
\t# sample_baked() takes no fallback argument and clamps out-of-domain reads
\t# to the nearest key, which would hide a caller asking for a time the curve
\t# does not cover. A driving curve silently clamped is how you get a kart
\t# that refuses to accelerate, so the caller decides instead.
\tif t < c.min_domain or t > c.max_domain:
\t\treturn fallback
\treturn c.sample_baked(t)
'''


def substitute(template: str, count: int, bundle: str, version: str,
                classes: int = 0) -> str:
    """Fill the header template.

    Deliberately not str.format: the templates contain literal GDScript braces
    (the CURVE marker alone is `{"__curve__": [...]}`), and escaping those
    through a format string is exactly the kind of thing that silently breaks
    a regeneration later.
    """
    return (template.replace("@@COUNT@@", str(count))
                    .replace("@@CLASSES@@", str(classes))
                    .replace("@@BUNDLE@@", bundle)
                    .replace("@@VERSION@@", version))


def pick_domain(cls_short: str) -> str:
    for dom, names in _DOMAINS.items():
        if cls_short in names:
            return dom
    return "misc"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("definitions_json")
    ap.add_argument("--out", default="port/scripts/data")
    args = ap.parse_args(argv)

    with open(args.definitions_json) as fh:
        doc = json.load(fh)

    classes = doc["classes"]
    bundle = doc.get("source_bundle", "?")
    version = doc.get("unity_version", "?")

    os.makedirs(args.out, exist_ok=True)
    files = collections.defaultdict(list)   # domain -> (const_name, oid, source)
    index = {}                             # id -> {class, domain}
    total = 0

    for cls, objs in sorted(classes.items()):
        short = cls.split(".")[-1]
        dom = pick_domain(short)
        for o in objs:
            oid = o.get("id") or ("path_%s" % o.get("path_id"))
            data = {k: v for k, v in o["data"].items() if k not in HEADER_FIELDS}
            const = "%s_%s" % (ident(short), ident(oid))
            files[dom].append((const, ident(oid),
                               "const %s := %s" % (const, emit(data, ""))))
            index.setdefault(ident(oid), {
                "class": cls, "domain": dom, "const": const})
            total += 1

    for dom, decls in files.items():
        lines = [substitute(PRELUDE, len(decls), bundle, version)]
        lines.append("# ---- %s: %d definitions ----\n" % (dom, len(decls)))
        # Individual consts rather than one giant nested literal: a parse error
        # then points at one definition, and each stays diffable on its own.
        for _, _, src in decls:
            lines.append(src)
        lines.append("\n\n## Every %s definition, keyed by its recovered id.\n" % dom)
        lines.append("const BY_ID := {")
        for const, oid, _ in decls:
            lines.append('\t"%s": %s,' % (oid, const))
        lines.append("}")
        lines.append("\n\n## %s definition ids only, for cheap existence checks.\n" % dom)
        # A plain Array, not PackedStringArray: a PackedStringArray constructor
        # is not a constant expression, so `const IDS := PackedStringArray([...])`
        # fails to parse. Nothing needs the packed type here.
        lines.append("const IDS := [")
        for _, oid, _ in decls:
            lines.append('\t"%s",' % oid)
        lines.append("]")
        path = os.path.join(args.out, "%s.gd" % dom)
        with open(path, "w") as fh:
            fh.write("\n".join(lines) + "\n")
        print("wrote %-30s %4d definitions" % (path, len(decls)))

    # The index every other system looks ids up through.
    with open(os.path.join(args.out, "game_db.gd"), "w") as fh:
        fh.write(substitute(GAME_DB_PRELUDE, total, bundle, version, len(classes)))
        for dom in list(_DOMAINS) + ["misc"]:
            p = os.path.join(args.out, "%s.gd" % dom)
            if not os.path.exists(p):
                continue
            fh.write('const %s := preload("res://scripts/data/%s.gd")\n' % (dom, dom))
        fh.write("\n\n## Every recovered definition id -> where it lives.\n")
        fh.write("const INDEX := {\n")
        for oid, meta in sorted(index.items()):
            fh.write('\t"%s": {"class": "%s", "domain": "%s", "const": "%s"},\n'
                     % (oid, meta["class"], meta["domain"], meta["const"]))
        fh.write("}\n")
        fh.write(ACCESSORS)
    print("wrote %-28s %4d ids" % (os.path.join(args.out, "game_db.gd"), len(index)))

    print("total: %d definitions across %d classes" % (total, len(classes)))
    return 0


GAME_DB_PRELUDE = '''## GENERATED by tools/game_defs_to_gdscript.py -- do not edit by hand.
##
## Index over the whole recovered game database: @@COUNT@@ ScriptableObjects
## across @@CLASSES@@ classes, read out of `@@BUNDLE@@` (Unity @@VERSION@@).
##
## The game's definitions reference each other by string id, so this is the
## single place that maps an id to the object it names -- the same job the
## game's `Definitions` service does at runtime.
##
## Regenerate with tools/game_defs_to_gdscript.py.

extends RefCounted

'''


ACCESSORS = '''

# ---------------------------------------------------------------------------
# Lookup API
#
# The port addresses definitions the way the game does -- by id string, e.g.
# `GameDB.get("KartPhysicsHandlingDefault")` or
# `GameDB.get("Kart_SO_TerraFormula1")["_seasonDefinitionRef"]["_refID"]`.
# ---------------------------------------------------------------------------


## Resolve a definition by its recovered id. Returns {} when unknown, so
## callers can do `if not GameDB.lookup(id).is_empty()` instead of null checks.
##
## Named `lookup`, not `get`, because Object already owns `get(property)` and
## shadowing it breaks static type resolution at every call site.
static func lookup(id: String) -> Dictionary:
\tvar meta: Dictionary = INDEX.get(id, {})
\tif not meta.is_empty():
\t\t# Returns the domain's id -> definition table directly rather than the
\t\t# script: a GDScript object's constants are not reachable by property
\t\t# access, so `domain.BY_ID` fails at runtime.
\t\treturn get_domain(meta["domain"]).get(id, {})
\t# Fall back to a case-insensitive match. The shipped data needs it: five
\t# chapters (Chapter_Prologue_1 and the four Chapter_Legend_* entries)
\t# reference `_ipDefinitionRef` = "IP_GENERIC" while the definition that
\t# actually ships is "IP_Generic". The original game resolves these -- its
\t# single-player menu works -- so its definition lookup cannot be
\t# case-sensitive. Matching the original here rather than "fixing" the
\t# reference keeps the recovered data byte-for-byte and makes the port behave
\t# like the game it is a port of.
\tvar folded := id.to_lower()
\tfor known in INDEX:
\t\tif (known as String).to_lower() == folded:
\t\t\treturn get_domain(INDEX[known]["domain"]).get(known, {})
\treturn {}


## Does this id exist in the recovered database? Case-insensitive, matching
## `lookup`.
static func has_def(id: String) -> bool:
\treturn not lookup(id).is_empty()


## Every definition of one class, as id -> definition. `cls` is the bare class
## name, e.g. "MapDefinition".
static func by_class(cls: String) -> Dictionary:
\tvar out := {}
\tfor id in INDEX:
\t\tif (INDEX[id]["class"] as String).ends_with("." + cls):
\t\t\tout[id] = lookup(id)
\treturn out


## Ids of one class, in database order.
static func ids_of_class(cls: String) -> Array:
\tvar out := []
\tfor id in INDEX:
\t\tif (INDEX[id]["class"] as String).ends_with("." + cls):
\t\t\tout.append(id)
\treturn out


## Follow a `_refID` field: `resolve(def, "_seasonDefinitionRef")` returns the
## definition that field names, or {} if it is empty or dangling. This is the
## one call the port needs to walk the whole game data graph.
static func resolve(def: Dictionary, field: String) -> Dictionary:
\tvar ref: Dictionary = def.get(field, {})
\tvar id: String = ref.get("_refID", "")
\treturn lookup(id) if id != "" else {}


## The id -> definition table for one data domain. Returns {} for an unknown
## domain name.
static func get_domain(domain: String) -> Dictionary:
\tmatch domain:
\t\t"handling":
\t\t\treturn handling.BY_ID
\t\t"catalog":
\t\t\treturn catalog.BY_ID
\t\t"pickups":
\t\t\treturn pickups.BY_ID
\t\t"modes":
\t\t\treturn modes.BY_ID
\t\t"misc":
\t\t\treturn misc.BY_ID
\treturn {}


## True if `value` is a recovered AnimationCurve rather than ordinary data.
static func is_curve(value) -> bool:
\treturn handling.is_curve(value)


## Build a live Curve from recovered keyframes. Cached per call site is the
## caller's business -- curves are cheap but not free, and the recovered set is
## large.
static func curve(value) -> Curve:
\tif not is_curve(value):
\t\tpush_warning("GameDB.curve() on non-curve value")
\t\treturn Curve.new()
\treturn handling.make_curve(value["__curve__"])


## Sample a recovered curve without materialising a Curve. Falls back to
## `fallback` for a non-curve so callers never have to branch.
static func sample(value, t: float, fallback: float = 0.0) -> float:
\tif not is_curve(value):
\t\treturn fallback
\tvar c: Curve = handling.make_curve(value["__curve__"])
\t# sample_baked() takes no fallback and clamps out-of-domain reads, which
\t# would hide a caller asking for a time the curve never covers. Clamping a
\t# driving curve silently is exactly the kind of bug that only shows up as a
\t# kart that will not accelerate, so the caller decides instead.
\tif t < c.min_domain or t > c.max_domain:
\t\treturn fallback
\treturn c.sample_baked(t)
'''


if __name__ == "__main__":
    sys.exit(main())