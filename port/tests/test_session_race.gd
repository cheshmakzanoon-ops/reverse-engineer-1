## Complete physical race -> results -> save -> fresh application instance.
## AI drives the player for this deterministic test; touch is tested separately.
## No checkpoint, lap, progress, finish or result state is directly assigned.
extends SceneTree
const Frontend := preload("res://scenes/frontend.tscn")
var passed := 0
var failed := 0
var app: GameSession
var laps_seen := 0
var finishes := 0

func _init() -> void:
	call_deferred("run")

func check(label: String, value: bool) -> void:
	passed += int(value)
	failed += int(not value)
	print("  ", "ok " if value else "FAIL ", label)

func frames(count: int = 3) -> void:
	for frame in count:
		await process_frame

func run() -> void:
	var path := "user://session-race-%d-%d" % [OS.get_process_id(), Time.get_ticks_usec()]
	app = Frontend.instantiate()
	app.profile_path = path
	root.add_child(app)
	app.show_menu()
	var chosen := app.selection.duplicate(true)
	chosen.laps = 1
	chosen.items = false
	check("physical test selection is accepted", app.set_selection(chosen))
	await app.start_race()
	check("selected race starts through session controller", app.screen == "racing" and app.race != null)
	if app.race == null:
		print("PASS: %d FAIL: %d" % [passed, failed])
		quit(1)
		return
	var race = app.race
	var player: Kart = race.player
	var director: RaceDirector = race.director
	var course_length := director._total_length
	var driver := KartAI.new()
	driver.kart = player
	player.add_child(driver)
	driver.configure(race._pick_ai_definition())
	driver.set_line(race._centreline)
	player.driver = driver
	director.lap_completed.connect(func(kart: Node3D, _lap: int):
		if kart == player:
			laps_seen += 1)
	director.race_finished.connect(func(_results: Array): finishes += 1)
	var finite := true
	var frame_count := 0
	for frame in 15000:
		await physics_frame
		frame_count += 1
		for kart in race._karts:
			finite = finite and kart.position.is_finite() and kart.velocity.is_finite()
		if app.screen == "results":
			break
	check("race reaches results within budget", app.screen == "results" and director.is_finished)
	check("all physics samples remain finite", finite)
	check("player physically completes one ordered lap", laps_seen == 1 and director.is_kart_finished(player))
	check("credited route distance is exactly one full course", is_equal_approx(float(director._state[player].credited), director._total_length))
	check("one actual race-finished signal is received", finishes == 1)
	check("result table contains all six participants", app.last_results.size() == 6)
	check("results world is paused and controls are released", paused and race.touch._fingers.is_empty() and race.player.command.throttle == 0.0)
	var finish_time := -1.0
	var finish_place := -1
	for index in director._results.size():
		if director._results[index].kart == player:
			finish_time = float(director._results[index].time)
			finish_place = index + 1
	check("saved time is an actual positive crossing time", finish_time > 30.0 and finish_place >= 1)
	check("physical race awards exactly one participation record", app.profile.data.stats.races == 1)
	check("wins follow actual finishing place", app.profile.data.stats.wins == int(finish_place == 1))
	check("best time matches the crossing result", is_equal_approx(app.profile.best_time(chosen), finish_time))
	check("result presentation does not retain kart object references", not app.last_results.is_empty() and not app.last_results[0].has("kart"))
	var old_event := app._event_id
	var old_id: int = race.get_instance_id()
	var elapsed: float = director.elapsed
	var revision := app.profile.revision
	app._on_results(director._results, old_id)
	await frames(30)
	check("replayed results do not duplicate a save", app.profile.revision == revision and app.profile.data.stats.races == 1)
	check("results leave the completed clock frozen", director.elapsed == elapsed and finishes == 1)
	var disk := LocalProfile.new(path)
	check("fresh disk reader restores actual result and selection", disk.load_profile() == OK and disk.data.stats.races == 1 and disk.data.selection == chosen and is_equal_approx(disk.best_time(chosen), finish_time))
	var old: WeakRef = weakref(race)
	await app.start_race()
	await frames()
	check("rematch creates a new event and fresh countdown", app.screen == "racing" and app._event_id != old_event and app.race.director.countdown > 2.5)
	check("rematch frees the completed world and result table", old.get_ref() == null and app.last_results.is_empty())
	app._on_results([], old_id)
	check("late callback from completed world cannot finish rematch", app.screen == "racing" and app.profile.data.stats.races == 1)
	app.show_menu()
	check("aborted rematch does not fabricate another result", app.profile.data.stats.races == 1 and not paused)
	app.queue_free()
	await frames()
	app = Frontend.instantiate()
	app.profile_path = path
	root.add_child(app)
	await frames()
	check("new application instance boots with prior progress", app.screen == "title" and app.profile.data.stats.races == 1 and app.selection == chosen)
	check("new application instance preserves best time", is_equal_approx(app.profile.best_time(chosen), finish_time))
	print("SESSION_RACE: frames=%d course=%.3fm time=%.3fs player=%.3fs place=%d" % [frame_count, course_length, elapsed, finish_time, finish_place])
	app.queue_free()
	await frames()
	for slot in 2:
		DirAccess.remove_absolute(ProjectSettings.globalize_path(path + ".%d.json" % slot))
	print("PASS: %d FAIL: %d" % [passed, failed])
	quit(1 if failed else 0)
