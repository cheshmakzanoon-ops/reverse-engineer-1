## On-screen controls for touch devices.
##
## The APK has to be playable on a phone, and keyboard/gamepad input alone is
## not. These are real on-screen inputs rather than a scripted InputMap hack,
## because the racing controls are analog-ish: a steering slider needs to report
## a continuous value, and a jump-start into the steer axes gives a kart a
## steering input it should never have had.
##
## Driving `Input.action_press`/`action_release` means everything downstream --
## the kart, the AI, the HUD -- keeps reading the same actions as on desktop,
## so there is exactly one input path to reason about.

class_name TouchControls
extends Control

## Steering stick, bottom-left. Reports -1 (full left) .. 1 (full right).
var steer: float = 0.0
var throttle: float = 0.0
var handbrake_held: bool = false

## Only builds and accepts input once a touch has been seen, so a desktop run
## does not show a stick nobody is using.
var is_touch_active: bool = false

const ACTION_STEER_LEFT := "steer_left"
const ACTION_STEER_RIGHT := "steer_right"
const ACTION_ACCELERATE := "accelerate"
const ACTION_BRAKE := "brake"
const ACTION_HANDBRAKE := "handbrake"

const STICK_RADIUS := 96.0
const KNOB_RADIUS := 46.0

var _stick_origin := Vector2.ZERO
var _stick_pos := Vector2.ZERO
var _stick_touch := -1
var _brake_touch := -1
var _throttle_touch := -1
var _handbrake_touch := -1
var _steer_left_pressed := false
var _steer_right_pressed := false
var _accel_pressed := false
var _brake_pressed := false
var _handbrake_pressed := false


func _ready() -> void:
	set_anchors_preset(Control.PRESET_FULL_RECT)
	mouse_filter = Control.MOUSE_FILTER_IGNORE


func _input(event: InputEvent) -> void:
	var touch := event as InputEventScreenTouch
	if touch != null:
		is_touch_active = true
		visible = true
		if touch.pressed:
			_on_press(touch.index, touch.position)
		else:
			_on_release(touch.index)
	elif event is InputEventScreenDrag:
		_on_drag((event as InputEventScreenDrag).index,
				(event as InputEventScreenDrag).position)


## Track the finger ourselves rather than polling `Input.get_touches()`, which
## is unavailable on the desktop and web backends this project also builds for.
func _on_drag(index: int, pos: Vector2) -> void:
	if index == _stick_touch:
		_stick_pos = pos


func _on_press(index: int, pos: Vector2) -> void:
	var size := get_viewport_rect().size
	# Left third, lower half -> steering stick.
	if pos.x < size.x * 0.42 and pos.y > size.y * 0.42 and _stick_touch < 0:
		_stick_touch = index
		# Placing the stick where the thumb lands, not at a fixed spot: on a
		# phone the thumb rarely lands on a fixed control.
		_stick_origin = pos
		_stick_pos = pos
		steer = 0.0
		return
	# Right third, upper part -> brake/reverse.
	if pos.x > size.x * 0.58 and pos.y < size.y * 0.55 and _brake_touch < 0:
		_brake_touch = index
		_brake_pressed = true
		Input.action_release(ACTION_ACCELERATE)
		_accel_pressed = false
		return
	# Right third, lower part -> throttle.
	if pos.x > size.x * 0.58 and pos.y >= size.y * 0.55 and _throttle_touch < 0:
		_throttle_touch = index
		_accel_pressed = true
		Input.action_release(ACTION_BRAKE)
		_brake_pressed = false
		return
	# Centre-right strip -> handbrake / drift.
	if pos.x > size.x * 0.42 and pos.x <= size.x * 0.58 and _handbrake_touch < 0:
		_handbrake_touch = index
		_handbrake_pressed = true


func _on_release(index: int) -> void:
	if index == _stick_touch:
		_stick_touch = -1
		steer = 0.0
		_set_axis(ACTION_STEER_LEFT, false)
		_set_axis(ACTION_STEER_RIGHT, false)
	elif index == _throttle_touch:
		_throttle_touch = -1
		if _accel_pressed:
			_accel_pressed = false
			Input.action_release(ACTION_ACCELERATE)
	elif index == _brake_touch:
		_brake_touch = -1
		if _brake_pressed:
			_brake_pressed = false
			Input.action_release(ACTION_BRAKE)
	elif index == _handbrake_touch:
		_handbrake_touch = -1
		if _handbrake_pressed:
			_handbrake_pressed = false
			Input.action_release(ACTION_HANDBRAKE)


func _process(_delta: float) -> void:
	if _stick_touch < 0:
		return
	var delta := (_stick_pos - _stick_origin) / STICK_RADIUS
	if delta.length() > 1.0:
		delta = delta.normalized()
	steer = delta.x
	_set_axis(ACTION_STEER_LEFT, steer < -0.08)
	_set_axis(ACTION_STEER_RIGHT, steer > 0.08)


func _set_axis(action: String, pressed: bool) -> void:
	if pressed:
		if not Input.is_action_pressed(action):
			Input.action_press(action)
	elif Input.is_action_pressed(action):
		Input.action_release(action)


## Drive the same actions programmatically -- used by the replay/attract mode
## and by the AI when it has to borrow the player's kart.
func set_virtual(throttle_amount: float, steer_amount: float, handbrake := false) -> void:
	throttle = throttle_amount
	steer = steer_amount
	handbrake_held = handbrake
	_set_axis(ACTION_ACCELERATE, throttle_amount > 0.05)
	_set_axis(ACTION_BRAKE, throttle_amount < -0.05)
	_set_axis(ACTION_STEER_LEFT, steer_amount < -0.08)
	_set_axis(ACTION_STEER_RIGHT, steer_amount > 0.08)
	_set_axis(ACTION_HANDBRAKE, handbrake)


func release_all() -> void:
	for a: String in [ACTION_ACCELERATE, ACTION_BRAKE, ACTION_STEER_LEFT,
			ACTION_STEER_RIGHT, ACTION_HANDBRAKE]:
		if Input.is_action_pressed(a):
			Input.action_release(a)
	steer = 0.0
	throttle = 0.0
	handbrake_held = false
	_accel_pressed = false
	_brake_pressed = false
	_handbrake_pressed = false
	_steer_left_pressed = false
	_steer_right_pressed = false


func _draw() -> void:
	if not is_touch_active or not visible:
		return
	var size := get_viewport_rect().size
	# Steering stick.
	draw_arc(_stick_origin, STICK_RADIUS, 0.0, TAU, 48,
			Color(1, 1, 1, 0.22), 4.0)
	draw_circle(_stick_origin + Vector2(steer * STICK_RADIUS, 0), KNOB_RADIUS,
			Color(1, 1, 1, 0.35))
	# Throttle / brake pads.
	_pads(size)


func _pads(size: Vector2) -> void:
	var dim := Color(1, 1, 1, 0.18)
	var lit := Color(0.55, 0.95, 0.55, 0.45)
	var r := 76.0
	_pad(Vector2(size.x * 0.80, size.y * 0.76), r,
			"GO" if not _accel_pressed else "", _accel_pressed, lit, dim)
	_pad(Vector2(size.x * 0.93, size.y * 0.40), r,
			"BRK" if not _brake_pressed else "", _brake_pressed, lit, dim)
	_pad(Vector2(size.x * 0.63, size.y * 0.34), 52.0,
			"DRIFT" if not _handbrake_pressed else "", _handbrake_pressed, lit, dim)


func _pad(centre: Vector2, radius: float, label: String, on: bool,
		lit: Color, dim: Color) -> void:
	draw_circle(centre, radius, lit if on else dim)
	if label != "":
		var font := ThemeDB.fallback_font
		var size_px := font.get_string_size(label,
				HORIZONTAL_ALIGNMENT_CENTER, -1, 22)
		draw_string(font, centre + Vector2(-size_px.x * 0.5, 8), label,
				HORIZONTAL_ALIGNMENT_CENTER, -1, 22, Color(1, 1, 1, 0.7))