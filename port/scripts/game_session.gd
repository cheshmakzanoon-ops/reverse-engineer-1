## PORT-SIDE local front end and race lifecycle; not recovered Apple services,
## original campaign, character art or FMOD audio. Tests distinguish those gates.
class_name GameSession
extends Node

signal screen_changed(screen: String)
const RaceScene := preload("res://scenes/main.tscn")
const Tracks := preload("res://scripts/data/tracks.gd")
const BUS_NAMES := {"master": "Master", "music": "Music", "sfx": "SFX",
	"voice": "Voice", "ambience": "Ambience", "engine": "Engine"}
@export var profile_path := "user://profile"
var profile: LocalProfile
var selection: Dictionary
var settings: Dictionary
var screen := "boot"
var race: Node3D
var notice := ""
var last_results: Array[Dictionary] = []
var _canvas: CanvasLayer
var _surface: Control
var _margin: MarginContainer
var _theme: Theme
var _load_generation := 0
var _event_id := ""
var _run_selection: Dictionary
var _focused := true
var _exit_return := "title"

func _ready() -> void:
	# Menus must work during pause; the world is explicitly PAUSABLE below.
	process_mode = Node.PROCESS_MODE_ALWAYS
	get_tree().auto_accept_quit = false
	profile = LocalProfile.new(profile_path)
	var load_error := profile.load_profile()
	selection = profile.data.selection.duplicate(true)
	settings = profile.data.settings.duplicate(true)
	if load_error != OK:
		notice = profile.last_error + " This session will not overwrite them."
	elif profile.recovered_backup:
		notice = "Recovered the previous verified profile generation."
	_canvas = CanvasLayer.new()
	_canvas.layer = 20
	add_child(_canvas)
	_theme = _make_theme()
	get_viewport().size_changed.connect(_update_safe_area)
	apply_settings()
	_show_title()

func _make_theme() -> Theme:
	var theme := Theme.new()
	theme.default_font_size = 24
	for kind in ["normal", "hover", "pressed", "focus"]:
		var style := StyleBoxFlat.new()
		style.bg_color = Color("193f50") if kind == "normal" else Color("246a75")
		style.set_corner_radius_all(12)
		style.set_content_margin_all(16)
		if kind == "focus":
			style.draw_center = false
			style.set_border_width_all(3)
			style.border_color = Color("9ee9da")
		theme.set_stylebox(kind, "Button", style)
	var panel := StyleBoxFlat.new()
	panel.bg_color = Color("112330")
	panel.set_corner_radius_all(18)
	panel.set_content_margin_all(24)
	theme.set_stylebox("panel", "PanelContainer", panel)
	return theme

func safe_area() -> Rect2:
	var rect := get_viewport().get_visible_rect()
	if OS.has_feature("android"):
		var native := Rect2(DisplayServer.get_display_safe_area())
		if native.has_area():
			var local: Rect2 = get_viewport().get_screen_transform().affine_inverse() * native
			var clipped := rect.intersection(local)
			if clipped.has_area():
				rect = clipped
	return rect.grow(-24.0)

func _update_safe_area() -> void:
	if not is_instance_valid(_margin):
		return
	var rect := safe_area()
	_margin.position = rect.position
	_margin.size = rect.size

func _clear_ui() -> void:
	for child: Node in _canvas.get_children():
		_canvas.remove_child(child)
		child.queue_free()
	_surface = Control.new()
	_surface.name = "Screen"
	_surface.theme = _theme
	_surface.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_surface.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	_canvas.add_child(_surface)
	_margin = MarginContainer.new()
	_margin.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_surface.add_child(_margin)
	_update_safe_area()

func _state(value: String) -> void:
	screen = value
	apply_settings()
	screen_changed.emit(screen)
	# Stable machine-readable transitions for Android logcat smoke verification.
	print("KART_SESSION screen=" + screen)

func _screen(title: String, subtitle: String) -> VBoxContainer:
	_clear_ui()
	var background := ColorRect.new()
	background.color = Color("08141e")
	background.mouse_filter = Control.MOUSE_FILTER_IGNORE
	background.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	_surface.add_child(background)
	_surface.move_child(background, 0)
	var panel := PanelContainer.new()
	_margin.add_child(panel)
	var layout := VBoxContainer.new()
	layout.add_theme_constant_override("separation", 10)
	panel.add_child(layout)
	_label(layout, title, 42)
	_label(layout, subtitle, 20)
	if not notice.is_empty():
		var warning := _label(layout, notice, 18)
		warning.name = "SaveNotice"
		warning.add_theme_color_override("font_color", Color("f4c987"))
	var scroll := ScrollContainer.new()
	scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	layout.add_child(scroll)
	var content := VBoxContainer.new()
	content.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	content.add_theme_constant_override("separation", 12)
	scroll.add_child(content)
	return content

func _label(parent: Node, text: String, font_size: int = 24) -> Label:
	var label := Label.new()
	label.text = text
	label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	label.add_theme_font_size_override("font_size", font_size)
	parent.add_child(label)
	return label

func _button(parent: Node, id: String, text: String, action: Callable) -> Button:
	var button := Button.new()
	button.name = id
	button.text = text
	button.custom_minimum_size.y = 64
	button.pressed.connect(action)
	parent.add_child(button)
	return button

func _show_title() -> void:
	var box := _screen("KART LAB", "A local kart-racing reconstruction")
	_label(box, "Recovered routes and tuning power the race. Original art, character models and audio are not yet integrated.", 22)
	_button(box, "ContinueButton", "Continue", show_menu)
	_state("title")

func show_menu() -> void:
	_dispose_race()
	var box := _screen("Main menu", "Local play — no account or network service required")
	_label(box, "%d events played   /   %d wins" % [profile.data.stats.races, profile.data.stats.wins])
	_button(box, "QuickRaceButton", "Race setup", show_selection)
	_button(box, "SettingsButton", "Settings", show_settings)
	_button(box, "ExitButton", "Exit", request_exit)
	_state("menu")

func show_selection() -> void:
	if is_instance_valid(race):
		return
	var box := _screen("Race setup", "Engineering routes; Arlen Speedway has full three-lap regression coverage. Other routes remain experimental.")
	var tracks := LocalProfile.race_tracks()
	_option(box, "TrackChoice", "Circuit", tracks, str(selection.track_id), func(value: String): selection.track_id = value)
	_option(box, "ModeChoice", "Handling", ["race", "race150"], str(selection.mode), func(value: String): selection.mode = value)
	_option(box, "DifficultyChoice", "Opponents", LocalProfile.difficulties(), str(selection.difficulty), func(value: String): selection.difficulty = value)
	_option(box, "LapChoice", "Laps (0 = recovered default)", ["0", "1", "2", "3", "5", "9"], str(selection.laps), func(value: String): selection.laps = int(value))
	_check(box, "ItemsToggle", "Pickups and item combat", selection.items, func(value: bool): selection.items = value)
	_label(box, "Character, kart-art, arena and campaign selection remain unavailable until their content and rules are implemented.", 18)
	_button(box, "StartRaceButton", "Start race", start_race)
	_button(box, "SetupBackButton", "Back", show_menu)
	_state("selection")

func _option(parent: Node, id: String, label: String, choices: Array, current: String, change: Callable) -> OptionButton:
	var row := HBoxContainer.new()
	parent.add_child(row)
	var caption := _label(row, label, 22)
	caption.custom_minimum_size.x = 280
	var choice := OptionButton.new()
	choice.name = id
	choice.custom_minimum_size.y = 58
	choice.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	for value: String in choices:
		choice.add_item(Tracks.display_name(value) if value.begins_with("map_") else value)
		choice.set_item_metadata(choice.item_count - 1, value)
		if value == current:
			choice.select(choice.item_count - 1)
	choice.item_selected.connect(func(index: int): change.call(str(choice.get_item_metadata(index))))
	row.add_child(choice)
	return choice

func _check(parent: Node, id: String, text: String, current: bool, change: Callable) -> CheckBox:
	var control := CheckBox.new()
	control.name = id
	control.text = text
	control.button_pressed = current
	control.custom_minimum_size.y = 58
	control.toggled.connect(change)
	parent.add_child(control)
	return control

func show_settings() -> void:
	var box := _screen("Settings", "Audio routing is configured here; the original sound banks are not yet imported.")
	for bus: String in LocalProfile.AUDIO_BUSES:
		var row := HBoxContainer.new()
		box.add_child(row)
		var caption := _label(row, BUS_NAMES[bus], 22)
		caption.custom_minimum_size.x = 220
		var slider := HSlider.new()
		slider.name = bus.capitalize() + "Volume"
		slider.min_value = 0.0
		slider.max_value = 1.0
		slider.step = 0.05
		slider.value = settings.volumes[bus]
		slider.custom_minimum_size = Vector2(200, 48)
		slider.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		slider.value_changed.connect(func(value: float): settings.volumes[bus] = value; apply_settings())
		row.add_child(slider)
	_check(box, "MuteToggle", "Mute all audio", settings.muted, func(value: bool): settings.muted = value; apply_settings())
	_check(box, "ShadowsToggle", "Dynamic shadows", settings.shadows, func(value: bool): settings.shadows = value; apply_settings())
	_check(box, "TouchToggle", "Show touch controls", settings.touch_controls, func(value: bool): settings.touch_controls = value; apply_settings())
	_button(box, "SaveSettingsButton", "Save and return", _save_settings)
	_button(box, "CancelSettingsButton", "Cancel changes", _cancel_settings)
	_state("settings")

func _save_settings() -> void:
	var error := profile.save_settings(settings)
	notice = "Settings saved." if error == OK else "Settings were not saved: " + profile.last_error
	show_menu()

func _cancel_settings() -> void:
	settings = profile.data.settings.duplicate(true)
	show_menu()

func apply_settings() -> void:
	for bus: String in LocalProfile.AUDIO_BUSES:
		var index := AudioServer.get_bus_index(BUS_NAMES[bus])
		if index < 0:
			AudioServer.add_bus()
			index = AudioServer.bus_count - 1
			AudioServer.set_bus_name(index, BUS_NAMES[bus])
			AudioServer.set_bus_send(index, "Master")
		AudioServer.set_bus_volume_db(index, linear_to_db(maxf(float(settings.volumes[bus]), 0.0001)))
		AudioServer.set_bus_mute(index, float(settings.volumes[bus]) <= 0.0)
	AudioServer.set_bus_mute(0, bool(settings.muted) or not _focused or screen in ["paused", "results", "exit"] or float(settings.volumes.master) <= 0.0)
	if is_instance_valid(race):
		for light in race.find_children("*", "DirectionalLight3D", true, false):
			light.shadow_enabled = bool(settings.shadows)
		race.touch.set_controls_visible(bool(settings.touch_controls))

func set_selection(value: Dictionary) -> bool:
	if not LocalProfile.valid_selection(value) or screen not in ["menu", "selection"]:
		return false
	selection = value.duplicate(true)
	return true

func start_race() -> void:
	if screen not in ["menu", "selection", "paused", "results"]:
		return
	if not LocalProfile.valid_selection(selection):
		_show_error("Invalid race selection. No substitute track was loaded.")
		return
	var error := profile.save_selection(selection)
	if error != OK:
		notice = "Selection was not saved: " + profile.last_error
	else:
		notice = ""
	_dispose_race()
	_run_selection = selection.duplicate(true)
	_load_generation += 1
	var generation := _load_generation
	var box := _screen("Loading circuit", Tracks.display_name(selection.track_id))
	_button(box, "CancelLoadingButton", "Cancel", show_menu)
	_state("loading")
	# Let the loading screen render and old physics objects leave the tree first.
	await get_tree().process_frame
	if not is_inside_tree() or generation != _load_generation or screen != "loading":
		return
	race = RaceScene.instantiate()
	race.process_mode = Node.PROCESS_MODE_PAUSABLE
	race.track_id = _run_selection.track_id
	race.mode = _run_selection.mode
	race.difficulty = _run_selection.difficulty
	race.laps = int(_run_selection.laps)
	race.items_enabled = bool(_run_selection.items)
	add_child(race)
	if race.director == null or not race.director.is_running:
		_dispose_race()
		_show_error("Circuit initialization failed. The race was not started.")
		return
	_event_id = Crypto.new().generate_random_bytes(16).hex_encode()
	var token := race.get_instance_id()
	race.director.race_finished.connect(_on_results.bind(token))
	_run_selection.laps = race.director.lap_count
	_show_race_overlay()
	if not _focused:
		pause_race()

func _show_race_overlay() -> void:
	_clear_ui()
	var row := HBoxContainer.new()
	row.alignment = BoxContainer.ALIGNMENT_END
	row.size_flags_vertical = Control.SIZE_SHRINK_BEGIN
	row.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_margin.add_child(row)
	_button(row, "PauseButton", "Pause", pause_race)
	_state("racing")
	apply_settings()

func pause_race() -> void:
	if screen != "racing" or not is_instance_valid(race):
		return
	race.cancel_player_input()
	get_tree().paused = true
	var box := _screen("Paused", "The race clock and world are stopped. Resume explicitly when ready.")
	_button(box, "ResumeButton", "Resume", resume_race)
	_button(box, "RestartButton", "Restart race", start_race)
	_button(box, "PauseMenuButton", "Return to menu", show_menu)
	_state("paused")
	apply_settings()

func resume_race() -> void:
	if screen != "paused" or not _focused or not is_instance_valid(race):
		return
	get_tree().paused = false
	race._update_driving_state()
	_show_race_overlay()

func _on_results(results: Array, token: int) -> void:
	if not is_instance_valid(race) or race.get_instance_id() != token or screen != "racing" or not race.director.is_finished:
		return
	last_results.clear()
	var player_result := {}
	for index in results.size():
		var result: Dictionary = results[index]
		var is_player: bool = result.get("kart") == race.player
		last_results.append({"name": "You" if is_player else "Opponent %d" % int(result.get("grid_index", index)),
			"finished": bool(result.get("finished", false)), "time": float(result.get("time", -1.0))})
		if is_player:
			player_result = {"finished": bool(result.finished), "place": index + 1, "time": float(result.time)}
	if player_result.is_empty():
		notice = "The player result is missing; no progress was saved."
	else:
		var error := profile.record_result(_event_id, _run_selection, player_result.finished, player_result.place, player_result.time)
		notice = "Progress saved locally." if error == OK else "Progress was not saved: " + profile.last_error
	race.cancel_player_input()
	race.hud.visible = false
	get_tree().paused = true
	var box := _screen("Results", "Event complete")
	for index in last_results.size():
		var result: Dictionary = last_results[index]
		_label(box, "%d.  %s   %s" % [index + 1, result.name, RaceDirector.format_time(result.time) if result.finished else "DNF"], 24)
	_button(box, "RematchButton", "Race again", start_race)
	_button(box, "ResultsMenuButton", "Return to menu", show_menu)
	_state("results")
	apply_settings()

func _show_error(message: String) -> void:
	var box := _screen("Race unavailable", message)
	_button(box, "ErrorMenuButton", "Return to menu", show_menu)
	_state("error")

func _dispose_race() -> void:
	_load_generation += 1
	if is_instance_valid(race):
		race.cancel_player_input()
		remove_child(race)
		race.queue_free()
		race = null
	get_tree().paused = false
	last_results.clear()

func request_exit() -> void:
	if screen == "racing":
		pause_race()
	_exit_return = screen
	var box := _screen("Exit Kart Lab?", "Completed results and saved settings are kept. An unfinished race is not recorded as completed.")
	_button(box, "ConfirmExitButton", "Exit", func(): get_tree().quit())
	_button(box, "CancelExitButton", "Stay", _cancel_exit)
	_state("exit")
	apply_settings()

func _cancel_exit() -> void:
	if _exit_return == "paused" and is_instance_valid(race):
		screen = "racing"
		pause_race()
	elif _exit_return == "title":
		_show_title()
	else:
		show_menu()
	apply_settings()

func back() -> void:
	match screen:
		"racing": pause_race()
		"paused": resume_race()
		"settings": _cancel_settings()
		"selection", "results", "error", "loading": show_menu()
		"menu": _show_title()
		"title": request_exit()
		"exit": _cancel_exit()

func _unhandled_input(event: InputEvent) -> void:
	if event.is_action_pressed("ui_cancel") and not event.is_echo():
		back()
		get_viewport().set_input_as_handled()

func _notification(what: int) -> void:
	if not is_inside_tree() or profile == null:
		return
	if what == NOTIFICATION_APPLICATION_FOCUS_OUT or what == NOTIFICATION_APPLICATION_PAUSED:
		_focused = false
		pause_race()
		apply_settings()
	elif what == NOTIFICATION_APPLICATION_FOCUS_IN or what == NOTIFICATION_APPLICATION_RESUMED:
		_focused = true
		# Gaining focus never resumes a race or restores held input implicitly.
		apply_settings()
	elif what == NOTIFICATION_WM_GO_BACK_REQUEST:
		back()
	elif what == NOTIFICATION_WM_CLOSE_REQUEST:
		request_exit()

func _exit_tree() -> void:
	_load_generation += 1
	get_tree().paused = false
