## The playable race.
##
## Everything this scene builds comes out of the recovered game data:
##   - the track shape, road widths and start position from the recovered map
##     scenes (data/tracks.gd)
##   - the physics tuning from the recovered KartPhysicsHandling profiles
##     (data/handling.gd), selected per game mode
##   - the pickup boxes and their weighted contents from the recovered
##     PickupBox/PickupTable definitions
##   - the AI behaviour from the recovered RaceKartAIDefinition
##
## Only the materials are placeholders: no art or audio has been wired in yet.

extends Node3D

const Tracks := preload("res://scripts/data/tracks.gd")
const Profile := preload("res://scripts/handling_profile.gd")
const GameDB := preload("res://scripts/data/game_db.gd")

## Track to race. Ids come from the recovered map bundles -- see `Tracks.IDS`.
@export var track_id: String = "map_race_racearlenspeedway"
## "race", "battle" or "150"; picks the recovered handling profile.
@export var mode: String = "race"
@export var field_size: int = 6
@export var laps: int = 0

var director: RaceDirector
var pickups: PickupSpawner
var hud: RaceHud
var touch: TouchControls

var player: Kart
var _karts: Array[Kart] = []
var _ais: Array[KartAI] = []
var _centreline: PackedVector3Array = PackedVector3Array()
var _kart_body: Node3D


func _ready() -> void:
	randomize()
	_build_environment()
	_build_course()
	_build_field()
	_build_ui()
	director.start()


func _first_race_track() -> String:
	for id in Tracks.IDS:
		if not Tracks.is_battle(id):
			return id
	return Tracks.IDS[0] if Tracks.IDS.size() > 0 else ""


func _build_environment() -> void:
	var env := WorldEnvironment.new()
	var e := Environment.new()
	e.background_mode = Environment.BG_SKY
	e.ambient_light_source = Environment.AMBIENT_SOURCE_SKY
	e.ambient_light_energy = 1.0
	e.fog_enabled = true
	e.fog_density = 0.0018
	env.environment = e
	add_child(env)

	var sun := DirectionalLight3D.new()
	sun.rotation_degrees = Vector3(-52, -38, 0)
	sun.light_energy = 1.35
	sun.shadow_enabled = true
	add_child(sun)


func _build_course() -> void:
	# Fall back to a track that exists rather than cascading a null director
	# through every later step. A bad id in the scene file should degrade to a
	# different track, not to a wall of null-dereference errors.
	if not Tracks.has_track(track_id):
		var fallback := _first_race_track()
		push_warning("RaceMain: track %s not recovered; using %s" % [track_id, fallback])
		track_id = fallback

	var builder := TrackBuilder.new()
	builder.name = "Track"
	add_child(builder)
	var length := builder.build(track_id)

	# Resample the recovered spline for gates, the AI line and pickup placement.
	var knots := Tracks.route_knots(track_id)
	var track: Dictionary = Tracks.get_track(track_id)
	var loops := false
	if not track.is_empty() and (track["routes"] as Array).size() > 0:
		loops = bool(track["routes"][0]["loop"])
	_centreline = builder.sample_along(builder._resample(knots, loops), 8.0)

	director = RaceDirector.new()
	director.name = "RaceDirector"
	add_child(director)
	director.setup(track_id, laps)
	director.build_course(_centreline, 10.0)

	pickups = PickupSpawner.new()
	pickups.name = "Pickups"
	add_child(pickups)
	pickups.build(track_id)
	pickups.kart_picked_up.connect(_on_pickup)


func _build_field() -> void:
	_kart_body = Node3D.new()
	_kart_body.name = "Karts"
	add_child(_kart_body)

	var handling := Profile.for_mode(mode)
	# `start` is emitted as a Vector3 by tools/tracks_to_gdscript.py, not as a
	# raw array, so it is read as the type it actually is.
	var origin: Vector3 = Tracks.get_track(track_id).get("start", Vector3.ZERO)

	var ai_profile := Profile.for_mode("ai")

	for i in field_size:
		var is_player := i == 0
		var kart := _spawn_kart(is_player, origin, i)
		_karts.append(kart)
		director.register_kart(kart)
		if is_player:
			player = kart
		else:
			# Each AI drives the recovered AI handling profile, not the player's.
			kart.handling = ai_profile
			var ai := KartAI.new()
			kart.add_child(ai)
			ai.kart = kart
			ai.configure(_pick_ai_definition())
			ai.set_line(_centreline)
			ai.respawn_points = Tracks.respawn_locations(track_id)
			_ais.append(ai)


## Grid placement: staggered rows behind the start line, so karts are not
## spawning inside each other on the first frame.
func _spawn_kart(is_player: bool, origin: Vector3, index: int) -> Kart:
	var kart := Kart.new()
	kart.name = "Kart%d" % index
	_kart_body.add_child(kart)

	var cs := CollisionShape3D.new()
	var capsule := CapsuleShape3D.new()
	capsule.radius = 1.1
	capsule.height = 2.4
	cs.shape = capsule
	cs.position = Vector3(0, 1.2, 0)
	kart.add_child(cs)

	var body := StaticBody3D.new()
	kart.add_child(body)
	var mi := MeshInstance3D.new()
	var box := BoxMesh.new()
	box.size = Vector3(2.2, 1.2, 3.2)
	mi.mesh = box
	var mat := StandardMaterial3D.new()
	mat.albedo_color = Color(0.85, 0.25, 0.25) if is_player else Color(0.3, 0.55, 0.9)
	mat.roughness = 0.6
	mi.material_override = mat
	body.add_child(mi)
	mi.position = Vector3(0, 0.5, 0)

	# Park on the grid, facing along the recovered racing line.
	var slot := index
	var lane := slot % 2
	var row := slot / 2
	var along: Vector3 = _centreline[0] if _centreline.size() > 0 else origin
	var next: Vector3 = _centreline[1] if _centreline.size() > 1 else along + Vector3.FORWARD
	var fwd := (next - along)
	fwd.y = 0.0
	if fwd.length_squared() < 1e-6:
		fwd = Vector3.FORWARD
	fwd = fwd.normalized()
	var right := fwd.cross(Vector3.UP).normalized()
	kart.global_position = along + right * (float(lane) * 3.0 - 1.5) - fwd * (float(row) * 4.0)
	kart.global_position.y += 1.4
	kart.look_at(kart.global_position + fwd, Vector3.UP)

	var cam := Camera3D.new()
	cam.fov = Tracks.camera_fov(track_id)
	cam.far = 900.0
	kart.add_child(cam)
	if is_player:
		kart.chase_camera = cam
		cam.current = true
	return kart


## Pick the AI tuning the game ships for this difficulty and field size.
##
## The 31 recovered `RaceKartAIDefinition`s follow a strict naming convention:
## `RaceKartAIDefinition<Difficulty>[_<n>AI]` -- VeryEasy through Legend, and
## One/Two/Three/Four/Five/NineAI field sizes. Matching on that convention gets
## the real per-difficulty tuning (Legend and League-Diamond are separate
## profiles with their own look-ahead and drift odds) instead of using one
## profile for everybody.
@export var difficulty: String = "Hard"

func _pick_ai_definition() -> String:
	var ids := _ai_definition_ids()
	var want := "RaceKartAIDefinition%s_%dAI" % [difficulty, field_size]
	if ids.has(want):
		return want
	# Same difficulty, any field size.
	for id in ids:
		if id == "RaceKartAIDefinition" + difficulty:
			return id
	for id in ids:
		if id.begins_with("RaceKartAIDefinition" + difficulty + "_"):
			return id
	# Any profile for this field size.
	for id in ids:
		if id.ends_with("_%dAI" % field_size):
			return id
	return "RaceKartAIDefinitionHard_NineAI"


func _ai_definition_ids() -> PackedStringArray:
	var out := PackedStringArray()
	for id in GameDB.ids_of_class("RaceKartAIDefinition"):
		out.append(str(id))
	return out


func _build_ui() -> void:
	var layer := CanvasLayer.new()
	layer.name = "UI"
	add_child(layer)

	hud = RaceHud.new()
	hud.name = "HUD"
	layer.add_child(hud)

	touch = TouchControls.new()
	touch.name = "TouchControls"
	layer.add_child(touch)
	touch.visible = OS.has_feature("mobile")


func _on_pickup(kart: Node3D, usable_id: String, effect: int) -> void:
	if not (kart is Kart):
		return
	var h: KartPhysicsHandling = kart.handling
	if h == null:
		return
	match effect:
		PickupSpawner.Effect.BOOST:
			kart.trigger_boost(h.boost_duration, 1.0)
		PickupSpawner.Effect.TRIPLE_BOOST:
			kart.trigger_boost(h.boost_duration * 3.0, 1.0)
		PickupSpawner.Effect.SHIELD, PickupSpawner.Effect.TURD:
			# Reserved: the shield and projectile behaviours are recovered as
			# definitions but have no port logic yet. Collected so the box
			# disappears and respawns on its recovered timer.
			pass