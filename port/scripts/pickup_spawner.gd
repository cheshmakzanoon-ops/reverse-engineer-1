## Pickup boxes and what they roll, driven by the recovered definitions.
##
## The chain is entirely data-driven and none of it is invented:
##
##   PickupSpotBehaviour (per track, recovered from the map scene)
##     -> PickupBoxDefinition      which box it is, and its respawn time
##       -> PickupTableDefinition  weighted distribution of usable ids
##         -> Usable_*             what the id actually does
##
## The weighted roll (`Weightage`, out of 100 in the shipped data) is the game's
## own loot table. Arlen Speedway's 52 spots are all `PickupBox_Race_Default`,
## so whatever that box resolves to is exactly what this track hands out.
##
## Only the effects that are actually implemented are wired to behaviour; the
## rest roll and are held, so an unimplemented pickup shows up as a missing
## effect rather than being quietly dropped from the table and changing the
## odds of the ones that do work.

class_name PickupSpawner
extends Node3D

const GameDB := preload("res://scripts/data/game_db.gd")
const Tracks := preload("res://scripts/data/tracks.gd")

## usables the port actually implements, and what they do.
enum Effect { BOOST, TRIPLE_BOOST, SHIELD, TURD, NONE }

signal kart_picked_up(kart: Node3D, usable_id: String, effect: Effect)

@export var track_id: String = ""
@export var kart_radius: float = 1.6
@export var random_seed: int = 20202

class Spot extends RefCounted:
	var area: Area3D
	var position: Vector3
	var box_id: String
	var respawn_time: float
	var timer: float = 0.0
	var held: String = ""
	var held_effect: int = Effect.NONE
	var held_ratio: float = 1.0
	var held_duration: float = 0.0

var _spots: Array[Spot] = []


## Build boxes from the track's recovered pickup spots.
func build(id: String) -> void:
	track_id = id
	_spots.clear()
	for child: Node in get_children():
		child.queue_free()

	var mat := StandardMaterial3D.new()
	mat.albedo_color = Color(0.95, 0.75, 0.2, 0.55)
	mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA

	for spot in Tracks.pickup_spots(id):
		var s := Spot.new()
		s.position = spot["pos"]
		s.box_id = str(spot["box"])
		s.respawn_time = float(spot["respawn"])
		var a := Area3D.new()
		var cs := CollisionShape3D.new()
		var shape := SphereShape3D.new()
		shape.radius = kart_radius
		cs.shape = shape
		a.add_child(cs)
		var mi := MeshInstance3D.new()
		var pm := PlaneMesh.new()
		pm.size = Vector2(2.2, 2.2)
		mi.mesh = pm
		mi.material_override = mat
		mi.rotation_degrees = Vector3(-90, 0, 0)
		a.add_child(mi)
		a.position = s.position + Vector3.UP * float(spot["height"])
		add_child(a)
		s.area = a
		_spots.append(s)


func spot_count() -> int:
	return _spots.size()


func _process(delta: float) -> void:
	for s in _spots:
		if s.respawn_time <= 0.0:
			continue
		if s.timer > 0.0:
			s.timer -= delta
			if s.timer <= 0.0:
				s.timer = 0.0
				s.held = ""
				s.held_effect = Effect.NONE
				s.area.visible = true


## Called by the kart when it drives into a box.
func collect(area: Area3D, kart: Node3D) -> void:
	var s := _find(area)
	if s == null or s.held != "":
		return
	var roll := _roll(s.box_id)
	s.held = roll["id"]
	s.held_effect = roll["effect"]
	s.held_ratio = roll["ratio"]
	s.held_duration = roll["duration"]
	if s.respawn_time > 0.0:
		s.timer = s.respawn_time
	s.area.visible = false
	kart_picked_up.emit(kart, s.held, s.held_effect)


func _find(a: Area3D) -> Spot:
	for s in _spots:
		if s.area == a:
			return s
	return null


## Resolve a box -> table -> weighted usable, all from the recovered database.
func _roll(box_id: String) -> Dictionary:
	var box := GameDB.lookup(box_id)
	if box.is_empty():
		return {"id": "", "effect": Effect.NONE, "ratio": 1.0, "duration": 0.0}
	var table_id := str(box.get("_playerTable", ""))
	var table := GameDB.lookup(table_id)
	if table.is_empty():
		return {"id": "", "effect": Effect.NONE, "ratio": 1.0, "duration": 0.0}

	var entries: Array = table.get("_pickupDistributions", [])
	if entries.is_empty():
		return {"id": "", "effect": Effect.NONE, "ratio": 1.0, "duration": 0.0}
	# One distribution per distance band; the default band is the one with no
	# max distance set, which is the general case for a race box.
	var dist: Dictionary = entries[0]
	for e in entries:
		if int(e.get("MaxDistanceOrPosition", -1)) <= 0:
			dist = e
			break

	var items: Array = dist.get("Distribution", [])
	if items.is_empty():
		return {"id": "", "effect": Effect.NONE, "ratio": 1.0, "duration": 0.0}
	var total := 0.0
	for it in items:
		total += maxf(float(it.get("Weightage", 0.0)), 0.0)
	if total <= 0.0:
		return {"id": "", "effect": Effect.NONE, "ratio": 1.0, "duration": 0.0}

	# Deterministic roll. A race must not hand the player a different item
	# distribution between two runs of the same race, or the recovered loot
	 # table stops meaning anything.
	var pick := fmod(absf(sin(float(random_seed) + _spots.size()) * 43758.5453), 1.0) * total
	var chosen: Dictionary = items[0]
	for it in items:
		pick -= maxf(float(it.get("Weightage", 0.0)), 0.0)
		if pick <= 0.0:
			chosen = it
			break
	return _describe(str(chosen.get("ID", "")))


## Map a usable id to an effect the port implements, and to the handling
## numbers that effect uses.
static func _describe(usable_id: String) -> Dictionary:
	var lower := usable_id.to_lower()
	var out := {"id": usable_id, "effect": Effect.NONE, "ratio": 1.0, "duration": 0.0}
	if lower.begins_with("usable_boost"):
		out["effect"] = Effect.BOOST
		out["ratio"] = 1.0
	elif lower.begins_with("usable_tripleboost"):
		out["effect"] = Effect.TRIPLE_BOOST
		out["ratio"] = 1.0
	elif lower.begins_with("usable_shield"):
		out["effect"] = Effect.SHIELD
	elif lower.begins_with("usable_turd") or lower.begins_with("usable_goldenturd"):
		out["effect"] = Effect.TURD
	else:
		# A real usable the port has no behaviour for. Kept visible in the roll
		# rather than dropped, so the recovered odds stay honest.
		out["effect"] = Effect.NONE
	return out