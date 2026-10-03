## One command buffer per kart. Drivers may never write another kart's buffer.
## Positive steer means right from the driver's view; the kart's nose is +Z.
class_name KartCommand
extends RefCounted

var throttle: float = 0.0
var steer: float = 0.0
var drift: bool = false
var reset_requested: bool = false
var use_item_requested: bool = false

func set_drive(accelerator: float, steering: float, drifting: bool = false) -> void:
	throttle = clampf(accelerator, -1.0, 1.0) if is_finite(accelerator) else 0.0
	steer = clampf(steering, -1.0, 1.0) if is_finite(steering) else 0.0
	drift = drifting

func clear() -> void:
	set_drive(0.0, 0.0)
	reset_requested = false
	use_item_requested = false

func consume_reset() -> bool:
	var requested := reset_requested
	reset_requested = false
	return requested

func consume_item() -> bool:
	var requested := use_item_requested
	use_item_requested = false
	return requested
