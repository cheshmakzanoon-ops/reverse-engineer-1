## The only driving adapter allowed to read the global keyboard/gamepad state.
## Kart invokes update_command immediately before simulation, avoiding a frame
## of input lag or dependencies on scene-tree processing order.
class_name PlayerDriver
extends Node

var kart: Kart
var touch: TouchControls

func update_command(_delta: float) -> void:
	if not is_instance_valid(kart):
		return
	if not kart.driving_enabled:
		kart.command.clear()
		return
	var throttle := Input.get_action_strength("accelerate") - Input.get_action_strength("brake")
	var steer := Input.get_axis("steer_left", "steer_right")
	var drift := Input.is_action_pressed("handbrake")
	if is_instance_valid(touch) and touch.has_active_input():
		throttle = touch.throttle
		steer = touch.steer
		drift = touch.handbrake_held
	kart.command.set_drive(throttle, steer, drift)
	kart.command.reset_requested = Input.is_action_just_pressed("reset_kart")
	kart.command.use_item_requested = InputMap.has_action("use_item") and Input.is_action_just_pressed("use_item")
	if is_instance_valid(touch):
		kart.command.reset_requested = touch.consume_reset() or kart.command.reset_requested
		kart.command.use_item_requested = touch.consume_item() or kart.command.use_item_requested
