## Builds live `KartPhysicsHandling` objects from the recovered game database.
##
## `KartPhysicsHandlingDefault` was exported to GDScript by
## tools/unity_defs_to_gdscript.py, but the other five profiles -- Battle,
## Race150, RaceAI, Shooter, ShooterExtraSlow -- were not, and hardcoding them
## would mean inventing numbers for exactly the modes where the tuning matters
## most (Battle glides heavily at torque 75 vs 48; Shooter caps at 40 vs 55).
##
## They are not hardcoded here either. All six ship in `data/handling.gd`, read
## out of the game's own ScriptableObjects, so this only has to copy one
## definition onto a `KartPhysicsHandling` -- including the curves, the
## three-tier drift boost ladder, and the seven per-surface grip modifiers,
## which are the values that make grass, boost pads and ice feel different.
##
## Curves are built through `GameDB.curve()`, which widens the domain first.
## That is not optional: the recovered curves run to time 47 and value 40, and
## Godot's default 0..1 clamp would collapse every keyframe onto one corner
## with no error at all.

class_name HandlingProfile
extends RefCounted

const GameDB := preload("res://scripts/data/game_db.gd")
const KartPhysicsHandlingScript := preload("res://scripts/kart_physics_handling.gd")
const DriftBoostLevelScript := preload("res://scripts/drift_boost_level.gd")

## Profile id -> the handling object. Built once and shared: these are
## immutable after load, and every kart of a type shares one in the original.
static var _cache: Dictionary = {}


## Every profile id the game ships.
static func profile_ids() -> PackedStringArray:
	var out := PackedStringArray()
	for id in GameDB.handling.IDS:
		out.append("KartPhysicsHandling" + str(id).trim_prefix("KartPhysicsHandling"))
	return out


## Load a profile by id, e.g. "KartPhysicsHandlingBattle".
static func load_profile(id: String) -> KartPhysicsHandling:
	if _cache.has(id):
		return _cache[id]
	var def := GameDB.lookup(id)
	if def.is_empty():
		push_warning("HandlingProfile: no recovered definition %s; using Default" % id)
		def = GameDB.lookup("KartPhysicsHandlingDefault")
		if def.is_empty():
			return null
	var h := _apply(KartPhysicsHandlingScript.new(), def)
	_cache[id] = h
	return h


## Load the profile a game mode uses. Race and Battle genuinely differ in the
## shipped data -- different grip, different drift ladder, different glide -- so
## this picks the real one rather than approximating a mode by scaling numbers.
static func for_mode(mode: String) -> KartPhysicsHandling:
	match mode.to_lower():
		"battle":
			return load_profile("KartPhysicsHandlingBattle")
		"race150":
			return load_profile("KartPhysicsHandlingRace150")
		"shooter":
			return load_profile("KartPhysicsHandlingShooter")
		"shooterextraslow":
			return load_profile("KartPhysicsHandlingShooterExtraSlow")
		"ai":
			return load_profile("KartPhysicsHandlingRaceAI")
		_:
			return load_profile("KartPhysicsHandlingDefault")


## Apply one recovered definition onto a handling object.
static func _apply(h: KartPhysicsHandling, def: Dictionary) -> KartPhysicsHandling:
	# Scalars. Keys are the game's own field names, which differ from the port's
	# snake_case by a fixed transform (drop '_', camel -> snake), so the mapping
	# is mechanical and total rather than a hand-written list that can drift.
	for key in def:
		if key in _SKIP:
			continue
		var prop := _prop_for(key)
		if prop == "":
			continue
		var value: Variant = def[key]
		if GameDB.is_curve(value):
			h.set(prop, GameDB.curve(value))
		elif value is float or value is int:
			h.set(prop, float(value))
		elif value is bool:
			h.set(prop, value)

	h.drift_boost_levels = _drift_levels(def)
	h.surface_types = _surface_types(def)
	return h


## Recovered fields that are NOT tuning: ids, names and asset references.
const _SKIP := {
	"m_Name": true, "_id": true, "_ipDefinitionRef": true,
	"_displayNameLocKey": true, "_seasonDefinitionRef": true,
	"_contentVersion": true, "_assetRef": true,
	"_thumbnailAtlasedSpriteRef": true, "_unlockedByDefault": true,
	"_enableMaskOnGrid": true,
}


static func _prop_for(key: String) -> String:
	## Unity field name -> port property name, matching the transform used when
	## the handling class was written: strip a leading underscore, then
	## camelCase to snake_case.
	var n := key.trim_prefix("_")
	var out := ""
	for i in n.length():
		var ch := n[i]
		if ch == ch.to_upper() and ch != ch.to_lower() and i > 0:
			out += "_" + ch.to_lower()
		else:
			out += ch.to_lower()
	return out


static func _drift_levels(def: Dictionary) -> Array[DriftBoostLevel]:
	# Typed, because KartPhysicsHandling.drift_boost_levels is declared
	# `Array[DriftBoostLevel]` and GDScript refuses an untyped Array there --
	# assigning one is a hard error, not a coercion.
	var out: Array[DriftBoostLevel] = []
	for row in def.get("_driftBoostLevelsTable", []):
		var lvl := DriftBoostLevelScript.new()
		lvl.drift_boost_type = int(row.get("_driftBoostType", 0))
		lvl.time_to_activate = float(row.get("_timeToActivate", 0.0))
		lvl.drift_boost_ratio = float(row.get("_driftBoostRatio", 0.0))
		lvl.drift_boost_duration = float(row.get("_driftBoostDuration", 0.0))
		lvl.drift_steer_min = float(row.get("_driftSteerMin", 0.0))
		lvl.drift_steer_max = float(row.get("_driftSteerMax", 0.0))
		lvl.drift_steer_neutral = float(row.get("_driftSteerNeutral", 0.0))
		out.append(lvl)
	return out


static func _surface_types(def: Dictionary) -> Array:
	## Per-surface grip multipliers (`KartHandlingSurfaceTypes`). These are the
	## numbers that decide whether a surface is grippy or not: surface 2 runs at
	## 0.5 speed and 1.2 braking, so leaving them at 1.0 makes every track feel
	## like tarmac.
	var out: Array = []
	for row in def.get("KartHandlingSurfaceTypes", []):
		var m: Dictionary = row.get("KartHandlingSurfaceModifiers", {})
		var turn: Dictionary = m.get("turningModifiers", {})
		out.append({
			"surface_type": int(row.get("SurfaceType", 0)),
			"speed": float(m.get("speedModifier", 1.0)),
			"reverse_speed": float(m.get("reverseSpeedModifier", 1.0)),
			"braking": float(m.get("brakingModifier", 1.0)),
			"drift_speed_min": float(m.get("driftSpeedMinModifier", 1.0)),
			"deceleration": float(m.get("surfaceDecelerationFactor", 0.0)),
			"rot_speed_from_speed": float(turn.get("rotSpeedFromKartSpeedModifier", 1.0)),
			"rot_speed_from_steer": float(turn.get("rotSpeedFromSteerAngleModifier", 1.0)),
			"tire_grip": float(turn.get("tireGripModifier", 1.0)),
			"turn_speed_compensation": float(turn.get("turnSpeedCompensationModifier", 1.0)),
			"turn_speed_drift_compensation": float(m.get("turnSpeedDriftCompensationModifier", 1.0)),
			"rot_speed_drift_ratio": float(m.get("rotSpeedDriftRatioModifier", 1.0)),
			"tire_grip_drift_min": float(m.get("tireGripDriftMinModifier", 1.0)),
			"tire_grip_drift_max": float(m.get("tireGripDriftMaxModifier", 1.0)),
			"hop_force": float(m.get("HopForceModifier", 1.0)),
		})
	return out


## Surface modifiers for a surface type, or {} when the track uses a type the
## handling profile does not define. `KartHandlingSurfaceTypes` covers only the
## types a kart is meant to meet; falling back to all-1.0 would silently make
## an unknown surface behave as tarmac, so callers are told it was missing.
static func surface_modifiers(h: KartPhysicsHandling, surface_type: int) -> Dictionary:
	for s in h.surface_types:
		if int(s["surface_type"]) == surface_type:
			return s
	return {}