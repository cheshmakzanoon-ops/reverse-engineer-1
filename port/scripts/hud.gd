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
var _drift_bar: ProgressBar
var _drift_tier: Label
var _countdown_label: Label
var _results_panel: PanelContainer


func _ready() -> void:
	set_anchors_preset(Control.PRESET_FULL_RECT)
	mouse_filter = Control.MOUSE_FILTER_IGNORE
	_build()


func _build() -> void:
	var root := VBoxContainer.new()
	root.set_anchors_preset(Control.PRESET_TOP_WIDE)
	root.offset_left = 24
	root.offset_top = 18
	root.add_theme_constant_override("separation", 2)
	add_child(root)

	_position_label = _mk_label(root, 52, Color(1.0, 0.92, 0.45))
	_position_label.text = "1st"
	_lap_label = _mk_label(root, 26, Color(1, 1, 1))
	_lap_label.text = "LAP 1/3"
	_time_label = _mk_label(root, 22, Color(0.85, 0.9, 1.0))
	_time_label.text = "0:00.000"

	# Speed, bottom right.
	_speed_label = _mk_label(self, 44, Color(1, 1, 1))
	_speed_label.set_anchors_preset(Control.PRESET_BOTTOM_RIGHT)
	_speed_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
	_speed_label.offset_right = -28
	_speed_label.offset_bottom = -24

	# Drift charge, bottom left.
	var drift_box := VBoxContainer.new()
	drift_box.set_anchors_preset(Control.PRESET_BOTTOM_LEFT)
	drift_box.offset_left = 28
	drift_box.offset_top = -74
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
	_countdown_label.set_anchors_preset(Control.PRESET_CENTER_TOP)
	_countdown_label.offset_top = 120
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
	_results_panel.set_anchors_preset(Control.PRESET_CENTER)
	_results_panel.visible = false
	_results_panel.offset_left = -260
	_results_panel.offset_right = 260
	_results_panel.offset_top = -170
	_results_panel.offset_bottom = 190
	add_child(_results_panel)


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
		_speed_label.text = "%d" % int(absf(player.speed))
		_update_drift()

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

	var sorted := results.duplicate()
	sorted.sort_custom(func(a, b): return float(a["time"]) < float(b["time"]))
	for i in sorted.size():
		var row := _mk_label(box, 22, Color(1, 1, 1))
		row.text = "%d.   %s" % [i + 1, RaceDirector.format_time(float(sorted[i]["time"]))]


static func _ordinal(n: int) -> String:
	var s := ["th", "st", "nd", "rd"]
	var v := n % 100
	return "%d%s" % [n, s[clampi(v - 20 if v > 20 else v, 0, 3)]]