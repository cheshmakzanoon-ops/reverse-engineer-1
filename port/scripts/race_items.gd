## Functional PORT-SIDE item models, not recovered native methods or artwork.
## Definition ids, counts, life times, speed/boost/shield/hit tuning are retained.
## Trajectories, target selection and activation timing require reference tests.
class_name RaceItems
extends Node3D
var enabled := true
var director: RaceDirector
var karts: Array[Kart] = []
var pending: Array[Dictionary] = []
var activations := 0
var hits := 0
const MAX_PROJECTILES := 256 # PORT-SIDE memory bound.

func execute(id: String, data: Dictionary, kart: Kart) -> bool:
	if not enabled or not is_instance_valid(kart) or not kart.driving_enabled:
		return false
	if data.has("_boostUsableData"):
		kart.start_item_boost(data["_boostUsableData"])
	elif data.has("_shieldUsableData"):
		kart.start_shield(float(data["_generalData"]["_lifeTime"]), int(data["_shieldUsableData"]["NumHits"]))
	elif data.has("_superShieldUsableData"):
		# PORT-SIDE duration-based shield. Turning modifiers are not yet modelled.
		kart.start_shield(float(data["_generalData"]["_lifeTime"]), -1)
		kart.start_item_boost(data["_superShieldUsableData"])
	elif data.has("_projectileData"):
		if get_child_count() >= MAX_PROJECTILES:
			return false
		var projectile := ItemProjectile.new()
		projectile.definition = data
		projectile.shooter = kart
		projectile.system = self
		projectile.name = "Projectile%d" % activations
		var direction := kart.global_basis.z.normalized()
		if int(data["_projectileData"].get("_primaryFireForwards", 1)) == 0:
			direction = -direction
		projectile.position = to_local(kart.global_position + Vector3.UP * 1.2 + direction * 3.0)
		projectile.direction = direction
		add_child(projectile)
	elif data.has("_satelliteUsableData"):
		pending.append({"time": float(data["_satelliteUsableData"]["EffectDelayTime"]), "source": kart, "data": data})
	else:
		return false # Keep the held id/charges; never silently substitute.
	activations += 1
	return true

func _physics_process(delta: float) -> void:
	if not enabled:
		return
	for index in range(pending.size() - 1, -1, -1):
		var event := pending[index]
		event["time"] = float(event["time"]) - delta
		if float(event["time"]) > 0.0:
			continue
		pending.remove_at(index)
		for kart in karts:
			if is_instance_valid(kart) and kart != event["source"]:
				# Default recovered effect; distance-specific variants and stagger
				# animation timing still need native behavior evidence.
				hit(kart, event["data"]["_kartTriggerEffectParametersDefault"])

func hit(kart: Kart, parameters: Dictionary) -> bool:
	if not is_instance_valid(kart) or not kart.driving_enabled:
		return false
	var accepted := kart.apply_hit(parameters)
	if accepted:
		hits += 1
	return accepted

func leader_target(shooter: Kart) -> Kart:
	var target: Kart
	for kart in karts:
		if not is_instance_valid(kart) or kart == shooter or not kart.driving_enabled:
			continue
		if target == null or (director != null and director.position_of(kart) < director.position_of(target)):
			target = kart
	return target
