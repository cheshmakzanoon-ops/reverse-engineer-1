## Real inventory/effect behavior. Source tuning is read, never rewritten.
extends SceneTree
const DB := preload("res://scripts/data/game_db.gd")
var passed := 0
var failed := 0
func _init() -> void:
	call_deferred("run")
func check(value: bool, message: String) -> void:
	passed += int(value)
	failed += int(not value)
	print("  ", "ok " if value else "FAIL ", message)
func run() -> void:
	var inventory := KartInventory.new()
	var other := KartInventory.new()
	check(not inventory.grant("not-a-definition"), "missing definition is rejected")
	check(inventory.is_empty(), "invalid grant leaves the slot empty")
	check(inventory.grant("Usable_Boost_X3"), "recovered triple boost grants three uses")
	check(inventory.charges == 3 and other.is_empty(), "inventories and charge counts are isolated")
	check(not inventory.grant("Usable_NormalShield_X1"), "a full slot cannot be overwritten")
	var deny := func(_id: String, _data: Dictionary): return false
	var accept := func(_id: String, _data: Dictionary): return true
	check(not inventory.activate(accept), "equipping timer blocks premature activation")
	inventory.tick(0.5)
	check(not inventory.activate(deny) and inventory.charges == 3, "unavailable effect preserves charges and error")
	check(not inventory.last_error.is_empty(), "unavailable effect is visible rather than a silent no-op")
	check(inventory.activate(accept) and inventory.charges == 2, "first activation spends exactly one charge")
	check(not inventory.activate(accept) and inventory.charges == 2, "rapid repeated use obeys recovered re-equip delay")
	inventory.tick(0.31)
	check(inventory.activate(accept) and inventory.charges == 1, "second activation spends the second charge")
	inventory.tick(0.31)
	check(inventory.activate(accept) and inventory.is_empty(), "third activation empties the slot")
	check(not inventory.activate(accept), "empty inventory cannot activate")
	inventory.clear_slot()
	check(inventory.grant("Usable_Satellite_X1"), "limited recovered item can be granted initially")
	inventory.clear_slot()
	check(not inventory.grant("Usable_Satellite_X1"), "clearing the slot cannot bypass pickup cooldown")
	inventory.tick(60.0)
	check(inventory.grant("Usable_Satellite_X1"), "pickup cooldown expires using the inventory clock")
	inventory.clear_slot()
	inventory.tick(60.0)
	check(not inventory.grant("Usable_Satellite_X1"), "per-race total pickup limit survives clears")

	var spawner := PickupSpawner.new()
	root.add_child(spawner)
	spawner.build("map_racearlenspeedway")
	var rolls: Array[String] = []
	for i in 80:
		rolls.append(spawner._roll("PickupBox_Race_Default")["id"])
	var distinct := {}
	for id in rolls:
		distinct[id] = true
	check(distinct.size() > 2, "advancing seeded RNG does not return one constant item")
	spawner.rng.seed = spawner.random_seed
	var repeatable := true
	for id in rolls:
		repeatable = repeatable and id == spawner._roll("PickupBox_Race_Default")["id"]
	check(repeatable, "the same seed reproduces the full roll sequence")
	var player_distribution := spawner.distribution("PickupBox_Race_Default", false, 0)
	var ai_distribution := spawner.distribution("PickupBox_Race_Default", true, 0)
	check(player_distribution[0]["Weightage"] == 35 and ai_distribution[0]["Weightage"] == 25, "player and AI select distinct recovered tables")
	check(spawner.distribution("PickupBox_Race_Default", false, 61)[0]["Weightage"] == 10, "distance bands select the first containing threshold")
	var weights := [{"ID": "zero", "Weightage": 0}, {"ID": "A", "Weightage": 2}, {"ID": "B", "Weightage": 3}]
	check(PickupSpawner.weighted_id(weights, 0) == "A", "zero weight is never selected at the zero boundary")
	check(PickupSpawner.weighted_id(weights, 0.4) == "B", "weight normalization handles totals other than 100")
	check(PickupSpawner.weighted_id(weights, 1.0) == "B", "upper random boundary stays in the last positive bucket")
	check(PickupSpawner._describe("Usable_Boost_X3")["effect"] == PickupSpawner.Effect.TRIPLE_BOOST, "triple boost is classified from count rather than a nonexistent name")
	check(PickupSpawner._describe("Usable_NormalShield_X1")["effect"] == PickupSpawner.Effect.SHIELD, "normal shield resolves by recovered definition shape")
	spawner.queue_free()
	await process_frame

	var world := Node3D.new()
	root.add_child(world)
	var system := RaceItems.new()
	world.add_child(system)
	var kart := make_kart(world, Vector3.ZERO)
	var victim := make_kart(world, Vector3(0, 0, 14))
	system.karts.assign([kart, victim])
	kart.item_executor = func(id: String, data: Dictionary): return system.execute(id, data, kart)
	kart.inventory.grant("Usable_Boost_X1")
	kart.command.use_item_requested = true
	kart._read_input()
	check(kart.inventory.is_empty() and system.activations == 1, "the real command path activates and consumes a held item")
	check(kart.is_boosting and is_equal_approx(kart._item_boost_timer, 1.8), "boost uses recovered duration rather than the drift timer")
	check(is_equal_approx(kart._item_boost_acceleration, 2.1) and is_equal_approx(kart.velocity.z, 10.0), "recovered boost ratio and speed offset reach this kart")
	check(not victim.is_boosting and victim.velocity == Vector3.ZERO, "boost never modifies a rival")
	kart._read_input()
	check(system.activations == 1, "one item command cannot pay out twice")
	kart._tick_timers(2.0)
	check(not kart.is_boosting, "boost expires on simulation time")
	var hit: Dictionary = DB.lookup("Usable_GoldenTurd_X1")["_collidableData"]["_kartTriggerEffectParameters"]
	check(system.execute("Usable_NormalShield_X1", DB.lookup("Usable_NormalShield_X1"), kart), "shield activates through the shared effect executor")
	check(not kart.apply_hit(hit) and not kart.lost_control and kart.shield_hits == 0, "one-hit shield consumes its hit without a spinout")
	check(kart.apply_hit(hit) and kart.lost_control, "the next unshielded hit applies recovered spinout")
	check(is_equal_approx(kart._lost_control_timer, 1.5) and is_equal_approx(kart.invulnerable_time, 2.0), "hit duration and invulnerability match the usable data")
	check(not kart.apply_hit(hit), "invulnerability rejects repeated hits")
	kart._tick_timers(1.6)
	check(not kart.lost_control and not kart.apply_hit(hit), "control returns before the invulnerability window ends")
	kart._tick_timers(0.5)
	check(kart.apply_hit(hit), "hits become possible after invulnerability expires")
	kart.respawn_at(Transform3D.IDENTITY)
	check(not kart.lost_control and kart.invulnerable_time == 0.0 and kart.inventory.is_empty(), "recovery clears transient effects and held inventory")
	kart.velocity = Vector3.ZERO
	check(system.execute("Usable_SuperShield_X1", DB.lookup("Usable_SuperShield_X1"), kart), "super shield activates with a recovered boost")
	check(kart.shield_time == 10.0 and kart.is_boosting and not kart.apply_hit(hit), "super shield protects for its duration")
	kart.respawn_at(Transform3D.IDENTITY)
	check(system.execute("Usable_Satellite_X1", DB.lookup("Usable_Satellite_X1"), kart), "satellite queues a delayed effect")
	system._physics_process(2.2)
	check(not victim.lost_control, "satellite does not hit before its recovered delay")
	system._physics_process(0.11)
	check(victim.lost_control and not kart.lost_control and system.pending.is_empty(), "satellite hits rivals once and excludes its owner")
	victim.respawn_at(Transform3D(Basis.IDENTITY, Vector3(0, 0, 14)))
	await physics_frame
	await physics_frame
	check(system.execute("Usable_Race_Clam_X1", DB.lookup("Usable_Race_Clam_X1"), kart), "ricochet projectile launches into the actual physics world")
	for frame in 30:
		await physics_frame
	check(victim.lost_control, "swept projectile collides with and affects the rival")
	check(system.get_child_count() == 0, "hit projectile is removed rather than leaking")
	victim.respawn_at(Transform3D(Basis.IDENTITY, Vector3(0, 0, -3)))
	await physics_frame
	check(system.execute("Usable_GoldenTurd_X1", DB.lookup("Usable_GoldenTurd_X1"), kart), "rear hazard spawns behind its owner")
	for frame in 4:
		await physics_frame
	check(victim.lost_control and not kart.lost_control, "stationary hazard uses volume overlap and owner immunity")
	victim.respawn_at(Transform3D(Basis.IDENTITY, Vector3(0, 0, 14)))
	await physics_frame
	check(system.execute("Usable_Race_Sheldon_X1", DB.lookup("Usable_Race_Sheldon_X1"), kart), "homing projectile launches")
	for frame in 30:
		await physics_frame
	check(victim.lost_control and system.get_child_count() == 0, "homing projectile reaches and hits a real target")
	victim.respawn_at(Transform3D(Basis.IDENTITY, Vector3(0, 0, 14)))
	await physics_frame
	check(system.execute("Usable_Race_CulturePod_X1", DB.lookup("Usable_Race_CulturePod_X1"), kart), "placement-homing projectile launches")
	for frame in 30:
		await physics_frame
	check(victim.lost_control, "placement projectile impacts the selected opponent")
	check(not system.execute("unsupported", {}, kart), "unsupported effects return failure, never silent success")
	world.queue_free()
	await process_frame
	print("PASS: %d FAIL: %d" % [passed, failed])
	quit(1 if failed else 0)

func make_kart(parent: Node3D, position: Vector3) -> Kart:
	var kart := Kart.new()
	kart.collision_layer = 2
	kart.collision_mask = 1
	var collider := CollisionShape3D.new()
	var shape := CapsuleShape3D.new()
	shape.radius = 1.1
	shape.height = 2.4
	collider.shape = shape
	collider.position.y = 1.2
	kart.add_child(collider)
	kart.position = position
	parent.add_child(kart)
	kart.set_physics_process(false)
	return kart
