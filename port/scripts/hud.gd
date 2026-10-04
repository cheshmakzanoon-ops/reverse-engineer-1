## In-race HUD: position, lap, timer, speed, drift boost charge.
##
## Values come straight from `RaceDirector` and the kart; nothing here decides
## game state. The drift meter reads `DriftBoostLevel.time_to_activate` from
## the recovered handling profile, so the bar filling means the same thing the
## original's did -- it is the real threshold, not a rescaled one, which is why
## the three tiers line up with the meter.

class_name RaceHud
extends Control

const DriftBoostLevel := preload("res://scripts/drift_boost_level.gd")

var director: RaceDirector
var player: Kart

var _position_label: Label
var _lap_label: Label
var _time_label: Label
var _speed_label: Label
var _item_label: Label
var _status_label: Label
var _drift_bar: ProgressBar
var _drift_tier: Label
var _countdown_label: Label
var _results_panel: PanelContainer
var _info: VBoxContainer
var _drift_box: VBoxContainer


func _ready() -> void:
	set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	mouse_filter = Control.MOUSE_FILTER_IGNORE
	_build()
	get_viewport().size_changed.connect(_layout)
	_layout()


func _build() -> void:
	var root := VBoxContainer.new()
	_info = root
	root.mouse_filter = Control.MOUSE_FILTER_IGNORE
	root.add_theme_constant_override("separation", 2)
	add_child(root)

	_position_label = _mk_label(root, 52, Color(1.0, 0.92, 0.45))
	_position_label.text = "1st"
	_lap_label = _mk_label(root, 26, Color(1, 1, 1))
	_lap_label.text = "LAP 1/3"
	_time_label = _mk_label(root, 22, Color(0.85, 0.9, 1.0))
	_time_label.text = "0:00.000"
	_item_label = _mk_label(root, 22, Color.WHITE)
	_status_label = _mk_label(root, 22, Color.WHITE)

	# Speed, bottom right.
	_speed_label = _mk_label(self, 28, Color(1, 1, 1))
	_speed_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT

	# Drift charge, bottom left.
	var drift_box := VBoxContainer.new()
	_drift_box = drift_box
	drift_box.mouse_filter = Control.MOUSE_FILTER_IGNORE
	drift_box.custom_minimum_size = Vector2(260, 0)
	add_child(drift_box)

	_drift_tier = _mk_label(drift_box, 18, Color(1, 1, 1))
	_drift_tier.text = ""
	_drift_bar = ProgressBar.new()
	_drift_bar.custom_minimum_size = Vector2(260, 16)
	_drift_bar.show_percentage = false
	_drift_bar.max_value = 1.0
	var fill := StyleBoxFlat.new()
	fill.bg_color = Color(1.0, 0.72, 0.15)
	var bg := StyleBoxFlat.new()
	bg.bg_color = Color(0, 0, 0, 0.45)
	_drift_bar.add_theme_stylebox_override("fill", fill)
	_drift_bar.add_theme_stylebox_override("background", bg)
	drift_box.add_child(_drift_bar)

	# Countdown, centred.
	_countdown_label = _mk_label(self, 120, Color(1, 1, 1))
	_countdown_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	_countdown_label.text = ""

	_build_results()


func _mk_label(parent: Node, px: int, colour: Color) -> Label:
	var l := Label.new()
	l.add_theme_font_size_override("font_size", px)
	l.add_theme_color_override("font_color", colour)
	l.add_theme_color_override("font_outline_color", Color(0, 0, 0, 0.85))
	l.add_theme_constant_override("outline_size", 6)
	parent.add_child(l)
	return l


func _build_results() -> void:
	_results_panel = PanelContainer.new()
	_results_panel.visible = false
	add_child(_results_panel)


func _layout() -> void:
	var safe := get_viewport_rect()
	if OS.has_feature("android"):
		var native := Rect2(DisplayServer.get_display_safe_area())
		if native.has_area():
			var converted: Rect2 = get_viewport().get_screen_transform().affine_inverse() * native
			var clipped := safe.intersection(converted)
			if clipped.has_area():
				safe = clipped
	safe = safe.grow(-24.0)
	_info.position = safe.position
	_info.size.x = minf(500.0, safe.size.x * 0.5)
	_speed_label.position = Vector2(safe.end.x - 220.0, safe.end.y - 44.0)
	_speed_label.size = Vector2(220.0, 44.0)
	_drift_box.position = Vector2(safe.position.x, safe.end.y - 62.0)
	_drift_box.size = Vector2(260.0, 62.0)
	_countdown_label.position = Vector2(safe.get_center().x - 120.0, safe.position.y + 120.0)
	_countdown_label.size = Vector2(240.0, 160.0)
	_results_panel.size = Vector2(minf(640.0, safe.size.x), 400.0)
	_results_panel.position = safe.get_center() - _results_panel.size * 0.5


func bind(d: RaceDirector, p: Kart) -> void:
	director = d
	player = p
	if d:
		d.race_finished.connect(show_results)


func _process(_delta: float) -> void:
	if director == null:
		return

	if director.is_running and director.countdown > 0.0:
		_countdown_label.text = "%d" % int(ceil(director.countdown))
	else:
		_countdown_label.text = ""

	if player != null:
		_speed_label.text = "SPEED %d" % int(absf(player.speed))
		_update_drift()
		_item_label.text = player.inventory.display_name()
		if not player.inventory.last_error.is_empty():
			_item_label.text += " — " + player.inventory.last_error
		_status_label.text = "SHIELD" if player.shield_time > 0.0 else ("SPINOUT" if player.lost_control else ("WRONG WAY" if director.is_wrong_way(player) else ""))

	if is_instance_valid(player):
		_position_label.text = _ordinal(director.position_of(player))
		_lap_label.text = "LAP %d/%d" % [mini(director.lap_of(player),
				director.lap_count), director.lap_count]
		_time_label.text = RaceDirector.format_time(director.elapsed)


## The bar fills against the NEXT tier's threshold, not against the whole
## three-tier ladder. Filling to 100% and only then paying out would mean the
## player never sees which tier they are close to, and would make tier 1 feel
## twice as long as tier 3 -- which is exactly backwards from the recovered
## data (2.0s to earn 0.24x, then 4.2s more to earn 0.38x, then 6.9s for 0.52x).
func _update_drift() -> void:
	if not player.is_drifting or player.handling == null:
		_drift_bar.value = 0.0
		_drift_tier.text = ""
		return

	var levels := player.handling.drift_boost_levels
	var elapsed := player.drift_elapsed
	# Levels arrive in the game's own order (3, 2, 1); sort so "next tier" is
	# the smallest one not yet earned.
	var sorted := levels.duplicate()
	sorted.sort_custom(func(a, b):
		return float(a.time_to_activate) < float(b.time_to_activate))

	var next: DriftBoostLevel = null
	var earned := 0
	for l in sorted:
		if elapsed >= float(l.time_to_activate):
			earned += 1
		elif next == null:
			next = l

	if next == null:
		_drift_bar.value = 1.0
		_drift_tier.text = "MAX BOOST"
		return

	var prev_time := 0.0
	for l in sorted:
		if float(l.time_to_activate) > elapsed:
			prev_time = maxf(float(l.time_to_activate), elapsed)
			break
	var lo := 0.0
	for l in sorted:
		if float(l.time_to_activate) <= elapsed:
			lo = float(l.time_to_activate)
	var span := maxf(next.time_to_activate - lo, 0.001)
	_drift_bar.value = clampf((elapsed - lo) / span, 0.0, 1.0)
	_drift_tier.text = "DRIFT  %d/%d" % [earned + 1, sorted.size()]


func show_results(results: Array) -> void:
	_results_panel.visible = true
	for c: Node in _results_panel.get_children():
		c.queue_free()
	var box := VBoxContainer.new()
	box.add_theme_constant_override("separation", 6)
	_results_panel.add_child(box)

	var title := _mk_label(box, 34, Color(1, 0.92, 0.45))
	title.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	title.text = "RESULTS"

	# Director already places finishers first, then DNFs by validated progress.
	# Sorting -1 (DNF) as a time would incorrectly promote it to first place.
	for i in results.size():
		var row := _mk_label(box, 22, Color(1, 1, 1))
		var result: Dictionary = results[i]
		var time_text := RaceDirector.format_time(float(result["time"])) if bool(result.get("finished", true)) else "DNF"
		row.text = "%d.  %s  %s" % [i + 1, result.get("name", "Kart"), time_text]


static func _ordinal(n: int) -> String:
	var s := ["th", "st", "nd", "rd"]
	var v := n % 100
	return "%d%s" % [n, s[clampi(v - 20 if v > 20 else v, 0, 3)]]