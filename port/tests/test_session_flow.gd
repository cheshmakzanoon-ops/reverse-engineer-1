## Synthetic Godot input and lifecycle tests, not physical Android verification.
extends SceneTree
const Frontend := preload("res://scenes/frontend.tscn")
var passed := 0
var failed := 0
var app: GameSession
var profile_path := ""

func _init() -> void:
	call_deferred("run")

func check(label: String, value: bool) -> void:
	passed += int(value)
	failed += int(not value)
	print("  ", "ok " if value else "FAIL ", label)

func frames(count: int = 3) -> void:
	for frame in count:
		await process_frame

func capture(id: String) -> void:
	var directory := OS.get_environment("KART_SCREENSHOTS")
	if directory.is_empty():
		return
	await frames()
	await RenderingServer.frame_post_draw
	var image := root.get_texture().get_image()
	if image == null or image.is_empty() or DirAccess.make_dir_recursive_absolute(directory) != OK or image.save_png(directory.path_join(id + ".png")) != OK:
		push_error("UI screenshot capture failed: " + id)

func control(id: String) -> Control:
	return app.find_child(id, true, false) as Control

func touch_at(position: Vector2, pressed: bool, index: int = 0) -> void:
	var event := InputEventScreenTouch.new()
	event.index = index
	event.position = position
	event.pressed = pressed
	Input.parse_input_event(event)
	Input.flush_buffered_events()

func tap(id: String, finger: int = 0) -> void:
	await frames()
	var button := control(id)
	if button == null:
		check("touch target exists: " + id, false)
		return
	var ancestor := button.get_parent()
	while ancestor != null:
		if ancestor is ScrollContainer:
			ancestor.ensure_control_visible(button)
		ancestor = ancestor.get_parent()
	await frames()
	var point := button.get_global_rect().get_center()
	check("touch target is inside viewport: " + id, root.get_visible_rect().has_point(point))
	touch_at(point, true, finger)
	await frames(1)
	touch_at(point, false, finger)
	await frames()

func clean_profile() -> void:
	for slot in 2:
		var path := ProjectSettings.globalize_path(profile_path + ".%d.json" % slot)
		if FileAccess.file_exists(path):
			DirAccess.remove_absolute(path)

func finish() -> void:
	paused = false
	for action in ["accelerate", "brake", "steer_left", "steer_right", "handbrake", "reset_kart", "use_item"]:
		Input.action_release(action)
	if is_instance_valid(app):
		app.queue_free()
	await frames()
	clean_profile()
	print("PASS: %d FAIL: %d" % [passed, failed])
	quit(1 if failed else 0)

func run() -> void:
	if DisplayServer.get_name() == "headless":
		check("UI events require a window: use xvfb-run with --audio-driver Dummy, not --headless", false)
		await finish()
		return
	profile_path = "user://session-flow-%d-%d" % [OS.get_process_id(), Time.get_ticks_usec()]
	app = Frontend.instantiate()
	app.profile_path = profile_path
	root.add_child(app)
	await frames()
	check("boot presents title without starting a race", app.screen == "title" and app.race == null)
	check("project boots front end rather than a debug race", ProjectSettings.get_setting("application/run/main_scene") == "res://scenes/frontend.tscn")
	await capture("title-16x9")
	await tap("ContinueButton")
	check("screen touch reaches main menu", app.screen == "menu")
	if app.screen != "menu":
		await finish()
		return
	await capture("menu-16x9")
	await tap("SettingsButton")
	await capture("settings-16x9")
	check("settings is navigable by touch", app.screen == "settings")
	check("six audio buses exist independently", AudioServer.bus_count >= 6 and AudioServer.get_bus_index("Engine") > 0)
	control("MusicVolume").value = 0.35
	check("music volume applies to its bus", is_equal_approx(db_to_linear(AudioServer.get_bus_volume_db(AudioServer.get_bus_index("Music"))), 0.35))
	await tap("MuteToggle")
	check("mute affects the master bus", app.settings.muted and AudioServer.is_bus_mute(0))
	await tap("SaveSettingsButton")
	check("save settings returns to menu", app.screen == "menu")
	var loaded := LocalProfile.new(profile_path)
	check("settings survive a fresh profile load", loaded.load_profile() == OK and loaded.data.settings.muted and is_equal_approx(float(loaded.data.settings.volumes.music), 0.35))
	await tap("SettingsButton")
	control("MusicVolume").value = 0.70
	app.back()
	check("back cancels unsaved settings and restores audio", is_equal_approx(float(app.settings.volumes.music), 0.35))
	# Persist unmuted settings before testing lifecycle-specific temporary mute.
	await tap("SettingsButton")
	await tap("MuteToggle")
	await tap("SaveSettingsButton")
	check("user can unmute again", not AudioServer.is_bus_mute(0))
	await tap("QuickRaceButton")
	check("race setup reached by touch", app.screen == "selection")
	await capture("selection-16x9")
	root.size = Vector2i(1600, 720)
	await frames()
	check("widescreen safe area stays within viewport", root.get_visible_rect().encloses(app.safe_area()))
	await capture("selection-wide")
	root.size = Vector2i(960, 720)
	await frames()
	check("tablet safe area stays within viewport", root.get_visible_rect().encloses(app.safe_area()))
	await capture("selection-tablet")
	root.size = Vector2i(1280, 720)
	await frames()
	var track_choice: OptionButton = control("TrackChoice")
	var battle_offered := false
	for index in track_choice.item_count:
		battle_offered = battle_offered or str(track_choice.get_item_metadata(index)).contains("battle")
	check("battle arenas are not offered as race circuits", not battle_offered and track_choice.item_count == LocalProfile.race_tracks().size())
	var chosen := app.selection.duplicate(true)
	chosen.laps = 1
	chosen.items = true
	check("valid one-lap selection accepted", app.set_selection(chosen))
	var bad := chosen.duplicate(true)
	bad.track_id = "not-a-track"
	check("invalid selection cannot silently fall back", not app.set_selection(bad) and app.selection == chosen)
	app.show_selection()
	await tap("StartRaceButton")
	check("touch starts the selected race", app.screen == "racing" and is_instance_valid(app.race))
	if not is_instance_valid(app.race):
		await finish()
		return
	var race = app.race
	check("race selection reaches director and kart field", race.director.lap_count == 1 and race.director.kart_count() == 6 and race.track_id == chosen.track_id)
	check("world is pausable while UI remains live", race.process_mode == Node.PROCESS_MODE_PAUSABLE and app.process_mode == Node.PROCESS_MODE_ALWAYS)
	check("HUD speed does not overlap rank", not race.hud._speed_label.get_global_rect().intersects(race.hud._position_label.get_global_rect()))
	check("HUD speed stays in the lower safe area", race.hud._speed_label.position.y > root.get_visible_rect().size.y * 0.8)
	await capture("countdown-16x9")
	var first_id: int = race.get_instance_id()
	app.start_race()
	check("repeated start while racing does not duplicate the world", app.race.get_instance_id() == first_id)
	await tap("PauseButton")
	var countdown: float = race.director.countdown
	await frames(30)
	check("pause during countdown freezes it", app.screen == "paused" and paused and race.director.countdown == countdown)
	await tap("ResumeButton")
	for frame in 220:
		await physics_frame
	check("countdown resumes and enables driving", app.screen == "racing" and race.director.countdown <= 0 and race.player.driving_enabled)
	app.settings.touch_controls = false
	app.apply_settings()
	# Real screen-touch event owns a finger in the actual race control layer.
	touch_at(race.touch.control_center("accelerate"), true, 0)
	for frame in 60:
		await physics_frame
	check("hidden-control preference remains respected after a touch", not race.touch.visible)
	app.settings.touch_controls = true
	app.apply_settings()
	check("touch accelerator moves the player", race.touch.throttle == 1.0 and race.player.velocity.length() > 1.0)
	await capture("race-16x9")
	# Pause must work with a SECOND finger while the first still accelerates.
	await tap("PauseButton", 1)
	check("pause cancels held touch and local player command", race.touch._fingers.is_empty() and race.player.command.throttle == 0.0)
	var positions: Array[Vector3] = []
	var velocities: Array[Vector3] = []
	for kart in race._karts:
		positions.append(kart.position)
		velocities.append(kart.velocity)
	var elapsed: float = race.director.elapsed
	var item_snapshot: int = race.items.activations
	await frames(90)
	var frozen := true
	for index in positions.size():
		frozen = frozen and race._karts[index].position == positions[index] and race._karts[index].velocity == velocities[index]
	check("pause freezes player and every opponent without erasing momentum", frozen and velocities[0].length() > 1.0)
	check("pause freezes race and item timers", race.director.elapsed == elapsed and race.items.activations == item_snapshot)
	check("pause temporarily mutes sound", AudioServer.is_bus_mute(0))
	await capture("pause-16x9")
	touch_at(race.touch.control_center("accelerate"), false, 0)
	await tap("ResumeButton")
	check("resume restores unmuted sound", not AudioServer.is_bus_mute(0))
	check("resume never resurrects stale touch", race.touch._fingers.is_empty() and race.touch.throttle == 0.0)
	# Held keyboard actions must be released before they can control the kart.
	Input.action_press("accelerate")
	race.player_driver.cancel_input()
	race.player_driver.update_command(1.0 / 60.0)
	check("held keyboard accelerator is suppressed after cancellation", race.player.command.throttle == 0.0)
	Input.action_release("accelerate")
	race.player_driver.update_command(1.0 / 60.0)
	Input.action_press("accelerate")
	race.player_driver.update_command(1.0 / 60.0)
	check("fresh keyboard press works after neutral", race.player.command.throttle == 1.0)
	Input.action_press("brake")
	race.player_driver.cancel_input()
	race.player_driver.update_command(1.0 / 60.0)
	Input.action_release("brake")
	race.player_driver.update_command(1.0 / 60.0)
	check("opposing held keys cannot bypass the neutral latch", race.player.command.throttle == 0.0)
	Input.action_release("accelerate")
	app.notification(Node.NOTIFICATION_APPLICATION_FOCUS_OUT)
	check("focus loss pauses an active race", app.screen == "paused" and paused)
	app.resume_race()
	check("background race cannot resume", app.screen == "paused")
	app.notification(Node.NOTIFICATION_APPLICATION_FOCUS_IN)
	check("focus gain requires explicit resume", app.screen == "paused" and paused)
	await tap("ResumeButton")
	app.notification(Node.NOTIFICATION_WM_GO_BACK_REQUEST)
	check("Android back request pauses racing", app.screen == "paused" and paused)
	await tap("RestartButton")
	check("restart creates a fresh race and countdown", app.screen == "racing" and app.race.get_instance_id() != first_id and app.race.director.countdown > 2.5)
	check("abandoned event does not award progress", app.profile.data.stats.races == 0)
	var old: WeakRef = weakref(app.race)
	await tap("PauseButton")
	await tap("PauseMenuButton")
	await frames()
	check("return to menu disposes world and unpauses tree", app.screen == "menu" and not paused and app.race == null and old.get_ref() == null)
	check("temporary pause mute is removed on return to menu", not AudioServer.is_bus_mute(0))
	# Cancellation during the yield must prevent a delayed stray race.
	app.start_race()
	check("loading has a cancellable state", app.screen == "loading")
	app.back()
	await frames(5)
	check("cancelled loading cannot instantiate a late world", app.screen == "menu" and app.race == null)
	app._on_results([], first_id)
	check("stale result callbacks do not mutate progress or UI", app.profile.data.stats.races == 0 and app.screen == "menu")
	# Repeated transitions must not retain a world or produce duplicate listeners.
	var disposed := true
	for cycle in 3:
		app.start_race()
		await frames(4)
		var ref: WeakRef = weakref(app.race)
		app.show_menu()
		await frames(4)
		disposed = disposed and ref.get_ref() == null and app.race == null and not paused
	check("three race-menu cycles release all old race roots", disposed)
	await tap("ExitButton")
	check("exit requires explicit confirmation", app.screen == "exit")
	await tap("CancelExitButton")
	check("exit cancellation restores menu", app.screen == "menu")
	await finish()
