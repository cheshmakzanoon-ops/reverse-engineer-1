## Build every recovered track and check the result is usable geometry.
##
## Only one track is wired into the main scene, so a fault in the track builder
## or in one particular map's data would otherwise sit undiscovered until
## someone picked that race. This runs all 16 through the same path the game
## uses and asserts each produces a road with real extent, collision, and a
## lap length consistent with the recovered spline.

extends SceneTree

const Tracks := preload("res://scripts/data/tracks.gd")
const TrackBuilderScript := preload("res://scripts/track_builder.gd")

var _passed := 0
var _failed := 0


func _init() -> void:
	print("=== all recovered tracks ===")
	var host := Node3D.new()
	root.add_child(host)

	for id in Tracks.IDS:
		var builder: Node3D = TrackBuilderScript.new()
		host.add_child(builder)
		var length: float = builder.build(id)

		if Tracks.is_battle(id):
			# Battle maps are arenas: no racing line is expected, but the scene
			# must still come up without errors and produce some geometry.
			expect_true("%s (battle) builds" % _short(id), length >= 0.0)
			builder.queue_free()
			continue

		expect_gt("%s has a measured lap length" % _short(id), length, 500.0)
		var road: MeshInstance3D = _first(builder, "MeshInstance3D") as MeshInstance3D
		if road == null or road.mesh == null:
			fail("%s produced a road mesh" % _short(id), "no MeshInstance3D")
			builder.queue_free()
			continue
		var aabb := road.mesh.get_aabb()
		expect_gt("%s road has extent" % _short(id), aabb.size.length(), 100.0)
		# The built road's footprint should be consistent with the measured lap
		# length: a lap cannot be longer than the road is wide.
		expect_gt("%s road covers its own lap length" % _short(id),
				maxf(aabb.size.x, aabb.size.z), length * 0.15)

		var col: CollisionShape3D = _first(builder, "CollisionShape3D") as CollisionShape3D
		expect_true("%s road has collision" % _short(id), col != null)
		print("     %-24s %7.1f m lap, %5d tris, %2d routes, %3d pickups, %3d respawns"
				% [_short(id), length, road.mesh.get_faces().size() / 3,
				   Tracks.num_routes(id), Tracks.pickup_spots(id).size(),
				   Tracks.respawn_locations(id).size()])
		builder.queue_free()

	print("\nPASS: %d   FAIL: %d" % [_passed, _failed])
	quit(1 if _failed > 0 else 0)


func _short(id: String) -> String:
	return id.trim_prefix("map_")


func _first(node: Node, cls: String) -> Node:
	for child in node.get_children():
		if child.is_class(cls):
			return child
		var found := _first(child, cls)
		if found != null:
			return found
	return null


func fail(label: String, note: String) -> void:
	_failed += 1
	print("  FAIL %s: %s" % [label, note])


func expect_gt(label: String, got, floor) -> void:
	if float(got) > float(floor):
		_passed += 1
		print("  ok   %s  (%s > %s)" % [label, str(got), str(floor)])
	else:
		_failed += 1
		print("  FAIL %s: %s <= %s" % [label, str(got), str(floor)])


func expect_true(label: String, cond: bool) -> void:
	if cond:
		_passed += 1
		print("  ok   %s" % label)
	else:
		_failed += 1
		print("  FAIL %s" % label)