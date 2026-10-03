## Behavioral fall recovery; no race-progress fields are written by this test.
extends SceneTree
var passed := 0
var failed := 0
func _init() -> void:
	call_deferred("run")
func check(value: bool, label: String) -> void:
	if value:
		passed += 1
	else:
		failed += 1
	print("  ", "ok " if value else "FAIL ", label)
func run() -> void:
	var race = load("res://scenes/main.tscn").instantiate()
	race.field_size = 1
	root.add_child(race)
	await process_frame
	var player: Kart = race.player
	var expected: Transform3D = race.director.recovery_transform(player)
	var next: int = race.director._state[player]["next_gate"]
	player.global_position -= Vector3.UP * 50.0
	player.velocity = Vector3(5, -100, 10)
	for frame in 6:
		await physics_frame
	check(player.global_position.distance_to(expected.origin) < 2.0, "fall is recovered to a safe validated position")
	check(absf(player.velocity.y) < 10.0, "recovery clears falling velocity")
	check(int(race.director._state[player]["next_gate"]) == next, "fall does not credit a checkpoint")
	check(race.director.lap_of(player) == 1, "fall does not credit a lap")
	race.queue_free()
	await process_frame
	print("PASS: %d FAIL: %d" % [passed, failed])
	quit(1 if failed else 0)
