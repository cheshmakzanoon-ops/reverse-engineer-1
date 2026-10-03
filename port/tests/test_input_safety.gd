## Behavioral regressions for the original global-input and heading defects.
extends SceneTree

var passed := 0
var failed := 0
const ACTIONS := ["accelerate", "brake", "steer_left", "steer_right", "handbrake"]

func _init() -> void:
	call_deferred("run")

func check(label: String, condition: bool) -> void:
	if condition:
		passed += 1
		print("  ok   ", label)
	else:
		failed += 1
		print("  FAIL ", label)

func run() -> void:
	var kart := Kart.new()
	root.add_child(kart)
	kart.set_physics_process(false)
	var ai := KartAI.new()
	ai.kart = kart
	kart.add_child(ai)
	ai.set_physics_process(false)
	for action in ACTIONS:
		Input.action_release(action)
	ai._drive(0.25)
	var untouched := true
	for action in ACTIONS:
		untouched = untouched and not Input.is_action_pressed(action)
	check("AI never writes the global player actions", untouched)
	Input.action_press("accelerate", 0.6)
	ai.stop()
	check("stopping an AI preserves the player's held accelerator",
		is_equal_approx(Input.get_action_strength("accelerate"), 0.6))
	kart._read_input()
	check("a stopped AI does not read the player's throttle", is_zero_approx(kart._throttle))
	for action in ACTIONS:
		Input.action_release(action)

	kart.global_basis = Basis(Vector3.UP, 0.8)
	kart.velocity = kart.global_basis.z * 20.0
	kart._update_speed()
	kart._steer_input = 0.3
	kart._apply_steering(1.0 / 60.0)
	check("steering rotates velocity only by the yaw delta",
		absf(kart.velocity.dot(kart.global_basis.z) - 20.0) < 0.001)
	kart.velocity = -kart.global_basis.z * 2.0
	kart._update_speed()
	kart._throttle = 1.0
	kart._apply_acceleration(1.0 / 60.0)
	check("forward throttle brakes reverse motion instead of accelerating backward",
		kart.velocity.dot(kart.global_basis.z) > -2.0)
	kart.queue_free()
	await process_frame

	var race: Node3D = load("res://scenes/main.tscn").instantiate()
	race.mode = "race150"
	root.add_child(race)
	var start: Transform3D = race.player.global_transform
	await process_frame
	var direction: Vector3 = race._centreline[1] - race._centreline[0]
	direction.y = 0.0
	check("player faces forward along the recovered route",
		race.player.global_basis.z.dot(direction.normalized()) > 0.99)
	check("player receives the selected handling profile before startup",
		race.player.handling == HandlingProfile.for_mode("race150"))
	check("HUD is bound to the actual race and player",
		race.hud.director == race.director and race.hud.player == race.player)
	race.player.global_position += Vector3(50, 0, 50)
	race.player.respawn()
	check("fallback respawn restores the grid transform, not the world origin",
		race.player.global_position.distance_to(start.origin) < 0.01)
	race.queue_free()
	await process_frame
	print("PASS: %d   FAIL: %d" % [passed, failed])
	quit(1 if failed else 0)
