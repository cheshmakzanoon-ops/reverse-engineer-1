## Behavioral checkpoint regressions. Only positions move; race counters are read-only.
extends SceneTree

var passed := 0
var failed := 0
var course := PackedVector3Array([
	Vector3(0, 0, 0), Vector3(0, 0, 20), Vector3(20, 0, 20), Vector3(20, 0, 0)
])

func _init() -> void:
	call_deferred("run")

func check(label: String, value: bool) -> void:
	if value:
		passed += 1
		print("  ok   ", label)
	else:
		failed += 1
		print("  FAIL ", label)

func fixture(position: Vector3) -> Array:
	var d := RaceDirector.new()
	d.process_mode = Node.PROCESS_MODE_DISABLED
	root.add_child(d)
	d.setup("map_racearlenspeedway", 1)
	d.build_course(course, 6.0)
	var k := Node3D.new()
	root.add_child(k)
	k.position = position
	d.register_kart(k)
	return [d, k]

func move_to(d: RaceDirector, k: Node3D, target: Vector3, steps: int = 40) -> void:
	var begin := k.position
	for i in steps:
		k.position = begin.lerp(target, float(i + 1) / steps)
		d._update_kart(k, 1.0 / 60.0)
	d._rerank()

func dispose(f: Array) -> void:
	f[1].free()
	f[0].free()

func run() -> void:
	var builder := TrackBuilder.new()
	var sampled := builder.sample_along(PackedVector3Array([Vector3.ZERO, Vector3(0, 0, 10)]), 8.0)
	check("sampling never extrapolates past an open route", sampled[sampled.size() - 1].z <= 10.0001)
	builder.free()

	var f := fixture(Vector3(0, 0, 0))
	for i in 20:
		f[0]._update_kart(f[1], 1.0 / 60.0)
	check("standing at the line earns zero progress", float(f[0]._state[f[1]]["progress"]) == 0.0)
	dispose(f)

	f = fixture(Vector3(0, 0, -2))
	move_to(f[0], f[1], Vector3(0, 0, 2), 20)
	check("starting the event is not completing a lap", f[0].lap_of(f[1]) == 1)
	move_to(f[0], f[1], Vector3(0, 0, -2), 20)
	move_to(f[0], f[1], Vector3(20, 0, 0), 40)
	check("reverse crossing and a skipped route do not finish", not f[0].is_kart_finished(f[1]))
	dispose(f)

	f = fixture(Vector3(1000, 0, 1000))
	for i in 100:
		f[0]._update_kart(f[1], 1.0 / 60.0)
	check("distant stationary kart cannot collect gates", float(f[0]._state[f[1]]["progress"]) == 0.0)
	dispose(f)

	f = fixture(Vector3(0, 0, -2))
	move_to(f[0], f[1], Vector3(0, 0, 2), 20)
	move_to(f[0], f[1], Vector3(0, 0, 20), 40)
	move_to(f[0], f[1], Vector3(20, 0, 20), 40)
	move_to(f[0], f[1], Vector3(20, 0, 0), 40)
	move_to(f[0], f[1], Vector3.ZERO, 40)
	move_to(f[0], f[1], Vector3(0, 0, 2), 20)
	check("a full forward circuit finishes through movement", f[0].is_kart_finished(f[1]))
	dispose(f)

	f = fixture(Vector3(0, 0, -2))
	var others: Array[Node3D] = []
	for i in 2:
		var k := Node3D.new()
		root.add_child(k)
		k.position = Vector3(0, 0, -4 - i)
		f[0].register_kart(k)
		others.append(k)
	f[0]._rerank()
	check("ranking is a permutation when the leader stays first", f[0].position_of(f[1]) == 1 and f[0].position_of(others[0]) == 2 and f[0].position_of(others[1]) == 3)
	for k in others:
		k.free()
	dispose(f)
	# Gate finite bounds reject both driving beside and flying above a gate.
	for offset in [Vector3(30, 0, 30), Vector3(0, 10, 0)]:
		f = fixture(offset + Vector3(0, 0, -2))
		move_to(f[0], f[1], offset + Vector3(0, 0, 2), 20)
		check("gate bounds reject offset %s" % offset, not f[0]._state[f[1]]["started"])
		dispose(f)

	f = fixture(Vector3(0, 0, -2))
	f[1].position = Vector3(0, 0, 200)
	f[0]._update_kart(f[1], 1.0 / 60.0)
	check("teleport cannot sweep checkpoints", not f[0]._state[f[1]]["started"])
	f[1].position = Vector3(0, 0, -2)
	f[0].reset_motion(f[1])
	move_to(f[0], f[1], Vector3(0, 0, 2))
	var before: int = f[0]._state[f[1]]["next_gate"]
	move_to(f[0], f[1], Vector3(0, 0, -2))
	move_to(f[0], f[1], Vector3(0, 0, 2))
	check("repeated start crossings cannot skip the next gate", f[0]._state[f[1]]["next_gate"] == before)
	var recover: Transform3D = f[0].recovery_transform(f[1])
	check("respawn uses last validated gate rather than a future point", recover.origin.distance_to(course[0]) < 1.0)
	dispose(f)

	f = fixture(Vector3(0, 0, -2))
	f[1].position = Vector3(0, 0, 22)
	f[0]._update_kart(f[1], 0.5)
	check("swept motion can cross multiple ordered gates in a tick", int(f[0]._state[f[1]]["next_gate"]) == 2)
	dispose(f)

	# A player finish closes the race after grace even if an opponent stops.
	f = fixture(Vector3(0, 0, -2))
	var d: RaceDirector = f[0]
	var k: Node3D = f[1]
	var idle := Node3D.new()
	root.add_child(idle)
	idle.position = Vector3(0, 0, -4)
	d.register_kart(idle)
	d.watched_kart = k
	d.finish_grace_seconds = 0.2
	d.start()
	for i in 181:
		d.advance(1.0 / 60.0)
	check("countdown expires without creating progress", d.countdown == 0.0 and float(d._state[k]["progress"]) == 0.0)
	move_to(d, k, Vector3(0, 0, 2))
	for point in [course[1], course[2], course[3], course[0], Vector3(0, 0, 2)]:
		move_to(d, k, point)
	for i in 20:
		d.advance(1.0 / 60.0)
	check("bounded finish grace closes a race with a stopped rival", d.is_finished and d._results.size() == 2)
	check("DNF stays behind the finisher with no fabricated finish time", bool(d._results[0]["finished"]) and not bool(d._results[1]["finished"]) and d._results[1]["time"] == -1.0)
	var clock := d.elapsed
	d.advance(10.0)
	d.start()
	check("completed event cannot restart or change its clock", not d.is_running and d.elapsed == clock)
	idle.free()
	dispose(f)
	check("time formatting carries rounded milliseconds correctly", RaceDirector.format_time(59.9999) == "1:00.000")
	print("PASS: %d   FAIL: %d" % [passed, failed])
	quit(1 if failed else 0)
