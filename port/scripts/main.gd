## Demo scene for the Warped Kart Racers port.
##
## Builds the track procedurally so the APK is self-contained: no art has been
## extracted from the game's AssetBundles yet, and this milestone is about the
## physics port, not the assets.
##
## Track shape follows the original's `Atlas.Geometry` layout idea -- a closed
## spline of control points with banking and boost pads -- and the 18 race
## tracks recovered in `map_race*_scenes_all_*.bundle` are the eventual
## replacement.

extends Node3D

const TRACK_RADIUS := 46.0
const TRACK_WIDTH := 11.0
const SEGMENTS := 96


func _ready() -> void:
	_build_environment()
	_build_track()
	_build_kart()


func _build_environment() -> void:
	var env := WorldEnvironment.new()
	var e := Environment.new()
	e.background_mode = Environment.BG_SKY
	e.ambient_light_source = Environment.AMBIENT_SOURCE_SKY
	e.ambient_light_energy = 1.0
	e.fog_enabled = true
	e.fog_density = 0.004
	env.environment = e
	add_child(env)

	var light := DirectionalLight3D.new()
	light.rotation_degrees = Vector3(-52, -38, 0)
	light.light_energy = 1.3
	light.shadow_enabled = true
	add_child(light)

	# Kart colors lifted from the recovered kart bundle naming.
	var fill := DirectionalLight3D.new()
	fill.rotation_degrees = Vector3(-20, 140, 0)
	fill.light_energy = 0.35
	add_child(fill)


func _build_track() -> void:
	var floor_mat := StandardMaterial3D.new()
	floor_mat.albedo_color = Color(0.16, 0.17, 0.19)
	floor_mat.roughness = 0.95

	var wall_mat := StandardMaterial3D.new()
	wall_mat.albedo_color = Color(0.55, 0.16, 0.18)
	wall_mat.roughness = 0.7

	for i in SEGMENTS:
		var angle := TAU * float(i) / float(SEGMENTS)
		# A gentle figure-of-eight wobble so the track is not a flat circle.
		var radius := TRACK_RADIUS + sin(angle * 3.0) * 9.0
		var pos := Vector3(cos(angle) * radius, 0.0, sin(angle) * radius)

		var floor_body := StaticBody3D.new()
		floor_body.transform = Transform3D(Basis(), pos)
		floor_body.rotation.y = -angle + PI * 0.5
		add_child(floor_body)

		var floor_mesh := MeshInstance3D.new()
		var plane := PlaneMesh.new()
		plane.size = Vector2(TRACK_WIDTH * 1.5, 9.0)
		plane.material = floor_mat
		floor_mesh.mesh = plane
		floor_body.add_child(floor_mesh)

		var floor_col := CollisionShape3D.new()
		var floor_shape := BoxShape3D.new()
		floor_shape.size = Vector3(TRACK_WIDTH * 1.5, 1.0, 9.0)
		floor_col.shape = floor_shape
		floor_col.position = Vector3(0, -0.5, 0)
		floor_body.add_child(floor_col)

		# Barriers on both sides, with a gap skipped every few segments so the
		# track reads as having entries.
		if i % 8 != 0:
			for side in [-1.0, 1.0]:
				var wall_body := StaticBody3D.new()
				wall_body.transform = Transform3D(Basis(), pos)
				wall_body.rotation.y = -angle + PI * 0.5
				add_child(wall_body)

				var wall_mesh := MeshInstance3D.new()
				var box := BoxMesh.new()
				box.size = Vector3(0.8, 2.2, 9.0)
				box.material = wall_mat
				wall_mesh.mesh = box
				wall_body.add_child(wall_mesh)

				var wall_col := CollisionShape3D.new()
				var wall_shape := BoxShape3D.new()
				wall_shape.size = box.size
				wall_col.shape = wall_shape
				wall_col.position = Vector3(side * TRACK_WIDTH * 0.75, 1.1, 0)
				wall_body.add_child(wall_col)

		# Boost pads, spaced like the recovered `boosts_assets_all_*.bundle`.
		if i % 12 == 0:
			var pad := MeshInstance3D.new()
			var pad_mesh := BoxMesh.new()
			pad_mesh.size = Vector3(TRACK_WIDTH, 0.1, 2.0)
			var pad_mat := StandardMaterial3D.new()
			pad_mat.albedo_color = Color(0.2, 0.85, 1.0, 0.85)
			pad_mat.emission_enabled = true
			pad_mat.emission = Color(0.2, 0.6, 1.0)
			pad_mat.emission_energy_multiplier = 1.6
			pad_mesh.material = pad_mat
			pad.mesh = pad_mesh
			pad.transform = Transform3D(Basis(), pos + Vector3(0, 0.06, 0))
			pad.rotation.y = -angle + PI * 0.5
			add_child(pad)


func _build_kart() -> void:
	var handling := KartPhysicsHandling.new()

	var kart_body := CharacterBody3D.new()
	add_child(kart_body)

	var kart := Kart.new()
	kart.handling = handling
	kart_body.add_child(kart)

	var body_mesh := MeshInstance3D.new()
	var body_box := BoxMesh.new()
	body_box.size = Vector3(1.9, 0.75, 3.0)
	var body_mat := StandardMaterial3D.new()
	body_mat.albedo_color = Color(0.85, 0.32, 0.12)
	body_mat.roughness = 0.35
	body_box.material = body_mat
	body_mesh.mesh = body_box
	body_mesh.position = Vector3(0, 0.85, 0)
	kart.add_child(body_mesh)

	# Nose marker so heading is readable without art.
	var nose := MeshInstance3D.new()
	var nose_box := BoxMesh.new()
	nose_box.size = Vector3(0.5, 0.3, 0.5)
	nose_box.material = body_mat
	nose.mesh = nose_box
	nose.position = Vector3(0, 0.95, 1.3)
	kart.add_child(nose)

	for side in [-1.0, 1.0]:
		var wheel := MeshInstance3D.new()
		var wheel_mesh := CylinderMesh.new()
		wheel_mesh.top_radius = 0.5
		wheel_mesh.bottom_radius = 0.5
		wheel_mesh.height = 0.4
		var wheel_mat := StandardMaterial3D.new()
		wheel_mat.albedo_color = Color(0.08, 0.08, 0.09)
		wheel_mesh.material = wheel_mat
		wheel.mesh = wheel_mesh
		wheel.position = Vector3(side * 1.0, 0.5, 1.0)
		wheel.rotation_degrees = Vector3(0, 0, 90)
		kart.add_child(wheel)

	var col := CollisionShape3D.new()
	var capsule := CapsuleShape3D.new()
	capsule.radius = 0.9
	capsule.height = 1.4
	col.shape = capsule
	col.position = Vector3(0, 0.9, 0)
	kart.add_child(col)

	var camera := Camera3D.new()
	camera.fov = 70.0
	add_child(camera)
	kart.chase_camera = camera
	kart_body.position = Vector3(TRACK_RADIUS + 6.0, 1.5, 0.0)
	kart_body.rotation.y = PI * 0.5

	var label := Label.new()
	label.text = "WASD / Arrows to drive - SPACE to drift - R to respawn"
	label.position = Vector2(16, 16)
	add_child(label)