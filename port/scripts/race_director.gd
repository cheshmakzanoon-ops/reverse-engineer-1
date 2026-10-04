## PORT-SIDE race rules over recovered route geometry; native parity is unproven.
## Credit requires ordered, forward, swept crossings inside finite gate bounds.
## Crossing the start arms a lap; only a full circuit then completes that lap.
class_name RaceDirector
extends Node

signal countdown_finished()
signal lap_completed(kart: Node3D, lap: int)
signal race_finished(results: Array)
signal position_changed(kart: Node3D, position: int, total: int)

const Tracks := preload("res://scripts/data/tracks.gd")
# PORT-SIDE safety limits, not recovered game constants.
@export var gate_half_height: float = 4.0
@export var maximum_motion_speed: float = 120.0
@export var motion_slack: float = 2.0
@export var finish_grace_seconds: float = 12.0
var watched_kart: Node3D
var track_id := ""
var lap_count := 3
var countdown := 3.0
var is_running := false
var is_finished := false
var elapsed := 0.0
var _gate_positions := PackedVector3Array()
var _gate_forward := PackedVector3Array()
var _gate_right := PackedVector3Array()
var _gate_widths: Array[Vector2] = []
var _segment_lengths := PackedFloat64Array()
var _centreline := PackedVector3Array()
var _total_length := 1.0
var _state: Dictionary = {}
var _results: Array = []
var _finish_deadline := INF

func _ready() -> void:
	# Sample AFTER kart movement, on the same fixed physics tick.
	process_physics_priority = 100

func setup(id: String, laps: int = 0) -> void:
	track_id = id
	lap_count = maxi(laps if laps > 0 else int(Tracks.get_track(id).get("laps", 3)), 1)
	_state.clear()
	_results.clear()
	_finish_deadline = INF
	elapsed = 0.0
	is_running = false
	is_finished = false
	countdown = 3.0

func build_course(centres: PackedVector3Array, spacing: float = 12.0, widths: Array = []) -> void:
	_gate_positions = PackedVector3Array()
	_gate_forward = PackedVector3Array()
	_gate_right = PackedVector3Array()
	_gate_widths.clear()
	_segment_lengths = PackedFloat64Array()
	_total_length = 0.0
	if not widths.is_empty() and widths.size() != centres.size():
		push_error("RaceDirector: route and gate widths disagree")
		return
	for i in centres.size():
		var p := centres[i]
		if not p.is_finite():
			push_error("RaceDirector: non-finite route point")
			_gate_positions.clear()
			return
		if not _gate_positions.is_empty() and p.distance_squared_to(_gate_positions[-1]) < 0.000001:
			continue
		_gate_positions.append(p)
		_gate_widths.append(Vector2(widths[i]) if not widths.is_empty() else Vector2.ONE * maxf(spacing * 0.6, 4.0))
	if _gate_positions.size() > 1 and _gate_positions[0].is_equal_approx(_gate_positions[-1]):
		_gate_positions.resize(_gate_positions.size() - 1)
		_gate_widths.pop_back()
	_centreline = _gate_positions
	var n := _gate_positions.size()
	if n < 3:
		_gate_positions.clear()
		return
	for i in n:
		var edge := _gate_positions[(i + 1) % n] - _gate_positions[i]
		_segment_lengths.append(edge.length())
		_total_length += edge.length()
		var forward := _gate_positions[(i + 1) % n] - _gate_positions[posmod(i - 1, n)]
		if forward.length_squared() < 0.000001:
			forward = edge
		forward = forward.normalized()
		_gate_forward.append(forward)
		var right := forward.cross(Vector3.UP).normalized()
		_gate_right.append(right if right.length_squared() > 0.5 else Vector3.RIGHT)

func register_kart(kart: Node3D) -> void:
	if _state.has(kart):
		return
	_state[kart] = {
		"next_gate": 0, "last_gate": -1, "started": false,
		"progress": 0.0, "credited": 0.0, "lap": 0,
		"position": _state.size() + 1, "grid_index": _state.size(),
		"finished": false, "finish_time": 0.0, "lap_started": 0.0,
		"last_point": kart.global_position, "spawn": kart.global_transform,
		"wrong_way_time": 0.0,
	}

func start() -> void:
	if _gate_positions.size() < 3 or _state.is_empty() or is_finished:
		return
	is_running = true
	countdown = 3.0
	for kart in _state:
		if is_instance_valid(kart):
			reset_motion(kart)

func _physics_process(delta: float) -> void:
	advance(delta)

## Also usable by deterministic simulations; never edits progress externally.
func advance(delta: float) -> void:
	if not is_running or delta <= 0.0 or not is_finite(delta):
		return
	if countdown > 0.0:
		countdown = maxf(countdown - delta, 0.0)
		for kart in _state:
			if is_instance_valid(kart):
				reset_motion(kart)
		if countdown == 0.0:
			countdown_finished.emit()
		return
	elapsed += delta
	for kart in _state:
		if is_instance_valid(kart):
			_update_kart(kart, delta)
	_rerank()
	if _results.size() == _state.size() or elapsed >= _finish_deadline:
		_close_results()

func _update_kart(kart: Node3D, delta: float) -> void:
	if not _state.has(kart) or _gate_positions.size() < 3:
		return
	var s: Dictionary = _state[kart]
	var current := kart.global_position
	var previous: Vector3 = s["last_point"]
	s["last_point"] = current
	if s["finished"] or not current.is_finite():
		return
	var motion := current - previous
	# Discontinuities do not sweep across gates. Respawn explicitly resets this
	# baseline too, so teleporting through the finish cannot award a lap.
	if motion.length() > maximum_motion_speed * maxf(delta, 0.0) + motion_slack:
		return
	var last_fraction := -1.0
	for attempt in _gate_positions.size():
		var next := int(s["next_gate"])
		var fraction := _crossing(next, previous, current)
		if fraction < 0.0 or fraction <= last_fraction:
			break
		last_fraction = fraction
		if s["started"]:
			s["credited"] = float(s["credited"]) + _segment_lengths[posmod(next - 1, _gate_positions.size())]
		else:
			s["started"] = true
			s["lap_started"] = elapsed
		s["last_gate"] = next
		s["next_gate"] = (next + 1) % _gate_positions.size()
		# Gate zero on the first crossing STARTS the race, not a completed lap.
		if next == 0 and float(s["credited"]) > 0.0:
			s["lap"] = int(s["lap"]) + 1
			var crossing_time := maxf(0.0, elapsed - delta * (1.0 - fraction))
			s["lap_started"] = crossing_time
			lap_completed.emit(kart, int(s["lap"]))
			if int(s["lap"]) >= lap_count:
				s["finished"] = true
				s["finish_time"] = crossing_time
				_results.append({"kart": kart, "name": str(kart.name), "time": crossing_time,
					"lap": int(s["lap"]), "finished": true, "grid_index": s["grid_index"]})
				if kart == watched_kart:
					_finish_deadline = elapsed + maxf(finish_grace_seconds, 0.0)
				break
	s["progress"] = float(s["credited"])
	if s["started"] and not s["finished"]:
		var last := int(s["last_gate"])
		var edge := _gate_positions[int(s["next_gate"])] - _gate_positions[last]
		var fraction := clampf((current - _gate_positions[last]).dot(edge) / maxf(edge.length_squared(), 0.0001), 0.0, 1.0)
		s["progress"] = float(s["credited"]) + fraction * _segment_lengths[last]
		var backwards := motion.dot(edge.normalized()) < -0.01
		s["wrong_way_time"] = float(s["wrong_way_time"]) + delta if backwards else 0.0

func _crossing(index: int, from: Vector3, to: Vector3) -> float:
	var point := _gate_positions[index]
	var forward := _gate_forward[index]
	var before := (from - point).dot(forward)
	var after := (to - point).dot(forward)
	if before > 0.0 or after <= 0.0 or after - before < 0.000001:
		return -1.0
	var fraction := -before / (after - before)
	var offset := from.lerp(to, fraction) - point
	var right := _gate_right[index]
	var lateral := offset.dot(right)
	var up := right.cross(forward).normalized()
	var widths := _gate_widths[index]
	if lateral < -widths.x or lateral > widths.y or absf(offset.dot(up)) > gate_half_height:
		return -1.0
	return fraction

func reset_motion(kart: Node3D) -> void:
	if _state.has(kart):
		_state[kart]["last_point"] = kart.global_position
		_state[kart]["wrong_way_time"] = 0.0

## PORT-SIDE recovery at the last validated gate, never the nearest future one.
func recovery_transform(kart: Node3D) -> Transform3D:
	if not _state.has(kart):
		return kart.global_transform
	var s: Dictionary = _state[kart]
	var last := int(s["last_gate"])
	if last < 0:
		return s["spawn"]
	var forward := _gate_forward[last]
	return Transform3D(Basis.looking_at(forward, Vector3.UP, true),
		_gate_positions[last] + forward * 0.25 + Vector3.UP * 0.15)

## Distance to the local validated route, not an arbitrary future branch.
func distance_from_expected_route(kart: Node3D) -> float:
	if not _state.has(kart) or _gate_positions.size() < 3:
		return INF
	var next := int(_state[kart]["next_gate"])
	var best := INF
	for offset in range(-3, 3):
		var index := posmod(next + offset, _gate_positions.size())
		var a := _gate_positions[index]
		var edge := _gate_positions[(index + 1) % _gate_positions.size()] - a
		var t := clampf((kart.global_position - a).dot(edge) / maxf(edge.length_squared(), 0.0001), 0.0, 1.0)
		best = minf(best, kart.global_position.distance_to(a + edge * t))
	return best

func _rerank() -> void:
	var order: Array = _state.keys()
	order.sort_custom(func(a: Node3D, b: Node3D) -> bool:
		var sa: Dictionary = _state[a]
		var sb: Dictionary = _state[b]
		if sa["finished"] != sb["finished"]:
			return sa["finished"]
		if sa["finished"] and sa["finish_time"] != sb["finish_time"]:
			return float(sa["finish_time"]) < float(sb["finish_time"])
		if sa["progress"] != sb["progress"]:
			return float(sa["progress"]) > float(sb["progress"])
		return int(sa["grid_index"]) < int(sb["grid_index"]))
	for index in order.size():
		var kart: Node3D = order[index]
		if int(_state[kart]["position"]) != index + 1:
			_state[kart]["position"] = index + 1
			if is_instance_valid(kart):
				position_changed.emit(kart, index + 1, order.size())

func _close_results() -> void:
	if is_finished:
		return
	is_running = false
	is_finished = true
	var order: Array = _state.keys()
	order.sort_custom(func(a, b): return position_of(a) < position_of(b))
	_results.clear()
	for kart in order:
		var s: Dictionary = _state[kart]
		_results.append({"kart": kart, "name": str(kart.name) if is_instance_valid(kart) else "Disconnected",
			"time": s["finish_time"] if s["finished"] else -1.0,
			"lap": s["lap"], "finished": s["finished"], "grid_index": s["grid_index"]})
	race_finished.emit(_results.duplicate(true))

func lap_of(kart: Node3D) -> int:
	return int(_state[kart]["lap"]) + 1 if _state.has(kart) else 1

func position_of(kart: Node3D) -> int:
	return int(_state[kart]["position"]) if _state.has(kart) else 1

func kart_count() -> int:
	return _state.size()

func is_kart_finished(kart: Node3D) -> bool:
	return _state.has(kart) and bool(_state[kart]["finished"])

func is_wrong_way(kart: Node3D) -> bool:
	return _state.has(kart) and float(_state[kart]["wrong_way_time"]) >= 0.5

func lap_time(kart: Node3D) -> float:
	if not _state.has(kart):
		return 0.0
	return 0.0 if is_kart_finished(kart) else maxf(0.0, elapsed - float(_state[kart]["lap_started"]))

static func format_time(seconds: float) -> String:
	var milliseconds := maxi(roundi(seconds * 1000.0), 0)
	return "%d:%02d.%03d" % [milliseconds / 60000, (milliseconds / 1000) % 60, milliseconds % 1000]

static func format_gap(seconds: float) -> String:
	return "--.---" if seconds <= 0.0 else "+%.3f" % seconds

## PORT-SIDE metric for recovered race pickup distribution bands.
func distance_behind_leader(kart: Node3D) -> float:
	if not _state.has(kart):
		return 0.0
	var leader := 0.0
	for state in _state.values():
		leader = maxf(leader, float(state["progress"]))
	return maxf(leader - float(_state[kart]["progress"]), 0.0)
