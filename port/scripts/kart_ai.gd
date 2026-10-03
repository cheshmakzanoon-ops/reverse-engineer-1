## PORT-SIDE pure-pursuit driver using recovered look-ahead / stuck thresholds.
## This control algorithm is not a decompilation of the native AI. It only
## writes its own kart's command; the Input singleton belongs to PlayerDriver.
class_name KartAI
extends Node

const GameDB := preload("res://scripts/data/game_db.gd")
var kart: Kart
var difficulty: String = "Hard"
var respawn_points: Array = []
var _line := PackedVector3Array()
var _cursor: int = 0
var _segment_fraction: float = 0.0
var _stuck_timer: float = 0.0
var _cfg: Dictionary = {}
var _desired_speed: float = 22.0 # PORT-SIDE conservative racing speed.
var _stopped: bool = false

func configure(definition_id: String) -> void:
	_cfg = GameDB.lookup(definition_id)
	if _cfg.is_empty():
		push_error("KartAI: missing recovered definition: " + definition_id)
		_stopped = true

func set_line(line: PackedVector3Array) -> void:
	_line = line
	_reanchor()

func handling_for_ai() -> KartPhysicsHandling:
	return HandlingProfile.for_mode("ai")

## Called by Kart immediately before the physics step, not by node ordering.
func update_command(delta: float) -> void:
	if not is_instance_valid(kart):
		return
	if _stopped or not kart.driving_enabled or _line.size() < 3:
		kart.command.clear()
		return
	_advance_cursor(delta)
	var target := _look_ahead_point()
	var offset := kart.global_basis.inverse() * (target - kart.global_position)
	offset.y = 0.0
	var distance_sq := maxf(offset.length_squared(), 1.0)
	var curvature := 2.0 * offset.x / distance_sq
	# PORT-SIDE lateral acceleration budget. Recovered thresholds do not prove
	# the native speed planner, so these numbers are deliberately labelled.
	_desired_speed = minf(22.0, sqrt(4.0 / maxf(absf(curvature), 0.002)))
	var yaw_rate := kart.steering_rate(1.0) * kart.handling.rot_speed_factor(absf(kart.speed))
	var steer := -curvature * maxf(absf(kart.speed), 6.0) / maxf(yaw_rate, 0.01)
	if offset.z < 0.0:
		steer = -atan2(offset.x, offset.z)
		_desired_speed = 5.0
	_drive(clampf(steer, -1.0, 1.0))
	_stuck_check(delta)

func _drive(steer: float) -> void:
	if not is_instance_valid(kart):
		return
	if _stopped or not kart.driving_enabled:
		kart.command.clear()
		return
	var throttle := clampf((_desired_speed - kart.speed) * 0.4, -1.0, 1.0)
	kart.command.set_drive(throttle, steer, false)

func _reanchor() -> void:
	if not is_instance_valid(kart) or _line.size() < 2:
		return
	_find_segment(0, _line.size())

func _advance_cursor(_delta: float) -> void:
	if not is_instance_valid(kart) or _line.size() < 2:
		return
	# Use actual position, including sub-metre movement. No int(speed*dt)
	# truncation and no clock-based progress while a kart is stationary.
	_find_segment(_cursor - 3, mini(16, _line.size()))

func _find_segment(first: int, count: int) -> void:
	var best_distance := INF
	var chosen := _cursor
	var fraction := _segment_fraction
	for step in count:
		var index := posmod(first + step, _line.size())
		var a := _line[index]
		var edge := _line[(index + 1) % _line.size()] - a
		var t := clampf((kart.global_position - a).dot(edge) / maxf(edge.length_squared(), 0.0001), 0.0, 1.0)
		var distance := kart.global_position.distance_squared_to(a + edge * t)
		if distance < best_distance:
			best_distance = distance
			chosen = index
			fraction = t
	_cursor = chosen
	_segment_fraction = fraction

func _look_ahead_point() -> Vector3:
	var remaining := float(_cfg.get("_lookAheadFixedDist", 20.0)) + absf(kart.speed) * float(_cfg.get("_lookAheadSpeedModifier", 0.32))
	var index := _cursor
	var point := _line[index].lerp(_line[(index + 1) % _line.size()], _segment_fraction)
	for step in _line.size():
		var target := _line[(index + 1) % _line.size()]
		var distance := point.distance_to(target)
		if remaining <= distance and distance > 0.0001:
			return point.lerp(target, remaining / distance)
		remaining -= distance
		point = target
		index = (index + 1) % _line.size()
	return point

func _stuck_check(delta: float) -> void:
	if absf(kart.speed) > float(_cfg.get("_stuckAISpeedThreshold", 0.5)):
		_stuck_timer = 0.0
		return
	_stuck_timer += delta
	if _stuck_timer >= float(_cfg.get("_stuckAITimeToRespawn", 4.0)):
		_stuck_timer = 0.0
		kart.request_respawn()
		_reanchor()

func stop() -> void:
	_stopped = true
	if is_instance_valid(kart):
		kart.command.clear()
