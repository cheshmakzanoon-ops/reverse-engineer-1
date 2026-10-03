## `Atlas.Gameplay.Kart.DriftBoostLevel` recovered from
## `GameAssembly.dylib`. All 8 fields and their offsets are exact.
##
## This is what makes Warped Kart Racers' drift a *level table* rather than a
## boolean: each level carries its own steer window, so the player holds a
## tighter drift angle as the meter fills. `_driftBoostType` (offset +0x0) is
## the discriminator and is preserved as an enum.
##
## VALUES ARE THE GAME'S OWN. The three levels come from the serialized
## `_driftBoostLevelsTable` inside `KartPhysicsHandlingDefault`
## (`definitions_assets_all_*.bundle`), extracted by
## `tools/unity_defs_to_gdscript.py`. The table ships in descending order
## (largest boost first); it is re-ordered smallest-first below so the drift
## meter can be walked in one ascending pass.
##
## Guessed values were wrong by up to 3x (time-to-activate is 2.0/4.2/6.9 s,
## not 1.0/2.2/3.8), which is why nothing here is hand-invented any more.

class_name DriftBoostLevel
extends Resource

## Recovered table + keyframe data. Generated; do not edit.
const D := preload("res://scripts/kart_physics_handling_data.gd")

enum DriftBoostType {
	INVALID = 0,
	SMALL = 1,
	MEDIUM = 2,
	LARGE = 3,
}

@export var drift_boost_type: DriftBoostType = DriftBoostType.INVALID  # +0x0
@export var time_to_activate: float = 0.0                               # +0x4
@export var drift_boost_ratio: float = 1.0                              # +0x8
@export var drift_boost_duration: float = 0.0                           # +0xC
@export var drift_steer_min: float = 0.5                                # +0x10
@export var drift_steer_max: float = 1.0                                # +0x14
@export var drift_steer_neutral: float = 0.2                            # +0x18


## Mirrors the recovered `bool IsValid()` (RVA 0x1256828).
func is_valid() -> bool:
	return drift_boost_type != DriftBoostType.INVALID


## The game's own `_driftBoostLevelsTable`, ordered smallest boost first so a
## drift can be walked by ascending elapsed time.
##
## Column order in the generated data is
## `[duration, ratio, type, steerMax, steerMin, steerNeutral, timeToActivate]`.
static func make_defaults() -> Array[DriftBoostLevel]:
	var out: Array[DriftBoostLevel] = []

	var invalid := DriftBoostLevel.new()
	invalid.drift_boost_type = DriftBoostType.INVALID
	out.append(invalid)

	var rows := D.drift_boost_levels_table_raw()
	var levels: Array[DriftBoostLevel] = []
	for r in rows:
		var row := r as Array
		var lv := DriftBoostLevel.new()
		lv.drift_boost_duration = row[0]
		lv.drift_boost_ratio = row[1]
		lv.drift_boost_type = row[2] as DriftBoostType
		lv.drift_steer_max = row[3]
		lv.drift_steer_min = row[4]
		lv.drift_steer_neutral = row[5]
		lv.time_to_activate = row[6]
		levels.append(lv)

	# Shipped largest-first; walk smallest-first.
	levels.reverse()
	out.append_array(levels)
	return out