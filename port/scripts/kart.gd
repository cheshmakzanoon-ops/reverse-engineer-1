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

var speed: float = 0.0
var is_drifting: bool = false
var is_boosting: bool = false
var lost_control: bool = false

var _steer_input: float = 0.0
var _throttle: float = 0.0
var _drift_direction: float = 0.0
var _drift_elapsed: float = 0.0
var _boost_timer: float = 0.0
var _lost_control_timer: float = 0.0
var _start_transform: Transform3D


func _ready() -> void:
	if handling == null:
		handling = KartPhysicsHandling.new()
	_start_transform = global_transform


func _physics_process(delta: float) -> void:
	_read_input()
	_tick_timers(delta)
	_update_speed()
	_apply_acceleration(delta)
	_apply_steering(delta)
	_update_drift(delta)
	move_and_slide()
	_apply_gravity(delta)
	_align_to_ground(delta)
	_update_camera(delta)


func _read_input() -> void:
	_steer_input = Input.get_axis("steer_left", "steer_right")
	_throttle = Input.get_action_strength("accelerate") - Input.get_action_strength("brake")
	if Input.is_action_just_pressed("reset_kart"):
		_respawn()


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

	if _throttle > 0.0 and speed < handling.speed_hard_cap:
		# `_forwardAccelCurve` maps absolute speed -> engine force, and falls
		# away toward the cap: (0,17.5) (8,13.5) (20,9.34) (30,3.2).
		var force := handling.forward_accel_force(speed)
		if is_boosting:
			# `_boost` is 0.8 on the default profile -- a multiplier, not an
			# additive impulse (an additive force would dwarf braking at 21).
			force *= 1.0 + handling.boost
		applied = force * _throttle
		if speed < 0.0:
			applied = -applied * 0.5   # braking out of a reverse
	elif _throttle < 0.0 and speed > -handling.speed_hard_cap * 0.4:
		applied = -handling.braking * absf(_throttle)
		if speed < 0.0:
			applied = -applied

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
	var cap := handling.speed_hard_cap + (handling.boost_speed_offset if is_boosting else 0.0)
	var longitudinal := velocity.dot(forward)
	if longitudinal > cap:
		velocity -= forward * (longitudinal - cap)


func _apply_steering(delta: float) -> void:
	if lost_control or is_boosting:
		# Recovered behaviour: boost and post-crash lockout suppress steering.
		return

	var basis := global_transform.basis
	var steer := _steer_input
	if is_drifting:
		steer = _drift_direction
	if is_zero_approx(steer):
		return

	# Yaw authority comes off the recovered absolute-speed curve
	# (0,1.3) .. (40,0.93), so the kart stays planted as it gains speed.
	var gain := steering_rate(steer) * handling.rot_speed_factor(absf(speed))
	if is_drifting:
		gain *= handling.rot_speed_drift_ratio   # recovered +0x74 (0.9)

	var basis_rotated := Basis(Vector3.UP, gain * delta) * basis
	basis_rotated = basis_rotated.orthonormalized()
	velocity = basis_rotated * velocity
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
	var want_drift := Input.is_action_pressed("handbrake") \
		and absf(speed) >= handling.drift_speed_min \
		and not is_zero_approx(_steer_input) \
		and not lost_control

	if want_drift and not is_drifting:
		is_drifting = true
		_drift_direction = signf(_steer_input)
		_drift_elapsed = 0.0
	elif not want_drift and is_drifting:
		_release_drift()

	if not is_drifting:
		return

	_drift_elapsed += delta

	# Clamp lateral velocity to the recovered drift grip so the kart slides
	# instead of gripping, and scrub speed while sliding.
	var lateral := velocity.dot(global_transform.basis.x)
	var limit := handling.tire_grip_drift * delta * 12.0
	velocity -= global_transform.basis.x * clampf(lateral, -limit, limit)
	velocity *= 1.0 - 0.35 * delta

	# Level up through `_driftBoostLevelsTable` as the meter fills.
	for level in handling.drift_boost_levels:
		if level.is_valid() and _drift_elapsed >= level.time_to_activate:
			trigger_boost(level.drift_boost_duration, level.drift_boost_ratio)


func _release_drift() -> void:
	is_drifting = false
	_drift_elapsed = 0.0
	_drift_direction = 0.0


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


func _respawn() -> void:
	global_transform = _start_transform
	velocity = Vector3.ZERO
	lost_control = false
	_lost_control_timer = 0.0
	is_boosting = false
	_boost_timer = 0.0