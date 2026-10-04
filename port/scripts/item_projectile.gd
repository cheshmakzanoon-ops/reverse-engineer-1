## PORT-SIDE swept projectile/hazard. Original prefab, mesh, VFX, animation,
## bounce tracking and native ballistics are not reconstructed by this model.
class_name ItemProjectile
extends Node3D
var definition: Dictionary = {}
var shooter: Kart
var system: RaceItems
var direction := Vector3.FORWARD
var age := 0.0
var bounces := 0
var expired := false
var _cursor := -1
var _shape := SphereShape3D.new()
var _radius := 0.65 # PORT-SIDE collision radius; original collider unavailable.

func _ready() -> void:
	_shape.radius = _radius
	var visual := MeshInstance3D.new()
	var mesh := SphereMesh.new()
	mesh.radius = _radius
	mesh.height = _radius * 2.0
	visual.mesh = mesh
	add_child(visual)

func _physics_process(delta: float) -> void:
	if expired or not is_instance_valid(system) or not system.enabled:
		return
	age += delta
	if age >= float(definition["_generalData"]["_lifeTime"]):
		_expire()
		return
	var projectile: Dictionary = definition["_projectileData"]
	var space := get_world_3d().direct_space_state
	var exclude: Array[RID] = []
	var collidable: Dictionary = definition["_collidableData"]
	if is_instance_valid(shooter) and (int(collidable.get("_canSelfHit", 0)) == 0 or age < float(collidable.get("_selfHitInvulnTime", 0.0))):
		exclude.append(shooter.get_rid())
	# Stationary hazards and spawning overlaps need a volume query, not a ray.
	var overlap := PhysicsShapeQueryParameters3D.new()
	overlap.shape = _shape
	overlap.transform = global_transform
	overlap.collision_mask = 2
	overlap.exclude = exclude
	for contact in space.intersect_shape(overlap, 16):
		if contact["collider"] is Kart:
			_hit_kart(contact["collider"])
			return
	_update_homing(delta)
	var speed := float(projectile.get("_speed", 0.0))
	var next := global_position + direction * speed * delta
	# Ground following is a port approximation; no gliding/native throw claim.
	var floor_query := PhysicsRayQueryParameters3D.create(next + Vector3.UP * 3.0, next - Vector3.UP * 7.0, 1)
	var floor_hit := space.intersect_ray(floor_query)
	if not floor_hit.is_empty():
		next.y = floor_hit["position"].y + _radius + float(projectile.get("_floorPerimeterGap", 0.0))
	if global_position.distance_squared_to(next) > 0.000001:
		var query := PhysicsRayQueryParameters3D.create(global_position, next, 3, exclude)
		query.hit_from_inside = true
		var hit := space.intersect_ray(query)
		if not hit.is_empty():
			if hit["collider"] is Kart:
				global_position = hit["position"]
				_hit_kart(hit["collider"])
				return
			bounces += 1
			var normal: Vector3 = hit["normal"]
			if bounces > int(projectile.get("_maxBounceCount", 0)) or normal.length_squared() < 0.5:
				_expire()
				return
			direction = direction.bounce(normal).normalized()
			global_position = hit["position"] + normal * (_radius + 0.01)
			return
	global_position = next

func _update_homing(delta: float) -> void:
	var homing: Dictionary = {}
	var target: Kart
	if definition.has("_placementHomingData"):
		homing = definition["_placementHomingData"]["_homingData"]
		target = system.leader_target(shooter if is_instance_valid(shooter) else null)
	elif definition.has("_proximityHomingData"):
		homing = definition["_proximityHomingData"]["_homingData"]
		var best := float(homing["_trackingRange"])
		for kart in system.karts:
			if not is_instance_valid(kart) or kart == shooter or not kart.driving_enabled:
				continue
			var offset := kart.global_position + Vector3.UP - global_position
			if offset.length() < best and direction.angle_to(offset) <= deg_to_rad(float(homing["_fov"]) * 0.5):
				best = offset.length()
				target = kart
	if homing.is_empty():
		return
	var desired := direction
	if target != null and target.global_position.distance_to(global_position) <= float(homing["_trackingRange"]):
		desired = (target.global_position + Vector3.UP - global_position).normalized()
	elif system.director != null:
		var line := system.director._centreline
		if line.size() > 2:
			var best := INF
			var begin := 0 if _cursor < 0 else _cursor - 3
			var count := line.size() if _cursor < 0 else mini(20, line.size())
			for step in count:
				var index := posmod(begin + step, line.size())
				var distance := line[index].distance_squared_to(global_position)
				if distance < best:
					_cursor = index
					best = distance
			var index := _cursor
			var remaining := float(homing["_waypointLookAhead"])
			for step in line.size():
				remaining -= line[index].distance_to(line[(index + 1) % line.size()])
				index = (index + 1) % line.size()
				if remaining <= 0.0:
					break
			desired = (line[index] + Vector3.UP - global_position).normalized()
	var angle := direction.angle_to(desired)
	if angle > 0.0001:
		direction = direction.slerp(desired, minf(1.0, deg_to_rad(float(homing["_kartMaxSteer"])) * delta / angle)).normalized()

func _hit_kart(kart: Kart) -> void:
	system.hit(kart, definition["_collidableData"]["_kartTriggerEffectParameters"])
	if definition.has("_placementHomingData"):
		var area: Dictionary = definition["_placementHomingData"]["_aoEHitData"]
		for other in system.karts:
			if is_instance_valid(other) and other != kart and other.global_position.distance_to(global_position) < float(area["Radius"]):
				var own_immunity := other == shooter and age < float(definition["_collidableData"].get("_selfHitInvulnTime", 0.0))
				if not own_immunity:
					system.hit(other, area["KartTriggerEffectParameters"])
	_expire()

func _expire() -> void:
	if expired:
		return
	expired = true
	queue_free()
