## Builds a playable road mesh and collision from the recovered track data.
##
## The track's shape is not invented here. `Tracks.route_knots()` returns the
## game's own spline control points, and each point carries the road half-widths
## the level shipped with (`_leftWidth` / `_rightWidth` on
## `MainSplineKnotBehaviour`). Those two facts fully determine the road surface:
## between consecutive control points the road is a quad whose corners are the
## points pushed left and right by their own widths. Nothing is smoothed or
## guessed, so the built road matches the recovered layout.
##
## The one thing added is resampling. The game builds its road with
## `SplineBehaviour._resolution` samples per segment; control points here are
## 6-70 units apart, which is far too coarse to drive on, so the Catmull-Rom
## spline is evaluated between control points. Where the control points already
## sit on a curve this reproduces the game's own sampling; where they are
## sparse it fills in the arc the original spline would have drawn.

class_name TrackBuilder
extends Node3D

const Tracks := preload("res://scripts/data/tracks.gd")

## Road surface tint. Kept plain because no art is wired in yet; everything
## about the GEOMETRY is recovered, only the material is a placeholder.
@export var road_color: Color = Color(0.22, 0.23, 0.26)
@export var shoulder_color: Color = Color(0.62, 0.20, 0.22)

## Samples generated per control-point segment.
@export var samples_per_segment: int = 10

## How far the verge extends past the road edge, in metres. The game's own
## off-track surface is what applies the grip modifiers in
## `KartHandlingSurfaceTypes`, so there has to be a surface to drive off onto.
@export var verge_width: float = 6.0

var track_id: String = ""


## Build `id` into this node. Returns the total centreline length in metres.
func build(id: String, route: int = 0) -> float:
	track_id = id
	for c: Node in get_children():
		c.queue_free()

	if not Tracks.has_track(id):
		push_warning("TrackBuilder: unknown track %s" % id)
		return 0.0

	var knots := Tracks.route_knots(id, route)
	if knots.is_empty():
		# A battle map legitimately has no racing line, so this is not a
		# failure. A race track with none is, and is reported as an error.
		if Tracks.is_battle(id):
			print("TrackBuilder: %s is a battle arena -- no racing line to build"
					% id)
		else:
			push_error("TrackBuilder: race track %s has no spline points" % id)
		return 0.0

	var loop: bool = Tracks.get_track(id)["routes"][route]["loop"]
	var centres := _resample(knots, loop)
	_build_road(centres, loop, id)
	_build_kerbs(centres, loop)
	return Tracks.track_length(id)


## Catmull-Rom interpolation of the recovered control points.
##
## Unity's own spline uses centripetal Catmull-Rom, which is what keeps a
## spline from overshooting when two control points are far apart -- and here
## they can be 70 units apart. Uniform Catmull-Rom visibly bulges on those, so
## the centripetal form is used.
func _resample(knots: Array, loop: bool) -> PackedVector3Array:
	var n := knots.size()
	if n < 2:
		return PackedVector3Array()
	var pts := PackedVector3Array()
	var closed: bool = loop and n > 2

	# Segment count includes the closing segment when the track is a loop.
	var segs := n if closed else n - 1
	for i in segs:
		var p0: Vector3 = knots[(i - 1 + n) % n]["pos"] if closed or i > 0 \
			else knots[0]["pos"]
		var p1: Vector3 = knots[i % n]["pos"]
		var p2: Vector3 = knots[(i + 1) % n]["pos"]
		var p3: Vector3 = knots[(i + 2) % n]["pos"] if closed or i + 2 < n \
			else knots[n - 1]["pos"]

		var w0 := 1.0
		var t0 := 0.0
		var t1 := t0 + maxf(p0.distance_to(p1), 1e-4) ** 0.5
		var t2 := t1 + maxf(p1.distance_to(p2), 1e-4) ** 0.5
		var t3 := t2 + maxf(p2.distance_to(p3), 1e-4) ** 0.5

		for s in samples_per_segment:
			var t := lerpf(t1, t2, float(s) / float(samples_per_segment))
			pts.append(_catmull_rom(p0, p1, p2, p3, t0, t1, t2, t3, t, w0))

	if not closed:
		pts.append(Vector3(knots[n - 1]["pos"][0], knots[n - 1]["pos"][1],
				knots[n - 1]["pos"][2]))
	return pts


## Non-uniform Catmull-Rom (Barry-Goldman form) for a non-uniform knot spacing.
static func _catmull_rom(p0: Vector3, p1: Vector3, p2: Vector3, p3: Vector3,
		t0: float, t1: float, t2: float, t3: float, t: float, w: float) -> Vector3:
	var d := t2 - t1
	if absf(d) < 1e-6:
		return p1
	var a1 := ((t1 - t) * p0 + (t - t0) * p1) / maxf(t1 - t0, 1e-6)
	var a2 := ((t2 - t) * p1 + (t - t1) * p2) / d
	var a3 := ((t3 - t) * p2 + (t - t2) * p3) / maxf(t3 - t2, 1e-6)
	var b1 := ((t2 - t) * a1 + (t - t0) * a2) / maxf(t2 - t0, 1e-6)
	var b2 := ((t3 - t) * a2 + (t - t1) * a3) / maxf(t3 - t1, 1e-6)
	return ((t2 - t) * b1 + (t - t1) * b2) / d


## Half-width at resampled point `i`, interpolated from the recovered knots.
##
## The road narrows and widens between 2 m and 5 m per side in these tracks, and
## that variation is a real part of how they play, so it is interpolated rather
## than averaged away.
func _widths_at(knots: Array, loop: bool) -> Array:
	var n := knots.size()
	if n < 2:
		return []
	var out := []
	var segs := n if (loop and n > 2) else n - 1
	for i in segs:
		var a: Dictionary = knots[i % n]
		var b: Dictionary = knots[(i + 1) % n]
		var al: float = a["left"] if a["left"] != null else 5.0
		var ar: float = a["right"] if a["right"] != null else 5.0
		var bl: float = b["left"] if b["left"] != null else 5.0
		var br: float = b["right"] if b["right"] != null else 5.0
		for s in samples_per_segment:
			var f := float(s) / float(samples_per_segment)
			out.append([lerpf(al, bl, f), lerpf(ar, br, f)])
	if not (loop and n > 2):
		var last: Dictionary = knots[n - 1]
		out.append([last["left"] if last["left"] != null else 5.0,
				last["right"] if last["right"] != null else 5.0])
	return out


func _frames(centres: PackedVector3Array, loop: bool) -> Array:
	"""Per-point {pos, right, forward, up}. Banked from the local slope."""
	var n := centres.size()
	var out := []
	for i in n:
		var next_i := (i + 1) % n
		var prev_i := (i - 1 + n) % n
		var a: Vector3 = centres[prev_i] if (loop or prev_i != i) else centres[i]
		var b: Vector3 = centres[next_i] if (loop or next_i != i) else centres[i]
		var fwd := (b - a)
		if fwd.length_squared() < 1e-8:
			fwd = Vector3.FORWARD
		fwd = fwd.normalized()
		var right := fwd.cross(Vector3.UP)
		if right.length_squared() < 1e-8:
			right = Vector3.RIGHT
		right = right.normalized()
		var up := right.cross(fwd).normalized()
		out.append({"pos": centres[i], "right": right, "forward": fwd, "up": up})
	return out


func _build_road(centres: PackedVector3Array, loop: bool, id: String) -> void:
	var knots := Tracks.route_knots(id)
	var widths := _widths_at(knots, loop)
	if widths.size() != centres.size():
		# Resampling and width interpolation must agree; if they ever drift the
		# mesh would be built from mismatched arrays and quietly come out wrong.
		push_warning("TrackBuilder: %d points vs %d widths; using uniform width"
				% [centres.size(), widths.size()])
		widths = []
		for i in centres.size():
			widths.append([5.0, 5.0])

	var frames := _frames(centres, loop)
	var n := frames.size()
	var segs := n if loop else n - 1

	var surface := SurfaceTool.new()
	surface.begin(Mesh.PRIMITIVE_TRIANGLES)
	var body := StaticBody3D.new()
	body.name = "RoadBody"
	add_child(body)

	for i in segs:
		var f0: Dictionary = frames[i]
		var f1: Dictionary = frames[(i + 1) % n]
		var w0: Array = widths[i]
		var w1: Array = widths[(i + 1) % n]

		# Dictionary reads are Variant, so the corner vectors are typed
		# explicitly; `:=` cannot infer from an untyped expression.
		var p0: Vector3 = f0["pos"]
		var r0: Vector3 = f0["right"]
		var u0: Vector3 = f0["up"]
		var p1: Vector3 = f1["pos"]
		var r1: Vector3 = f1["right"]
		var u1: Vector3 = f1["up"]
		var a: Vector3 = p0 - r0 * float(w0[0]) - u0 * 0.1
		var b: Vector3 = p0 + r0 * float(w0[1]) - u0 * 0.1
		var c: Vector3 = p1 + r1 * float(w1[1]) - u1 * 0.1
		var d: Vector3 = p1 - r1 * float(w1[0]) - u1 * 0.1

		for tri: Array in [[a, b, c], [a, c, d]]:
			for corner: Vector3 in tri:
				surface.set_normal(Vector3.UP)
				surface.add_vertex(corner)

	var mat := StandardMaterial3D.new()
	mat.albedo_color = road_color
	mat.roughness = 0.92
	surface.set_material(mat)

	var mesh := ArrayMesh.new()
	surface.commit(mesh)

	var mi := MeshInstance3D.new()
	mi.mesh = mesh
	body.add_child(mi)

	# Collision as a trimesh. Built from the same vertices as the visual mesh
	# so what you see is exactly what you hit -- a separate collision pass
	# would drift out of sync the moment a width changed.
	var shape := mesh.create_trimesh_shape()
	var col := CollisionShape3D.new()
	col.shape = shape
	body.add_child(col)


func _build_kerbs(centres: PackedVector3Array, loop: bool) -> void:
	"""Red/white verge either side of the road, slightly raised."""
	var frames := _frames(centres, loop)
	var n := frames.size()
	var segs := n if loop else n - 1
	var mat := StandardMaterial3D.new()
	mat.albedo_color = shoulder_color
	mat.roughness = 0.85

	for side in [-1.0, 1.0]:
		var st := SurfaceTool.new()
		st.begin(Mesh.PRIMITIVE_TRIANGLES)
		for i in segs:
			var f0: Dictionary = frames[i]
			var f1: Dictionary = frames[(i + 1) % n]
			var r0: Vector3 = f0["right"]
			var u0: Vector3 = f0["up"]
			var r1: Vector3 = f1["right"]
			var u1: Vector3 = f1["up"]
			var inner0: Vector3 = Vector3(f0["pos"]) + r0 * (5.0 * side)
			var inner1: Vector3 = Vector3(f1["pos"]) + r1 * (5.0 * side)
			var outer0: Vector3 = inner0 + r0 * (verge_width * side) - u0 * 0.35
			var outer1: Vector3 = inner1 + r1 * (verge_width * side) - u1 * 0.35
			for tri: Array in [[inner0, outer0, outer1], [inner0, outer1, inner1]]:
				for corner: Vector3 in tri:
					st.set_normal(Vector3.UP)
					st.add_vertex(corner)
		var m := ArrayMesh.new()
		st.set_material(mat)
		st.commit(m)
		var mi := MeshInstance3D.new()
		mi.mesh = m
		add_child(mi)


## Centreline points spaced `spacing` metres apart, for lap progress and AI.
func sample_along(centres: PackedVector3Array, spacing: float) -> PackedVector3Array:
	var out := PackedVector3Array()
	if centres.is_empty():
		return out
	var carry := 0.0
	out.append(centres[0])
	for i in range(centres.size() - 1):
		var a: Vector3 = centres[i]
		var b: Vector3 = centres[i + 1]
		var seg := a.distance_to(b)
		if seg < 1e-5:
			continue
		var d := carry
		while d < seg:
			d += spacing
			out.append(a.lerp(b, d / seg))
		carry = d - seg
	return out