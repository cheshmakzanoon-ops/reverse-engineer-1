## Real Area3D contact, cooldown, claim arbitration and inventory ownership.
extends SceneTree
var grants := 0
var passed := 0
var failed := 0
func _init() -> void:
	call_deferred("run")
func check(value: bool, label: String) -> void:
	passed += int(value)
	failed += int(not value)
	print("  ", "ok " if value else "FAIL ", label)
func run() -> void:
	var scene := Node3D.new()
	root.add_child(scene)
	var spawner := PickupSpawner.new()
	scene.add_child(spawner)
	spawner.build("map_racearlenspeedway")
	spawner.kart_picked_up.connect(func(_kart, _id, _effect): grants += 1)
	var kart := make_kart(scene, spawner._spots[0].area.position - Vector3.UP)
	for frame in 6:
		await physics_frame
	var spot := spawner._spots[0]
	check(grants == 1 and not kart.inventory.is_empty(), "actual body overlap grants exactly one held pickup")
	check(not kart.is_boosting and kart.shield_time == 0.0, "collection never auto-activates the held item")
	check(not spot.available and not spot.area.visible, "a claimed box disappears on its cooldown")
	var id := kart.inventory.usable_id
	check(not spawner.collect(spot.area, kart) and grants == 1, "repeat contact cannot double-grant")
	var rival := make_kart(scene, kart.position + Vector3.RIGHT * 100.0)
	check(not spawner.collect(spot.area, rival) and rival.inventory.is_empty(), "a second kart cannot claim the same box during cooldown")
	spawner._physics_process(spot.respawn_time + 0.01)
	check(spot.available and spot.area.visible, "the box respawns even while the first kart holds its item")
	check(kart.inventory.usable_id == id and grants == 1, "full inventory is not overwritten on box respawn")
	spawner.enabled = false
	check(not spawner.collect(spot.area, rival), "disabled race pickups reject collection")
	spawner.enabled = true
	rival.set_driving_enabled(false)
	check(not spawner.collect(spot.area, rival), "countdown or finished karts cannot collect")
	rival.set_driving_enabled(true)
	var scenery := Node3D.new()
	scene.add_child(scenery)
	check(not spawner.collect(spot.area, scenery), "non-kart contacts cannot consume a box")
	# Move the rival into the active trigger: no direct grant or signal injection.
	rival.position = kart.position
	for frame in 6:
		await physics_frame
	check(grants == 2 and not rival.inventory.is_empty(), "the respawned box grants to a new physical contact")
	check(kart.inventory.usable_id == id, "a rival's collection leaves the player's held id unchanged")
	scene.queue_free()
	await process_frame
	print("PASS: %d FAIL: %d" % [passed, failed])
	quit(1 if failed else 0)
func make_kart(parent: Node3D, position: Vector3) -> Kart:
	var kart := Kart.new()
	kart.collision_layer = 2
	kart.collision_mask = 1
	var collider := CollisionShape3D.new()
	var capsule := CapsuleShape3D.new()
	capsule.height = 2.4
	capsule.radius = 1.1
	collider.shape = capsule
	collider.position.y = 1.2
	kart.add_child(collider)
	kart.position = position
	parent.add_child(kart)
	kart.set_physics_process(false)
	return kart
