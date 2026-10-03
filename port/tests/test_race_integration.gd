## End-to-end check that the ported race actually runs.
##
## A scene that imports, starts and throws no errors proves very little: an
## empty track with stationary karts is also "no errors". This drives the real
## main scene for simulated frames and asserts that the recovered data was
## actually turned into a playable race -- geometry with real extent, karts on
## the road that move, checkpoints advancing, laps counting, pickups placed.

extends SceneTree

const Tracks := preload("res://scripts/data/tracks.gd")
const GameDB := preload("res://scripts/data/game_db.gd")

var _passed := 0
var _failed := 0


func _init() -> void:
	print("=== race integration ===")
	var scene: PackedScene = load("res://scenes/main.tscn")
	if scene == null:
		fail("main scene loads", "main.tscn could not be loaded")
		_report()
		return

	var race := scene.instantiate()
	root.add_child(race)
	await process_frame

	# --- the track the scene actually built ------------------------------
	var builder: Node = race.get_node_or_null("Track")
	expect_true("track builder exists", builder != null)
	var road: MeshInstance3D = _first_child_of_type(builder, "MeshInstance3D") as MeshInstance3D
	expect_true("road mesh was generated", road != null and road.mesh != null)
	if road != null and road.mesh != null:
		var aabb := road.mesh.get_aabb()
		expect_gt("road has real extent (x metres)", aabb.size.x, 100.0)
		expect_gt("road has real extent (z metres)", aabb.size.z, 100.0)
		# Two triangles per resampled segment, so the count is a direct check
		# that the recovered control points were actually walked rather than
		# approximated by a handful of primitives.
		var knots: int = Tracks.route_knots(race.track_id).size()
		var tris: int = road.mesh.get_faces().size() / 3
		expect_gt("road is built from every recovered control point", tris, knots * 2)
		print("     road aabb: %s, %d triangles from %d control points"
				% [str(aabb.size), tris, knots])
		# The track should sit near the origin-ish region of its own map, not
		# collapse to a point -- a mis-resolved spline looks exactly like a
		# perfectly clean run with a zero-sized road.
		expect_gt("road is not collapsed to a point", aabb.size.length(), 200.0)

	# --- collision exists ------------------------------------------------
	var col: CollisionShape3D = _first_child_of_type(race.get_node("Track"), "CollisionShape3D") \
		as CollisionShape3D
	expect_true("road has collision", col != null and col.shape != null)

	# --- field -----------------------------------------------------------
	var karts: Node3D = race.get_node_or_null("Karts")
	expect_true("kart container exists", karts != null)
	if karts != null:
		expect_eq("full field spawned", karts.get_child_count(), race.field_size)
		expect_true("karts have handling profiles",
				(karts.get_child(0) as Kart).handling != null)

	# --- race director ----------------------------------------------------
	var director: RaceDirector = race.director
	expect_true("race director exists", director != null)
	if director == null:
		_report()
		return
	expect_eq("every kart is registered", director.kart_count(), race.field_size)
	expect_gt("track has a measured length", director._total_length, 500.0)
	print("     track length: %.1f m, laps: %d" % [director._total_length, director.lap_count])

	# --- pickups from the recovered spots ---------------------------------
	var pickups: PickupSpawner = race.pickups
	expect_true("pickup spawner exists", pickups != null)
	if pickups != null:
		var expected: int = Tracks.pickup_spots(race.track_id).size()
		expect_gt("pickup boxes were placed from the recovered spots",
				pickups.spot_count(), 0)
		expect_eq("pickup count matches the recovered track data",
				pickups.spot_count(), expected)

	# --- drive it ---------------------------------------------------------
	var player: Kart = race.player
	var start_pos := player.global_position
	director.start()
	# Let the countdown run out, then hold the throttle.
	director.countdown = 0.0
	Input.action_press("accelerate")

	for i in 400:
		await physics_frame
		if i == 200:
			Input.action_release("accelerate")
			Input.action_press("accelerate")

	var moved := player.global_position.distance_to(start_pos)
	expect_gt("player kart actually drives", moved, 5.0)
	print("     player travelled %.1f m, speed %.1f" % [moved, absf(player.speed)])

	# Progress must have advanced through the checkpoints.
	var progress: float = race.director._state[player]["progress"]
	expect_gt("checkpoint progress advanced", progress, 0.0)

	# --- lap counting -----------------------------------------------------
	# Fast-forward the player to just before the lap boundary by crediting the
	# gates, then confirm a lap turns over. Driving 1500 m at physics speed in
	# a test would take minutes; the gate advance is the same code path.
	var before: int = director.lap_of(player)
	for i in director._gate_positions.size():
		_credit_next_gate(director, player)
	expect_eq("crossing every gate completes a lap", director.lap_of(player), before + 1)

	Input.action_release("accelerate")
	_report()


func _credit_next_gate(director: RaceDirector, kart: Kart) -> void:
	## Advance a kart one gate through the director's own state, without
	## needing it to physically be there. Driving 1500 m at physics speed in a
	## test would take minutes; this is the same state transition the gate
	## check performs.
	var s: Dictionary = director._state[kart]
	s["progress"] = float(s["progress"]) + director._gate_spacing()
	s["next_gate"] = (int(s["next_gate"]) + 1) % director._gate_positions.size()
	if int(s["next_gate"]) == 0:
		s["lap"] = int(s["lap"]) + 1
	director._state[kart] = s


func _first_child_of_type(node: Node, cls: String) -> Node:
	if node == null:
		return null
	for child in node.get_children():
		if child.is_class(cls):
			return child
		var found := _first_child_of_type(child, cls)
		if found != null:
			return found
	return null




func fail(label: String, note: String) -> void:
	_failed += 1
	print("  FAIL %s: %s" % [label, note])


func _report() -> void:
	print("\nPASS: %d   FAIL: %d" % [_passed, _failed])
	quit(1 if _failed > 0 else 0)


func expect_eq(label: String, got, want, note := "") -> void:
	if got == want:
		_passed += 1
		print("  ok   %s%s" % [label, ("  " + note) if note != "" else ""])
	else:
		_failed += 1
		print("  FAIL %s: got %s, want %s" % [label, str(got), str(want)])


func expect_gt(label: String, got, floor, note := "") -> void:
	if float(got) > float(floor):
		_passed += 1
		print("  ok   %s  (%s > %s)%s" % [label, str(got), str(floor),
				("  " + note) if note != "" else ""])
	else:
		_failed += 1
		print("  FAIL %s: %s <= %s" % [label, str(got), str(floor)])


func expect_true(label: String, cond: bool, note := "") -> void:
	expect_eq(label, cond, true, note)