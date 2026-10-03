## Multi-touch controls with local state, exact visual/hit-test agreement and
## no Input.action_press/action_release calls. Native touch/device latency is
## a separate verification gate; synthetic event tests do not establish it.
class_name TouchControls
extends Control

var steer: float = 0.0
var throttle: float = 0.0
var handbrake_held: bool = false
var is_touch_active: bool = false
var enabled: bool = true
var _fingers: Dictionary = {}
var _stick_origin := Vector2.ZERO
var _stick_pos := Vector2.ZERO
var _virtual := false
var _reset_requested := false
var _item_requested := false

const PLACEMENT := {
	"steer": Vector2(0.15, 0.77), "accelerate": Vector2(0.88, 0.78),
	"brake": Vector2(0.88, 0.48), "drift": Vector2(0.72, 0.78),
	"item": Vector2(0.72, 0.48), "reset": Vector2(0.55, 0.78),
}
const LABELS := {"accelerate": "GO", "brake": "BRAKE", "drift": "DRIFT", "item": "USE", "reset": "RESET"}

func _ready() -> void:
	set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	mouse_filter = Control.MOUSE_FILTER_IGNORE
	is_touch_active = OS.has_feature("mobile")
	visible = is_touch_active
	queue_redraw()

func safe_rect() -> Rect2:
	var rect := get_viewport_rect()
	if OS.has_feature("android"):
		var safe := Rect2(DisplayServer.get_display_safe_area())
		if safe.has_area():
			var local_safe: Rect2 = get_viewport().get_screen_transform().affine_inverse() * safe
			rect = rect.intersection(local_safe)
	return rect.grow(-16.0)

func control_center(action: String) -> Vector2:
	var rect := safe_rect()
	return rect.position + rect.size * Vector2(PLACEMENT[action])

func control_radius(action: String) -> float:
	var scale := minf(safe_rect().size.x / 1280.0, safe_rect().size.y / 720.0)
	return (96.0 if action == "steer" else (48.0 if action == "reset" else 68.0)) * scale

func _input(event: InputEvent) -> void:
	if not enabled or get_tree().paused:
		return
	var handled := false
	if event is InputEventScreenTouch:
		is_touch_active = true
		visible = true
		if event.pressed and not event.canceled:
			handled = _on_press(event.index, event.position)
		else:
			handled = _fingers.has(event.index)
			_on_release(event.index)
	elif event is InputEventScreenDrag:
		handled = _fingers.has(event.index)
		_on_drag(event.index, event.position)
	if handled:
		get_viewport().set_input_as_handled()
	queue_redraw()

func _on_press(index: int, pos: Vector2) -> bool:
	if not enabled or _fingers.has(index):
		return false
	for action: String in PLACEMENT:
		if pos.distance_to(control_center(action)) > control_radius(action):
			continue
		if _fingers.values().has(action):
			return false
		_virtual = false
		_fingers[index] = action
		if action == "steer":
			_stick_origin = control_center("steer")
			_on_drag(index, pos)
		elif action == "reset":
			_reset_requested = true
		elif action == "item":
			_item_requested = true
		_sync_buttons()
		queue_redraw()
		return true
	return false

func _on_drag(index: int, pos: Vector2) -> void:
	if _fingers.get(index, "") == "steer":
		_stick_pos = pos
		steer = clampf((pos.x - _stick_origin.x) / maxf(control_radius("steer"), 1.0), -1.0, 1.0)
		queue_redraw()

func _on_release(index: int) -> void:
	if _fingers.get(index, "") == "steer":
		steer = 0.0
	_fingers.erase(index)
	_sync_buttons()
	queue_redraw()

func _sync_buttons() -> void:
	var held := _fingers.values()
	# Brake wins while both are held; releasing it restores the held throttle.
	throttle = -1.0 if held.has("brake") else (1.0 if held.has("accelerate") else 0.0)
	handbrake_held = held.has("drift")

func has_active_input() -> bool:
	return enabled and (_virtual or not _fingers.is_empty())

func set_virtual(accelerator: float, steering: float, handbrake: bool = false) -> void:
	if not enabled:
		return
	_virtual = true
	throttle = clampf(accelerator, -1.0, 1.0)
	steer = clampf(steering, -1.0, 1.0)
	handbrake_held = handbrake
	queue_redraw()

func consume_reset() -> bool:
	var requested := _reset_requested
	_reset_requested = false
	return requested

func consume_item() -> bool:
	var requested := _item_requested
	_item_requested = false
	return requested

func set_enabled(value: bool) -> void:
	enabled = value
	if not value:
		release_all()
	queue_redraw()

func release_all() -> void:
	_fingers.clear()
	_virtual = false
	steer = 0.0
	throttle = 0.0
	handbrake_held = false
	_reset_requested = false
	_item_requested = false
	queue_redraw()

func _notification(what: int) -> void:
	if what == NOTIFICATION_APPLICATION_FOCUS_OUT or what == NOTIFICATION_APPLICATION_PAUSED or what == NOTIFICATION_PAUSED:
		release_all()
	elif what == NOTIFICATION_RESIZED and is_inside_tree():
		release_all()

func _exit_tree() -> void:
	release_all()

func _draw() -> void:
	if not is_touch_active or not enabled:
		return
	var center := control_center("steer")
	var radius := control_radius("steer")
	draw_arc(center, radius, 0.0, TAU, 48, Color(1, 1, 1, 0.4), 3.0)
	draw_circle(center + Vector2(steer * radius, 0), radius * 0.4, Color(1, 1, 1, 0.5))
	for action: String in LABELS:
		var held := _fingers.values().has(action)
		center = control_center(action)
		draw_circle(center, control_radius(action), Color(0.35, 0.8, 0.5, 0.65) if held else Color(0.1, 0.15, 0.2, 0.6))
		var text: String = LABELS[action]
		var font := ThemeDB.fallback_font
		var extent := font.get_string_size(text, HORIZONTAL_ALIGNMENT_CENTER, -1, 20)
		draw_string(font, center + Vector2(-extent.x * 0.5, 7), text, HORIZONTAL_ALIGNMENT_LEFT, -1, 20, Color.WHITE)
