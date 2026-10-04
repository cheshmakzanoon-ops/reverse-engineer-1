## Validate every generated GLB in a directory using Godot's real importer.
## Input: -- /absolute/export-directory /absolute/new-report.json
## Parser/structural evidence only; not rendering fidelity or device performance.
extends SceneTree
var failures: Array[String] = []
var results: Array[Dictionary] = []

func _init() -> void:
	call_deferred("run")

func gather(directory: String, files: Array[String]) -> void:
	var dir := DirAccess.open(directory)
	if dir == null:
		failures.append("Unreadable directory: " + directory)
		return
	dir.list_dir_begin()
	var name := dir.get_next()
	while not name.is_empty():
		var path := directory.path_join(name)
		if dir.is_link(name):
			failures.append("Symlink is not allowed: " + path)
		elif dir.current_is_dir():
			gather(path, files)
		elif name.ends_with(".glb"):
			files.append(path)
		name = dir.get_next()
	dir.list_dir_end()

func inspect(path: String) -> void:
	var sidecar := path + ".json"
	var parsed: Variant = JSON.parse_string(FileAccess.get_file_as_string(sidecar))
	if not parsed is Dictionary or not parsed.has("sha256") or FileAccess.get_sha256(path) != parsed.sha256:
		failures.append("Missing/mismatched source sidecar: " + path)
		return
	var state := GLTFState.new()
	var document := GLTFDocument.new()
	if document.append_from_file(path, state) != OK:
		failures.append("glTF import failed: " + path)
		return
	var scene := document.generate_scene(state)
	if scene == null:
		failures.append("glTF scene generation failed: " + path)
		return
	root.add_child(scene)
	var meshes := scene.find_children("*", "MeshInstance3D", true, false)
	if scene is MeshInstance3D:
		meshes.append(scene)
	if meshes.is_empty():
		failures.append("No geometry: " + path)
	var vertices := 0
	for mesh: MeshInstance3D in meshes:
		if mesh.mesh == null or mesh.mesh.get_surface_count() == 0:
			failures.append("Empty mesh: " + path)
			continue
		for surface in mesh.mesh.get_surface_count():
			var arrays := mesh.mesh.surface_get_arrays(surface)
			var positions: PackedVector3Array = arrays[Mesh.ARRAY_VERTEX]
			vertices += positions.size()
			if positions.is_empty():
				failures.append("Empty vertex array: " + path)
			for position: Vector3 in positions:
				if not position.is_finite():
					failures.append("Non-finite vertex: " + path)
			var material := mesh.mesh.surface_get_material(surface) as BaseMaterial3D
			if material == null:
				failures.append("Missing/unsupported material: " + path)
			elif material.albedo_texture != null and (material.albedo_texture.get_width() == 0 or material.albedo_texture.get_height() == 0):
				failures.append("Empty texture: " + path)
		if mesh.skin != null:
			var skeleton := mesh.get_node_or_null(mesh.skeleton) as Skeleton3D
			if skeleton == null or mesh.skin.get_bind_count() == 0:
				failures.append("Broken skin binding: " + path)
	var expected: Dictionary = parsed.get("counts", {})
	if meshes.size() != int(expected.get("mesh_instances", -1)):
		failures.append("Renderer count differs from exported manifest: " + path)
	results.append({"file": path.get_file(), "sha256": parsed.sha256, "mesh_instances": meshes.size(), "vertex_references": vertices})
	scene.queue_free()

func run() -> void:
	var args := OS.get_cmdline_user_args()
	if args.size() != 2 or not args[0].is_absolute_path() or not args[1].is_absolute_path() or FileAccess.file_exists(args[1]):
		printerr("Use -- /absolute/export-directory /absolute/new-report.json")
		quit(1)
		return
	var files: Array[String] = []
	gather(args[0], files)
	files.sort()
	if files.is_empty():
		failures.append("No GLB outputs to validate")
	for path: String in files:
		inspect(path)
		await process_frame
	var report := {"status": "godot-import-verified" if failures.is_empty() else "failed", "files": results, "errors": failures, "original_game_verified": false, "android_verified": false}
	var file := FileAccess.open(args[1], FileAccess.WRITE)
	if file == null:
		printerr("Could not write import report")
		quit(1)
		return
	file.store_string(JSON.stringify(report, "\t"))
	file.close()
	print(JSON.stringify(report))
	print("PASS: %d FAIL: %d" % [results.size(), failures.size()])
	quit(0 if failures.is_empty() else 1)
