#!/usr/bin/env python3
"""Export recovered Unity ScriptableObject data as a Godot GDScript resource.

The kart handling *values* do not live in code -- they live in
`Atlas.Definitions.*` / `Atlas.Gameplay.Kart.*` ScriptableObjects serialized
inside the game's AssetBundles. Reading them is what turns the port from
"right shape, invented numbers" into the real tuning.

This reads a named profile out of an AssetBundle with UnityPy and emits a
`.gd` file whose exported vars carry the REAL values, including full
AnimationCurve keyframes.

The bundle's Unity version string is stripped by the IL2CPP build, so the
version known from `globalgamemanagers` is supplied explicitly rather than
guessed.

Usage:
    unity_defs_to_gdscript.py <bundle> <Class.Name> <ProfileId> <out.gd> \\
        [--monoscripts <bundle>] [--class-name OutClassName]

Example:
    unity_defs_to_gdscript.py \\
      ".../definitions_assets_all_<hash>.bundle" \\
      Atlas.Gameplay.Kart.KartPhysicsHandling KartPhysicsHandlingDefault \\
      port/scripts/kart_physics_handling_data.gd \\
      --monoscripts ".../..._monoscripts_<hash>.bundle"

The MonoScripts bundle is required to map a MonoBehaviour back to its class
name; without it every MonoBehaviour is anonymous.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys

# From Resources/Data/globalgamemanagers. The AssetBundles have the version
# string stripped, so UnityPy needs to be told.
FALLBACK_UNITY_VERSION = "2021.3.56f2"


def load_unitypy():
    """Import UnityPy on demand.

    Imported lazily so `--help` and argument validation work without the
    dependency installed -- otherwise this script is unrunnable on a machine
    that has not set up the venv, which is exactly when you want to read the
    usage text.
    """
    try:
        import UnityPy
        import UnityPy.config
    except ImportError:
        sys.exit("missing UnityPy. Install with: "
                 "/opt/re-tools/venv/bin/pip install UnityPy")
    UnityPy.config.FALLBACK_UNITY_VERSION = FALLBACK_UNITY_VERSION
    return UnityPy


def snake(name: str) -> str:
    """_kartToStaticBounceMax -> kart_to_static_bounce_max (Unity name -> GDScript)."""
    n = name.lstrip("_")
    n = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", n)
    n = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", n)
    return n.lower()


def gdscript_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def gdscript_float(v) -> str:
    """Emit a float literal that round-trips, without float32 noise."""
    f = float(v)
    # Unity serializes float32, so values arrive as e.g. 4.199999809265137.
    # Trim to something a human wrote: 6 significant decimals.
    r = round(f, 6)
    if r == int(r) and abs(r) < 1e15:
        return "%.1f" % r
    s = ("%.6f" % r).rstrip("0").rstrip(".")
    return s if s else "0.0"


def emit_curve(curve, indent: str) -> list[str]:
    """Render a Unity AnimationCurve as GDScript Curve.add_point() calls."""
    lines = []
    keys = curve.get("m_Curve") or []
    if not keys:
        return lines
    ts = [float(k.get("time", 0.0)) for k in keys]
    vs = [float(k.get("value", 0.0)) for k in keys]
    t_lo, t_hi = min(ts), max(ts)
    v_lo, v_hi = min(vs), max(vs)
    tspan = max(t_hi - t_lo, 0.001)
    vspan = max(v_hi - v_lo, 0.001)
    lines.append("%svar c := Curve.new()" % indent)
    # Widen the domains FIRST: Curve.add_point clamps to 0..1 by default and
    # these curves are in absolute units, so points would silently collapse.
    lines.append("%sc.min_domain = %s" % (indent, gdscript_float(t_lo - tspan * 0.02)))
    lines.append("%sc.max_domain = %s" % (indent, gdscript_float(t_hi + tspan * 0.02)))
    lines.append("%sc.min_value = %s" % (indent, gdscript_float(v_lo - vspan * 0.1)))
    lines.append("%sc.max_value = %s" % (indent, gdscript_float(v_hi + vspan * 0.1)))
    for k in keys:
        lines.append(
            "%sc.add_point(Vector2(%s, %s), %s, %s, Curve.TANGENT_FREE, Curve.TANGENT_FREE)"
            % (
                indent,
                gdscript_float(k.get("time", 0.0)),
                gdscript_float(k.get("value", 0.0)),
                gdscript_float(k.get("inSlope", 0.0)),
                gdscript_float(k.get("outSlope", 0.0)),
            )
        )
        # Unity tangent weights are dropped: every keyframe in this game's
        # curves uses Unity's neutral 1/3 (i.e. TANGENT_FREE, no weighting), so
        # they carry no information -- and Godot 4's Curve has no per-point
        # weight setter anyway. Lossless.
    return lines


def curve_expr(curve, indent: str) -> str:
    """Curve values as a single expression usable in a default initialiser."""
    keys = curve.get("m_Curve") or []
    if not keys:
        return "_default_curve()"
    parts = []
    for k in keys:
        parts.append(
            "[%s, %s, %s, %s, %s, %s]"
            % (
                gdscript_float(k.get("time", 0.0)),
                gdscript_float(k.get("value", 0.0)),
                gdscript_float(k.get("inSlope", 0.0)),
                gdscript_float(k.get("outSlope", 0.0)),
                gdscript_float(k.get("inWeight", 0.0)),
                gdscript_float(k.get("outWeight", 0.0)),
            )
        )
    return "_curve([%s])" % ", ".join(parts)


HEADER = '''## GENERATED by tools/unity_defs_to_gdscript.py -- do not edit by hand.
##
## Real tuning values extracted from the Unity AssetBundle asset
## `{source}`
## profile `{profile_id}` of `{cls}`.
##
## These are the game's OWN numbers, recovered from the shipped
## ScriptableObjects -- not estimates. Regenerate with:
##   {cmd}
##
## Curves carry the full keyframe set (time, value, in/out slope, weights).

extends Resource

const SOURCE_BUNDLE := {source}
const PROFILE_ID := {profile_id}
const SOURCE_CLASS := {class_literal}


## Build a Curve from raw keyframes [time, value, inSlope, outSlope, inW, outW].
## Weights are accepted for format stability but ignored: every keyframe in this
## game's curves uses Unity's neutral 1/3, and Godot 4's Curve has no per-point
## weight setter. Elements are cast explicitly -- indexing an untyped Array
## yields Variant, and inferring from Variant is an error in Godot 4.7.
static func _curve(points: Array) -> Curve:
	var c := Curve.new()
	if points.is_empty():
		return c
	# CRITICAL: Godot's Curve clamps every added point to min/max domain and
	# min/max value, which both default to 0..1. These curves live in absolute
	# game units (time up to 47, values up to 40), so without widening the
	# ranges first every keyframe silently collapses onto the corner and the
	# whole curve is destroyed -- with no error at all.
	var t_lo := INF
	var t_hi := -INF
	var v_lo := INF
	var v_hi := -INF
	for p in points:
		var arr := p as Array
		t_lo = minf(t_lo, float(arr[0])); t_hi = maxf(t_hi, float(arr[0]))
		v_lo = minf(v_lo, float(arr[1])); v_hi = maxf(v_hi, float(arr[1]))
	# Pad so end tangents are not clipped at the boundary.
	var span := maxf(t_hi - t_lo, 0.001)
	c.min_domain = t_lo - span * 0.02
	c.max_domain = t_hi + span * 0.02
	var vspan := maxf(v_hi - v_lo, 0.001)
	c.min_value = v_lo - vspan * 0.1
	c.max_value = v_hi + vspan * 0.1
	for p in points:
		var arr2 := p as Array
		c.add_point(
			Vector2(float(arr2[0]), float(arr2[1])),
			float(arr2[2]), float(arr2[3]),
			Curve.TANGENT_FREE, Curve.TANGENT_FREE)
	return c


## Unity stores a "no weighting" tangent as 1/3; every keyframe in this game's
## curves uses exactly that, so weights are simply ignored on export.
static func _default_curve() -> Curve:
	return _curve([[0.0, 1.0, 0.0, 0.0, 0.0, 0.0], [1.0, 1.0, 0.0, 0.0, 0.0, 0.0]])

'''


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("bundle")
    ap.add_argument("cls", help="e.g. Atlas.Gameplay.Kart.KartPhysicsHandling")
    ap.add_argument("profile_id", help="value of the _id field, e.g. KartPhysicsHandlingDefault")
    ap.add_argument("out")
    ap.add_argument("--monoscripts", help="bundle containing the MonoScript table")
    ap.add_argument("--class-name", help="class_name to declare in the output")
    args = ap.parse_args(argv)

    UnityPy = load_unitypy()

    monos = args.monoscripts
    if not monos:
        hits = glob.glob(os.path.join(os.path.dirname(args.bundle), "*monoscripts*.bundle"))
        if not hits:
            sys.exit("no MonoScripts bundle given and none found next to the input")
        monos = hits[0]

    env = UnityPy.load(monos, args.bundle)

    scripts = {}
    for o in env.objects:
        if o.type.name == "MonoScript":
            d = o.read()
            scripts[o.path_id] = "%s.%s" % (d.m_Namespace, d.m_ClassName)

    matches = []
    for o in env.objects:
        if o.type.name != "MonoBehaviour":
            continue
        try:
            m = o.read()
        except Exception:
            continue
        if scripts.get(m.m_Script.path_id) != args.cls:
            continue
        tt = o.read_typetree()
        ident = tt.get("_id")
        ident = ident.get("Value") if isinstance(ident, dict) else ident
        if ident == args.profile_id:
            matches.append(tt)

    if not matches:
        sys.exit("no %s with _id == %r found" % (args.cls, args.profile_id))
    if len(matches) > 1:
        sys.exit("%d objects share _id == %r (ambiguous)" % (len(matches), args.profile_id))
    tt = matches[0]

    floats, curves, tables, others = [], [], [], []
    for key, val in tt.items():
        if key in ("m_Script", "m_GameObject", "m_Enabled", "_id"):
            continue
        if key.startswith("_") and key.endswith("Curve") and isinstance(val, dict):
            curves.append((key, val))
        elif isinstance(val, bool):
            others.append((key, val))
        elif isinstance(val, (int, float)):
            floats.append((key, val))
        elif isinstance(val, list) and val and isinstance(val[0], dict):
            tables.append((key, val))
        elif isinstance(val, dict):
            floats.append((key, None))     # structured; rendered as comment only

    cls_name = args.class_name or re.sub(r"[^A-Za-z0-9]", "", args.cls.split(".")[-1])
    cmd = "python3 tools/unity_defs_to_gdscript.py <bundle> %s %s %s" % (
        args.cls, args.profile_id, os.path.basename(args.out))

    lines = [HEADER.format(
        source=gdscript_str(os.path.basename(args.bundle)),
        profile_id=gdscript_str(args.profile_id),
        class_literal=gdscript_str(args.cls),
        cls=gdscript_str(args.cls),
        cmd=cmd)]

    lines.append("\n# --- scalar tuning values (%d) ---" % len(floats))
    for key, val in sorted(floats):
        if val is None:
            lines.append("## %s = %s   (structured value, not a float)" % (key, tt[key]))
            continue
        lines.append("const %s: float = %s" % (snake(key).upper(), gdscript_float(val)))

    lines.append("\n# --- curves (%d, full keyframes) ---" % len(curves))
    for key, val in sorted(curves):
        lines.append("\n# %s (%d keyframes)" % (key, len(val.get("m_Curve") or [])))
        lines.append("static func curve_%s() -> Curve:" % snake(key))
        for l in emit_curve(val, "\t"):
            lines.append(l)
        lines.append("\treturn c")

    if others:
        lines.append("\n# --- other ---")
        for key, val in sorted(others):
            lines.append("const %s: bool = %s" % (snake(key).upper(), "true" if val else "false"))

    # Nested struct tables (e.g. DriftBoostLevel[]). Emitted as flat Arrays of
    # [type, time, ratio, duration, steerMin, steerMax, steerNeutral] rather
    # than constructing typed objects, because the consuming script already
    # depends on this one -- building the classes here would be circular.
    if tables:
        lines.append("\n# --- nested tables (%d) ---" % len(tables))
        for key, rows in sorted(tables):
            keys = sorted(rows[0].keys())
            # Only flat rows (all scalars) are emitted. A row holding a nested
            # struct -- e.g. KartHandlingSurfaceModifiers inside
            # KartHandlingSurfaceTypes -- would stringify as Python repr and
            # produce invalid GDScript, so those are skipped and left in the
            # raw JSON instead.
            if not all(isinstance(r.get(k), (int, float, bool))
                       for r in rows for k in keys):
                lines.append("\n# %s (%d entries) -- SKIPPED: contains nested"
                             " structs, see work/assets/definitions_raw.json"
                             % (key, len(rows)))
                continue
            lines.append("\n# %s (%d entries)" % (key, len(rows)))
            lines.append("# columns: %s" % ", ".join(keys))
            lines.append("static func %s_raw() -> Array:" % snake(key))
            lines.append("\treturn [")
            for row in rows:
                cells = []
                for k in keys:
                    v = row.get(k, 0.0)
                    cells.append(gdscript_float(v) if isinstance(v, (int, float))
                                 else gdscript_str(str(v)))
                lines.append("\t\t[%s]," % ", ".join(cells))
            lines.append("\t]")

    with open(args.out, "w") as fh:
        fh.write("\n".join(lines) + "\n")

    print("wrote %s" % args.out)
    print("  profile      : %s" % args.profile_id)
    print("  scalars      : %d" % len([1 for _, v in floats if v is not None]))
    print("  curves       : %d" % len(curves))
    if tables:
        print("  tables       : %s" % ", ".join("%s[%d]" % (k, len(v)) for k, v in tables))
    total_keys = sum(len(v.get("m_Curve") or []) for _, v in curves)
    print("  curve keys   : %d" % total_keys)
    print("  json kept at : work/assets/definitions_raw.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())