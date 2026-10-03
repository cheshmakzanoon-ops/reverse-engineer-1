## Kart behaviour, ported from `Atlas.Gameplay.Kart.KartPhysics`
## (recovered from `GameAssembly.dylib`).
##
## `KartPhysics` in the original is a MonoBehaviour wrapping a Unity Rigidbody
## plus a ground `Physics.Raycast` (recovered at RVA 0x1257744 -- see
## work/analysis/ghidra/physics.c, where the probe distance decompiles as the
## float constant 0x40200000 == 2.5f).
##
## Godot's CharacterBody3D stands in for Rigidbody + capsule collider: it gives
## the same arcade control loop without fighting a physics engine, and it keeps
## the port readable as a direct translation of the C#.
##
## IMPORTANT: all tuning numbers come from `handling`, whose defaults are the
## game's OWN recovered values (see kart_physics_handling.gd). The recovered
## curves are in ABSOLUTE speed units, so they are sampled by raw speed.
##
## Control flow mirrors the original's recovered method names:
##   InitPhysics -> accel/brake/steer -> drift -> boost -> collision.

class_name Kart
extends CharacterBody3D

## Distance of the ground probe, matching the recovered constant.
@export var raycast_distance: float = 2.5      # 0x40200000 in the original

@export var handling: KartPhysicsHandling
@export var chase_camera: Camera3D

signal respawn_requested(kart: Kart)
signal respawned(kart: Kart)

var command := KartCommand.new()
var driver: Node
var driving_enabled: bool = true

var speed: float = 0.0
var is_drifting: bool = false
var is_boosting: bool = false
var lost_control: bool = false

var _steer_input: float = 0.0
var _throttle: float = 0.0
var _drift_direction: float = 0.0
var _boost_timer: float = 0.0
var _lost_control_timer: float = 0.0
var _start_transform: Transform3D

## Seconds the current drift has been held. Public because the HUD draws the
## drift meter against the recovered `DriftBoostLevel.time_to_activate`
## thresholds -- it is the same number the game uses to decide when a tier
## fires, so exposing it is what keeps the meter honest.
var drift_elapsed: float = 0.0

## True while the kart is off the road or on a surface with its own modifiers.
var current_surface_type: int = 0
var _surface_modifiers: Dictionary = {}


func _ready() -> void:
	if handling == null:
		handling = KartPhysicsHandling.new()
	_start_transform = global_transform


func _physics_process(delta: float) -> void:
	if not driving_enabled:
		command.clear()
		velocity.x = 0.0
		velocity.z = 0.0
		_apply_gravity(delta)
		move_and_slide()
		_update_speed()
		_update_camera(delta)
		return
	if is_instance_valid(driver):
		driver.update_command(delta)
	_read_input()
	_tick_timers(delta)
	_update_speed()
	_apply_acceleration(delta)
	_apply_steering(delta)
	_update_drift(delta)
	_apply_gravity(delta)
	move_and_slide()
	_align_to_ground(delta)
	_update_speed()
	_update_camera(delta)


func _read_input() -> void:
	_steer_input = command.steer
	_throttle = command.throttle
	if command.consume_reset():
		request_respawn()


func set_driving_enabled(value: bool) -> void:
	if driving_enabled == value:
		return
	driving_enabled = value
	if not value:
		command.clear()
		velocity = Vector3.ZERO
		_release_drift(false)


func request_respawn() -> void:
	if respawn_requested.has_connections():
		respawn_requested.emit(self)
	else:
		respawn()


func _tick_timers(delta: float) -> void:
	if _boost_timer > 0.0:
		_boost_timer -= delta
		is_boosting = _boost_timer > 0.0
	if _lost_control_timer > 0.0:
		_lost_control_timer -= delta
		lost_control = _lost_control_timer > 0.0


func _update_speed() -> void:
	# Ground speed along the kart's own forward axis.
	speed = velocity.dot(global_transform.basis.z)


func _apply_acceleration(delta: float) -> void:
	var forward := global_transform.basis.z
	var applied := 0.0

	var cap := handling.speed_hard_cap + (handling.boost_speed_offset if is_boosting else 0.0)
	if _throttle > 0.0 and speed < cap:
		# `_forwardAccelCurve` maps absolute speed -> engine force, and falls
		# away toward the cap: (0,17.5) (8,13.5) (20,9.34) (30,3.2).
		var force := handling.forward_accel_force(speed)
		if is_boosting:
			# `_boost` is 0.8 on the default profile -- a multiplier, not an
			# additive impulse (an additive force would dwarf braking at 21).
			force *= 1.0 + handling.boost
		applied = force * _throttle
		if speed < 0.0:
			applied = handling.braking * _throttle   # oppose reverse velocity
	elif _throttle < 0.0:
		# Use the recovered reverse curve once forward motion has been braked.
		var force := handling.braking if speed > 0.0 else maxf(handling.reverse_accel_curve.sample(absf(speed)), 0.0)
		applied = -force * absf(_throttle)

	# Slope compensation (recovered +0x40..+0x4C): push into uphill, ease off
	# downhill, so ramps read as ramps.
	var slope_degrees := rad_to_deg(acos(clampf(global_transform.basis.y.dot(Vector3.UP), -1.0, 1.0)))
	if slope_degrees > handling.slope_compensation_angle_start:
		var t := clampf(
			(slope_degrees - handling.slope_compensation_angle_start)
			/ maxf(handling.slope_compensation_angle_max - handling.slope_compensation_angle_start, 0.001),
			0.0, 1.0)
		if _throttle > 0.0:
			applied *= lerpf(1.0, handling.slope_compensation_up, t)

	velocity += forward * applied * delta

	# `_speedHardCap` (55 on default), extended by `_boostSpeedOffset` (10).
	var longitudinal := velocity.dot(forward)
	if longitudinal > cap:
		velocity -= forward * (longitudinal - cap)


func _apply_steering(delta: float) -> void:
	if lost_control:
		# PORT-SIDE: boosts remain steerable; native steering parity is unproven.
		return

	var basis := global_transform.basis
	var steer := _steer_input
	if is_zero_approx(steer):
		return

	# Yaw authority comes off the recovered absolute-speed curve
	# (0,1.3) .. (40,0.93), so the kart stays planted as it gains speed.
	var gain := steering_rate(steer) * handling.rot_speed_factor(absf(speed))
	if is_drifting:
		gain *= handling.rot_speed_drift_ratio   # recovered +0x74 (0.9)

	var turn := Basis(Vector3.UP, -gain * delta)
	var basis_rotated := (turn * basis).orthonormalized()
	velocity = turn * velocity
	global_transform.basis = basis_rotated


## Steering rate. `steer_speed` is PORT-SIDE (see handling); `tire_grip` (3.99
## on default) and `tire_grip_drift` (2.1) are the game's own.
func steering_rate(steer: float) -> float:
	var grip := handling.tire_grip_drift if is_drifting else handling.tire_grip
	var rate := handling.steer_speed * grip * handling.turn_speed_compensation
	if is_drifting:
		rate *= handling.turn_speed_drift_compensation
		rate *= lerpf(handling.turn_speed_drift_side_force_ratio, 1.0, absf(steer))
	else:
		# Below `_driftSpeedMin` (16) the kart has little to turn against.
		rate *= lerpf(0.5, 1.0, clampf(absf(speed) / handling.drift_speed_min, 0.0, 1.0))
	return rate * steer


func _update_drift(delta: float) -> void:
	var want_drift := command.drift \
		and absf(speed) >= handling.drift_speed_min \
		and not is_zero_approx(_steer_input) \
		and not lost_control

	if want_drift and not is_drifting:
		is_drifting = true
		_drift_direction = signf(_steer_input)
		drift_elapsed = 0.0
	elif not want_drift and is_drifting:
		_release_drift()

	if not is_drifting:
		return

	drift_elapsed += delta

	# Clamp lateral velocity to the recovered drift grip so the kart slides
	# instead of gripping, and scrub speed while sliding.
	var lateral := velocity.dot(global_transform.basis.x)
	var limit := handling.tire_grip_drift * delta * 12.0
	velocity -= global_transform.basis.x * clampf(lateral, -limit, limit)
	velocity *= 1.0 - 0.35 * delta

	# Charge only. Paying out every tick made an unlimited boost generator.


func _release_drift(award_boost: bool = true) -> void:
	var earned: DriftBoostLevel = null
	if award_boost and is_drifting:
		for level in handling.drift_boost_levels:
			if level.is_valid() and drift_elapsed >= level.time_to_activate:
				if earned == null or level.time_to_activate > earned.time_to_activate:
					earned = level
	is_drifting = false
	drift_elapsed = 0.0
	_drift_direction = 0.0
	# PORT-SIDE payout timing until the native release method is recovered.
	if earned != null:
		trigger_boost(earned.drift_boost_duration, earned.drift_boost_ratio)


func trigger_boost(duration: float, ratio: float = 1.0) -> void:
	_boost_timer = maxf(_boost_timer, duration)
	is_boosting = true
	velocity += global_transform.basis.z * handling.boost_speed_offset * ratio


func _apply_gravity(delta: float) -> void:
	if is_on_floor():
		return
	# get_setting() returns Variant; the project sets 24.0, matching the original's
	# gravity of 24 (see project.godot).
	var g: float = ProjectSettings.get_setting("physics/3d/default_gravity", 24.0)
	# `_hopGravityScale` (2.75) is the hop's own gravity; the general airborne
	# case uses `_lateralAirGrip` / `_verticalAirGrip`.
	velocity += Vector3.DOWN * (g * handling.hop_gravity_scale * 0.4) * delta
	var up_alignment := global_transform.basis.y.dot(Vector3.UP)
	if up_alignment < 0.999 and absf(speed) > handling.rotate_to_vertical_in_air_speed:
		global_transform.basis = global_transform.basis.slerp(
			Basis(), clampf(handling.rotate_to_vertical_in_air_speed * delta, 0.0, 1.0))


func _align_to_ground(delta: float) -> void:
	if not is_on_floor():
		return
	# `_rotateToSurfaceSpeed` / `_rotateToSurfaceMaxAngle`: settle onto the
	# surface, but only within a max angle so a wall never snaps the kart flat.
	var axis := global_transform.basis.y.cross(Vector3.UP)
	if axis.length_squared() < 0.0001:
		return
	var angle := global_transform.basis.y.angle_to(Vector3.UP)
	if rad_to_deg(angle) > handling.rotate_to_surface_max_angle:
		return
	global_transform.basis = (
		Basis(axis.normalized(), -angle * handling.rotate_to_surface_speed * delta)
		* global_transform.basis
	).orthonormalized()


func _update_camera(delta: float) -> void:
	if chase_camera == null:
		return
	var target := global_position - global_transform.basis.z * 7.0 + Vector3.UP * 3.5
	chase_camera.global_position = chase_camera.global_position.lerp(
		target, clampf(6.0 * delta, 0.0, 1.0))
	if global_transform.basis.z.length_squared() > 0.001:
		chase_camera.look_at(global_position + global_transform.basis.z.normalized(), Vector3.UP)


## Put the kart back on the track at the nearest recovered respawn location.
##
## Public because the AI calls it: `KartAI` respawns a kart wedged against
## scenery using `_stuckAITimeToRespawn` from the recovered AI definition, and
## the player's reset key does the same. `respawn_points` is supplied by the
## race scene from `Tracks.respawn_locations()`; with none, the kart returns to
## where it started.
func respawn(respawn_points: Array = []) -> void:
	var target := _start_transform
	var best := INF
	for entry in respawn_points:
		var pos: Vector3 = entry.get("pos", Vector3.ZERO) if entry is Dictionary else Vector3(entry)
		var distance := pos.distance_to(global_position)
		if distance < best:
			best = distance
			var orientation: Basis = entry.get("basis", _start_transform.basis) if entry is Dictionary else _start_transform.basis
			target = Transform3D(orientation.orthonormalized(), pos)
	respawn_at(target)


func respawn_at(target: Transform3D) -> void:
	global_transform = target
	velocity = Vector3.ZERO
	speed = 0.0
	lost_control = false
	_lost_control_timer = 0.0
	is_boosting = false
	_boost_timer = 0.0
	_release_drift(false)
	command.clear()
	respawned.emit(self)


func _respawn() -> void:
	request_respawn()
