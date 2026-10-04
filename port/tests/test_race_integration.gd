## Runs the ACTUAL kart physics around three complete recovered-route laps.
## The player's test driver is AI; input/touch ownership is tested separately.
## Never writes lap, next_gate, credited distance, progress or finish state.
extends SceneTree
const DT := 1.0 / 60.0
var passed := 0
var failed := 0
var finished_events := 0
var player_laps := 0

func _init() -> void:
	call_deferred("run")

func check(label: String, value: bool) -> void:
	if value:
		passed += 1
	else:
		failed += 1
	print("  ", "ok " if value else "FAIL ", label)

func with_items() -> bool:
	return false

func run() -> void:
	var race = load("res://scenes/main.tscn").instantiate()
	race.laps = 3
	race.items_enabled = with_items()
	root.add_child(race)
	await process_frame
	var director: RaceDirector = race.director
	# The clean physics baseline has no items. The combat subclass enables
	# items and allows every AI to finish despite hit-induced time gaps;
	# production's short DNF grace is exercised separately in race_rules.
	if with_items():
		director.finish_grace_seconds = 120.0
	var player: Kart = race.player
	var initial_position := player.global_position
	check("six independent karts registered", director.kart_count() == 6 and race._karts.size() == 6)
	check("course spans the recovered route", director._total_length > 1400.0 and director._gate_positions.size() > 150)
	check("HUD observes the actual player and director", race.hud.player == player and race.hud.director == director)
	check("pickup locations come from the selected map", race.pickups.spot_count() == 52)
	director.race_finished.connect(func(_results: Array): finished_events += 1)
	director.lap_completed.connect(func(k: Node3D, _lap: int):
		if k == player:
			player_laps += 1)
	var driver := KartAI.new()
	driver.kart = player
	player.add_child(driver)
	driver.configure(race._pick_ai_definition())
	driver.set_line(race._centreline)
	player.driver = driver

	# Countdown is exercised normally, without forcing the race clock.
	for frame in 120:
		await physics_frame
	check("countdown prevents horizontal movement", Vector2(player.position.x, player.position.z).distance_to(Vector2(initial_position.x, initial_position.z)) < 0.05)
	check("countdown prevents checkpoint credit", float(director._state[player]["progress"]) == 0.0)
	var simulated_frames := 120
	var finite_motion := true
	for frame in 30000:
		await physics_frame
		simulated_frames += 1
		for kart in race._karts:
			finite_motion = finite_motion and kart.position.is_finite() and kart.velocity.is_finite()
		if director.is_finished:
			break
	check("race ends within the simulation budget", director.is_finished)
	check("all physics samples remain finite", finite_motion)
	check("player physically crosses three complete laps", director.is_kart_finished(player) and player_laps == 3)
	check("player credited distance equals three course lengths", is_equal_approx(float(director._state[player]["credited"]), director._total_length * 3.0))
	check("finish signal emitted once", finished_events == 1)
	check("one result per participant", director._results.size() == race._karts.size())
	var last_time := -1.0
	var all_finished := true
	var ordered := true
	for result in director._results:
		all_finished = all_finished and bool(result["finished"]) and int(result["lap"]) == 3
		ordered = ordered and float(result["time"]) >= last_time
		last_time = float(result["time"])
	check("every AI also completes all three laps", all_finished and director._results.size() == 6)
	check("finish order is sorted by crossing time", ordered and last_time > 200.0)
	# Finished karts must not block the finish gate or keep driving.
	for frame in 5:
		await physics_frame
	check("finished player has neutral controls", not player.driving_enabled and player.command.throttle == 0.0)
	check("finished player cannot block other racers", player.collision_layer == 0 and player.collision_mask == 1)
	check("HUD displays the results", race.hud._results_panel.visible)
	var final_position := player.position
	var final_elapsed := director.elapsed
	for frame in 120:
		await physics_frame
	check("completed race clock is frozen", director.elapsed == final_elapsed)
	check("finish cannot be emitted twice", finished_events == 1)
	check("finished kart remains grounded", player.position.distance_to(final_position) < 0.2)
	if with_items():
		check("AI actually activates items during full races", race.items.activations > 10)
		check("item impacts affect karts during full races", race.items.hits > 0)
		check("projectile allocation stays bounded", race.items.get_child_count() < RaceItems.MAX_PROJECTILES)
		print("COMBAT: activations=%d hits=%d live_projectiles=%d" % [race.items.activations, race.items.hits, race.items.get_child_count()])
	print("SIMULATION: frames=%d time=%.3fs course=%.3fm gates=%d laps=%d field=%d" % [simulated_frames, director.elapsed, director._total_length, director._gate_positions.size(), director.lap_count, director.kart_count()])
	for result in director._results:
		print("RESULT: %s laps=%d finished=%s time=%.3f" % [result["name"], result["lap"], result["finished"], result["time"]])
	race.queue_free()
	await process_frame
	print("PASS: %d FAIL: %d" % [passed, failed])
	quit(1 if failed else 0)
