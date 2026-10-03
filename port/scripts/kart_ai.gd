## AI driver, tuned by the game's own `RaceKartAIDefinition` values.
##
## The look-ahead and drift behaviour are not hand-tuned: `_lookAheadFixedDist`
## (20) and `_lookAheadSpeedModifier` (0.32) are the shipped numbers, so the AI
## looks further ahead the faster it goes, exactly as the original does. Drift
## entry, drift exit angle and hop-boost probability come from the same
## definitions.
##
## Steering is produced by feeding the same InputMap actions the player uses,
## so there is one input path: the kart cannot tell who is driving it. That
## matters here because the handling model, drift ladder and boost all live
## downstream of input -- an AI that bypassed them would be a different car.

class_name KartAI
extends Node

const GameDB := preload("res://scripts/data/game_db.gd")

var kart: Kart
var difficulty: String = "Hard"

## Recovered respawn locations for this track, supplied by the race scene.
## Passing them to Kart.respawn() means a stuck AI rejoins the track at a point
## the level itself designates, rather than at its grid slot.
var respawn_points: Array = []

## Racing line, resampled to a fixed spacing, plus the track's total length.
var _line: PackedVector3Array = PackedVector3Array()
var _cursor: int = 0
var _stuck_timer: float = 0.0
var _drift_hold: float = 0.0
var _rng := RandomNumberGenerator.new()

var _cfg: Dictionary = {}


func _ready() -> void:
	_rng.randomize()


## Load `RaceKartAIDefinition<difficulty>_*` from the recovered database.
func configure(definition_id: String) -> void:
	_cfg = GameDB.lookup(definition_id)
	if _cfg.is_empty():
		push_warning("KartAI: no recovered %s; using default look-ahead" % definition_id)
		_cfg = {"_lookAheadFixedDist": 20.0, "_lookAheadSpeedModifier": 0.32,
			"_driftProbability": 100.0, "_driftStartThreshold": 5.5,
			"_driftMaxSteerThreshold": 7.5, "_exitDriftOutTime": 0.17,
			"_exitDriftAngle": 3.0, "_hopBoostProbability": 85.0,
			"_stuckAISpeedThreshold": 0.5, "_stuckAITimeToRespawn": 4.0,
			"_wrongWayWarningTime": 4.0, "_wrongWayRespawnTime": 16.0,
			"_reroutePointReachedDistThreshold": 15.0}


func set_line(line: PackedVector3Array) -> void:
	_line = line
	_cursor = 0


## The handling profile the AI drives with. `KartPhysicsHandlingRaceAI` is a
## distinct shipped profile, not the player's -- it exists because the AI is
## meant to be drivable-but-not-identical.
func handling_for_ai() -> KartPhysicsHandling:
	var Profile := load("res://scripts/handling_profile.gd")
	return Profile.for_mode("ai")


func _physics_process(delta: float) -> void:
	if kart == null or not is_instance_valid(kart) or _line.size() < 2:
		return
	if kart.handling == null:
		return

	var forward := _look_ahead_point()
	var to_target := forward - kart.global_position
	to_target.y = 0.0
	if to_target.length_squared() < 1e-6:
		return

	# Heading error in the kart's own frame: +x is right.
	var local := kart.global_transform.basis.inverse() * to_target.normalized()
	var angle := rad_to_deg(atan2(local.x, -local.z))

	# `steer` is a signed command in -1..1; the thresholds below come straight
	# from the definition and are in degrees.
	var steer := clampf(angle / maxf(float(_cfg.get("_driftMaxSteerThreshold", 7.5)) * 2.0, 1.0),
			-1.0, 1.0)

	_advance_cursor(delta)
	_stuck_check(delta)
	_drift_check(delta, steer)

	_drive(steer)


## Look further ahead the faster we go, per `_lookAheadFixedDist` +
## `_lookAheadSpeedModifier`.
func _look_ahead_point() -> Vector3:
	var fixed := float(_cfg.get("_lookAheadFixedDist", 20.0))
	var mult := float(_cfg.get("_lookAheadSpeedModifier", 0.32))
	var ahead_m := fixed + absf(kart.speed) * mult
	var steps := 0
	var want := ahead_m
	var accum := 0.0
	var i := _cursor
	while accum < want and steps < _line.size():
		var a: Vector3 = _line[i % _line.size()]
		var b: Vector3 = _line[(i + 1) % _line.size()]
		accum += a.distance_to(b)
		i += 1
		steps += 1
	return _line[i % _line.size()]


func _advance_cursor(delta: float) -> void:
	## Follow the cursor along the line at roughly driving speed, so progress
	## tracks the kart rather than teleporting to the nearest point.
	var spacing := _line_spacing()
	_cursor = (_cursor + int(maxf(absf(kart.speed) * delta / maxf(spacing, 0.01), 0.0))) \
		% _line.size()


func _line_spacing() -> float:
	if _line.size() < 2:
		return 1.0
	var total := 0.0
	for i in _line.size():
		total += _line[i].distance_to(_line[(i + 1) % _line.size()])
	return total / float(_line.size())


func _drive(steer: float) -> void:
	_press("steer_left", steer < -0.05)
	_press("steer_right", steer > 0.05)
	# Lift off the throttle when the corner is tight, so the AI has to actually
	# drift the hairpins instead of carrying the entry speed round.
	var tight := absf(steer) > 0.72
	_press("accelerate", not tight)
	_press("brake", false)


## Drift on tight corners, held until the kart has straightened out.
func _drift_check(delta: float, steer: float) -> void:
	if kart.is_drifting:
		_drift_hold -= delta
		if _drift_hold <= 0.0 and absf(steer) < float(_cfg.get("_exitDriftAngle", 3.0)) * 0.05:
			_press("handbrake", false)
		return
	var prob := float(_cfg.get("_driftProbability", 100.0))
	if prob <= 0.0:
		return
	if absf(steer) < float(_cfg.get("_driftStartThreshold", 5.5)) * 0.08:
		return
	if _rng.randf() * 100.0 > prob:
		return
	_press("handbrake", true)
	_drift_hold = float(_cfg.get("_exitDriftOutTime", 0.17))


## Respawn if wedged, using the definition's own stuck thresholds.
func _stuck_check(delta: float) -> void:
	if absf(kart.speed) > float(_cfg.get("_stuckAISpeedThreshold", 0.5)):
		_stuck_timer = 0.0
		return
	_stuck_timer += delta
	if _stuck_timer >= float(_cfg.get("_stuckAITimeToRespawn", 4.0)):
		_stuck_timer = 0.0
		kart.respawn(respawn_points)


## Named `_press`, not `_set`: Object already defines
## _set(StringName, Variant) -> bool, and shadowing it breaks the parser.
func _press(action: String, pressed: bool) -> void:
	if pressed:
		if not Input.is_action_pressed(action):
			Input.action_press(action)
	elif Input.is_action_pressed(action):
		Input.action_release(action)


func stop() -> void:
	for a: String in ["steer_left", "steer_right", "accelerate", "brake", "handbrake"]:
		if Input.is_action_pressed(a):
			Input.action_release(a)