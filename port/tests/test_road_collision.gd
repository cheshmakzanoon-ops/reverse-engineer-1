## Real physics queries must see the generated road from above, not its back face.
extends SceneTree
const Tracks := preload("res://scripts/data/tracks.gd")
var passed := 0
var failed := 0
func _init() -> void:
	call_deferred("run")
func run() -> void:
	var builder := TrackBuilder.new()
	root.add_child(builder)
	builder.build("map_racearlenspeedway")
	await physics_frame
	await physics_frame
	var knots := Tracks.route_knots("map_racearlenspeedway")
	var points := builder._resample(knots, true)
	for index in [0, 15, 45, 80, 130]:
		var point := points[index]
		var query := PhysicsRayQueryParameters3D.create(point + Vector3.UP * 5.0, point - Vector3.UP * 3.0)
		query.hit_back_faces = false
		var hit := builder.get_world_3d().direct_space_state.intersect_ray(query)
		var ok: bool = not hit.is_empty() and hit["normal"].y > 0.5
		print("  ", "ok" if ok else "FAIL", " upward road collision at sample ", index)
		if ok:
			passed += 1
		else:
			failed += 1
	builder.queue_free()
	await process_frame
	print("PASS: %d   FAIL: %d" % [passed, failed])
	quit(1 if failed else 0)
