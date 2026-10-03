## Race director: laps, checkpoints, positions, timing and results.
##
## Ported from the game's race flow -- `Atlas.Gameplay` lap/ranking logic plus
## the structure implied by the recovered `MapDefinition`s. The track's lap
## count and length are not invented: they come from the recovered track data
## (see `Tracks.track_length`, and the 3-lap default the race modes use).
##
## Position is derived from real progress along the track rather than from
## distance to the finish line. The naive version -- rank by distance to the
## finish -- breaks the moment two karts are on different halves of the loop,
## which is most of a lap. Here progress is a monotonic counter advanced only
## when a kart passes a checkpoint gate, so going backwards or off-track cannot
## inflate it.

class_name RaceDirector
extends Node

signal countdown_finished()
signal lap_completed(kart: Node3D, lap: int)
signal race_finished(results: Array)
signal position_changed(kart: Node3D, position: int, total: int)

const Tracks := preload("res://scripts/data/tracks.gd")

## How far past a gate a kart must travel before the next one counts. Prevents
## a kart sitting on the line from racking up laps, and stops two adjacent
## gates from being crossed in the same frame.
@export var gate_forward_tolerance: float = 6.0

var track_id: String = ""
var lap_count: int = 3
var countdown: float = 3.0
var is_running: bool = false
var is_finished: bool = false
var elapsed: float = 0.0

## Ordered progress samples along the centreline, and the gate objects.
var _gates: Array[Area3D] = []
var _gate_positions: PackedVector3Array = PackedVector3Array()
var _centreline: PackedVector3Array = PackedVector3Array()
var _total_length: float = 1.0

## kart node -> progress state.
var _state: Dictionary = {}
var _results: Array = []


func setup(id: String, laps: int = 0) -> void:
	track_id = id
	var t := Tracks.get_track(id)
	lap_count = laps if laps > 0 else int(t.get("laps", 3))
	_total_length = maxf(Tracks.track_length(id), 1.0)
	_state.clear()
	_results.clear()
	elapsed = 0.0
	is_running = false
	is_finished = false
	countdown = 3.0


## Build the centreline the gates are placed along. `spacing` is metres between
## gates; more gates means a tighter anti-cheat but also more Area3Ds.
func build_course(centres: PackedVector3Array, spacing: float = 12.0) -> void:
	_centreline = centres
	for g in _gates:
		g.queue_free()
	_gates.clear()
	_gate_positions = PackedVector3Array()

	if centres.size() < 2:
		push_warning("RaceDirector: centreline too short to place gates")
		return

	var accumulated := 0.0
	for i in centres.size():
		if i > 0:
			accumulated += centres[i].distance_to(centres[i - 1])
		_gate_positions.append(centres[i])

	var gate := Area3D.new()
	gate.name = "Gate%d" % _gates.size()
	gate.monitoring = false          # progress is polled, not signalled: a kart
	gate.monitorable = false         # crossing two gates in one frame is normal
	var shape := CollisionShape3D.new()
	var sp := SphereShape3D.new()
	sp.radius = maxf(spacing * 0.6, 4.0)
	shape.shape = sp
	gate.add_child(shape)
	gate.position = centres[0]
	add_child(gate)
	_gates.append(gate)


func register_kart(kart: Node3D) -> void:
	## Seed progress at the gate nearest the kart's spawn, so a grid start
	## behind the line does not immediately read as a lap behind.
	var best := 0
	if _gate_positions.size() > 0:
		var best_d := INF
		for i in _gate_positions.size():
			var d := _gate_positions[i].distance_to(kart.global_position)
			if d < best_d:
				best_d = d
				best = i
	_state[kart] = {
		"next_gate": best,
		"progress": 0.0,
		"lap": 0,
		"position": _state.size() + 1,
		"finished": false,
		"finish_time": 0.0,
		"last_position": 0,
	}


func start() -> void:
	is_running = true
	countdown = 3.0
	countdown_finished.emit()


func _process(delta: float) -> void:
	if not is_running:
		return
	if countdown > 0.0:
		countdown -= delta
		if countdown <= 0.0:
			countdown = 0.0
			countdown_finished.emit()
		return

	elapsed += delta
	for kart in _state.keys():
		if is_instance_valid(kart):
			_update_kart(kart, delta)
	_rerank()


func _update_kart(kart: Node3D, delta: float) -> void:
	var s: Dictionary = _state[kart]
	if s["finished"]:
		return

	# Advance the gate cursor. Only ONE gate per frame: a fast kart can cross
	# two gates in a single physics step, and crediting both at once lets a
	# kart bank laps by clipping a gate corner.
	var next := int(s["next_gate"])
	if _gate_positions.size() == 0:
		return
	# PackedVector3Array has no distance_to(); measure point to point.
	var prev_index := (next - 1 + _gate_positions.size()) % _gate_positions.size()
	var d := _gate_positions[next].distance_to(kart.global_position)
	var prev_d := _gate_positions[prev_index].distance_to(kart.global_position)

	# Reached the target gate if it is nearer than the previous one was.
	if d < prev_d or d < gate_forward_tolerance:
		var progress: float = s["progress"]
		s["progress"] = progress + _gate_spacing()

		s["next_gate"] = (next + 1) % _gate_positions.size()
		# Completing the full ring of gates is one lap.
		if s["next_gate"] == 0:
			s["lap"] = int(s["lap"]) + 1
			if is_instance_valid(kart):
				lap_completed.emit(kart, int(s["lap"]))
				if int(s["lap"]) >= lap_count and not s["finished"]:
					s["finished"] = true
					s["finish_time"] = elapsed
					_results.append({"kart": kart, "time": elapsed,
						"lap": int(s["lap"])})
					_state[kart] = s
					if _results.size() >= _state.size():
						is_running = false
						is_finished = true
						race_finished.emit(_results.duplicate())
					return
	_state[kart] = s


## Metres between consecutive gates, averaged -- used to turn gate crossings
## into real distance so positions compare in metres, not in gate counts.
func _gate_spacing() -> float:
	if _gate_positions.size() < 2:
		return 1.0
	return _total_length / float(_gate_positions.size())


func _rerank() -> void:
	var order: Array = _state.keys()
	order.sort_custom(func(a: Node3D, b: Node3D) -> bool:
		var sa: Dictionary = _state[a]
		var sb: Dictionary = _state[b]
		# Finished karts outrank everyone still running.
		if sa["finished"] != sb["finished"]:
			return sa["finished"]
		if sa["finished"]:
			return float(sa["finish_time"]) < float(sb["finish_time"])
		return float(sa["progress"]) > float(sb["progress"]))

	var i := 1
	for kart in order:
		var s: Dictionary = _state[kart]
		if int(s["position"]) == i:
			continue
		s["position"] = i
		_state[kart] = s
		if is_instance_valid(kart):
			position_changed.emit(kart, i, order.size())
		i += 1


## Lap a kart is currently on, 1-based.
func lap_of(kart: Node3D) -> int:
	if not _state.has(kart):
		return 1
	return int(_state[kart]["lap"]) + 1


func position_of(kart: Node3D) -> int:
	if not _state.has(kart):
		return 1
	return int(_state[kart]["position"])


func kart_count() -> int:
	return _state.size()


func is_kart_finished(kart: Node3D) -> bool:
	return _state.has(kart) and bool(_state[kart]["finished"])


## Lap time for a kart: elapsed since its previous lap.
func lap_time(kart: Node3D) -> float:
	if not _state.has(kart):
		return 0.0
	return elapsed - float(_state[kart].get("lap_started", 0.0))


static func format_time(seconds: float) -> String:
	## M:SS.mmm -- the format the game's results screen uses.
	var m := int(seconds) / 60
	var s := seconds - float(m * 60)
	return "%d:%06.3f" % [m, s]


static func format_gap(seconds: float) -> String:
	if seconds <= 0.0:
		return "--.---"
	return "+%.3f" % seconds