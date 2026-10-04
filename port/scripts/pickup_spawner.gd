## Recovered spots, box references and weights; PORT-SIDE deterministic rolls.
## Race distance bands use validated distance behind the leader. That heuristic
## and collision timing are not claimed to reproduce the native implementation.
class_name PickupSpawner
extends Node3D
const GameDB := preload("res://scripts/data/game_db.gd")
const Tracks := preload("res://scripts/data/tracks.gd")
enum Effect { BOOST, TRIPLE_BOOST, SHIELD, TURD, NONE, PROJECTILE, SUPER_SHIELD, SATELLITE }
signal kart_picked_up(kart: Node3D, usable_id: String, effect: Effect)
@export var track_id := ""
@export var kart_radius := 1.6 # PORT-SIDE trigger radius, not original geometry.
@export var random_seed := 20202
var enabled := true
var director: RaceDirector
var rng := RandomNumberGenerator.new()
class Spot extends RefCounted:
	var area: Area3D
	var position: Vector3
	var box_id: String
	var respawn_time: float
	var timer := 0.0
	var available := true
var _spots: Array[Spot] = []

func build(id: String) -> void:
	track_id = id
	_spots.clear()
	rng.seed = random_seed
	for child in get_children():
		remove_child(child)
		child.queue_free()
	var material := StandardMaterial3D.new()
	material.albedo_color = Color(0.95, 0.75, 0.2, 0.7)
	material.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	for data in Tracks.pickup_spots(id):
		var spot := Spot.new()
		spot.position = data["pos"]
		spot.box_id = str(data["box"])
		spot.respawn_time = float(data["respawn"])
		var area := Area3D.new()
		area.collision_layer = 0
		area.collision_mask = 2 # kart bodies, not the road or other pickups
		area.monitorable = false
		var shape := SphereShape3D.new()
		shape.radius = kart_radius
		var collider := CollisionShape3D.new()
		collider.shape = shape
		area.add_child(collider)
		var visual := MeshInstance3D.new()
		var mesh := BoxMesh.new() # Explicit engineering placeholder.
		mesh.size = Vector3.ONE * 1.7
		visual.mesh = mesh
		visual.material_override = material
		area.add_child(visual)
		area.position = spot.position + Vector3.UP * float(data["height"])
		spot.area = area
		_spots.append(spot)
		area.body_entered.connect(func(body: Node3D): collect(area, body))
		add_child(area)

func spot_count() -> int:
	return _spots.size()

func _physics_process(delta: float) -> void:
	if not enabled:
		return
	for spot in _spots:
		if spot.available or spot.respawn_time <= 0.0:
			continue
		spot.timer = maxf(spot.timer - delta, 0.0)
		if spot.timer == 0.0:
			spot.available = true
			spot.area.visible = true
			# A stationary kart present at respawn is a real overlap too.
			for body in spot.area.get_overlapping_bodies():
				collect(spot.area, body)

func collect(area: Area3D, body: Node3D) -> bool:
	if not enabled or not body is Kart or not body.driving_enabled or not body.inventory.is_empty():
		return false
	if director != null and (not director._state.has(body) or director.is_kart_finished(body)):
		return false
	var spot := _find(area)
	if spot == null or not spot.available:
		return false
	var gap := director.distance_behind_leader(body) if director != null else 0.0
	var roll := _roll(spot.box_id, body.driver is KartAI, gap)
	var id := str(roll.get("id", ""))
	if id.is_empty():
		push_error("PickupSpawner: unresolved table/usable for " + spot.box_id)
		return false
	if not body.inventory.grant(id):
		# PORT-SIDE: a race cap/cooldown rejection leaves the box intact; it
		# does not silently reroll, erase the item, or change recovered weights.
		return false
	spot.available = false # Synchronous claim prevents same-tick double grants.
	spot.timer = spot.respawn_time
	spot.area.visible = false
	kart_picked_up.emit(body, id, int(roll["effect"]))
	return true

func _find(area: Area3D) -> Spot:
	for spot in _spots:
		if spot.area == area:
			return spot
	return null

func distribution(box_id: String, for_ai: bool, gap: float) -> Array:
	var box := GameDB.lookup(box_id)
	var table := GameDB.lookup(str(box.get("_aiTable" if for_ai else "_playerTable", "")))
	var bands: Array = table.get("_pickupDistributions", [])
	if bands.is_empty():
		return []
	var ordered := bands.duplicate()
	ordered.sort_custom(func(a, b): return float(a["MaxDistanceOrPosition"]) < float(b["MaxDistanceOrPosition"]))
	for band in ordered:
		if gap <= float(band["MaxDistanceOrPosition"]):
			return band["Distribution"]
	return ordered[-1]["Distribution"]

func _roll(box_id: String, for_ai: bool = false, gap: float = 0.0) -> Dictionary:
	return _describe(weighted_id(distribution(box_id, for_ai, gap), rng.randf()))

static func weighted_id(entries: Array, fraction: float) -> String:
	if not is_finite(fraction):
		return ""
	var total := 0.0
	for entry in entries:
		total += maxf(float(entry.get("Weightage", 0)), 0.0)
	if total <= 0.0:
		return ""
	var target := clampf(fraction, 0.0, 0.999999999) * total
	for entry in entries:
		var weight := maxf(float(entry.get("Weightage", 0)), 0.0)
		if weight <= 0.0:
			continue
		if target < weight:
			return str(entry.get("ID", ""))
		target -= weight
	return ""

static func _describe(id: String) -> Dictionary:
	var data := GameDB.lookup(id)
	var effect := Effect.NONE
	if data.has("_boostUsableData"):
		effect = Effect.TRIPLE_BOOST if int(data["_generalData"]["_numberOfUses"]) > 1 else Effect.BOOST
	elif data.has("_shieldUsableData"):
		effect = Effect.SHIELD
	elif data.has("_superShieldUsableData"):
		effect = Effect.SUPER_SHIELD
	elif data.has("_goldenTurdData"):
		effect = Effect.TURD
	elif data.has("_projectileData"):
		effect = Effect.PROJECTILE
	elif data.has("_satelliteUsableData"):
		effect = Effect.SATELLITE
	return {"id": str(data.get("_id", "")), "effect": effect}
