## Kart handling model for Warped Kart Racers v2.02.
##
## Source: `Atlas.Gameplay.Kart.KartPhysicsHandling` in `GameAssembly.dylib`.
##
## TWO KINDS OF NUMBER IN HERE, and the difference matters:
##
##  * RECOVERED. Every scalar default below comes from the game's own
##    serialized ScriptableObject (`KartPhysicsHandlingDefault`), extracted
##    from `definitions_assets_all_*.bundle` by
##    `tools/unity_defs_to_gdscript.py` into `kart_physics_handling_data.gd`.
##    These are the game's own numbers.
##
##  * PORT-SIDE. A handful of constants that exist only in the Godot port --
##    chiefly `steer_speed`, which in the original is folded into native code
##    rather than a field. These are marked `# PORT-SIDE` and are the only
##    tuning knobs still under our control.
##
## Field names are the ORIGINAL C# names; the recovered object offset is in
## the comment above each field, so this file stays diffable against
## `dump.cs`. See docs/RE-FINDINGS.md.

class_name KartPhysicsHandling
extends Resource

## Recovered constants + full AnimationCurve keyframes. Generated; do not edit.
const D := preload("res://scripts/kart_physics_handling_data.gd")

# ---------------------------------------------------------------------------
# Acceleration — curves are in ABSOLUTE speed units (see forward_accel_force)
# ---------------------------------------------------------------------------

@export var forward_accel_curve: Curve = D.curve_forward_accel_curve()   # +0x20
@export var reverse_accel_curve: Curve = D.curve_reverse_accel_curve()   # +0x28

@export_group("Caps")
@export var speed_hard_cap: float = D.SPEED_HARD_CAP          # +0x30
@export var vertical_speed_cap: float = D.VERTICAL_SPEED_CAP  # +0x34
@export var braking: float = D.BRAKING                       # +0x38
@export var drift_speed_min: float = D.DRIFT_SPEED_MIN       # +0x3C

@export_group("Slope compensation")
@export var slope_compensation_up: float = D.SLOPE_COMPENSATION_UP              # +0x40
@export var slope_compensation_down: float = D.SLOPE_COMPENSATION_DOWN          # +0x44
@export var slope_compensation_angle_start: float = D.SLOPE_COMPENSATION_ANGLE_START  # +0x48
@export var slope_compensation_angle_max: float = D.SLOPE_COMPENSATION_ANGLE_MAX    # +0x4C

@export_group("Rotation")
@export var rot_speed_from_kart_speed_curve: Curve = D.curve_rot_speed_from_kart_speed_curve()  # +0x50
@export var rot_speed_from_steer_angle_curve: Curve = D.curve_rot_speed_from_steer_angle_curve()  # +0x58
@export var rotate_to_surface_speed: float = D.ROTATE_TO_SURFACE_SPEED       # +0x60
@export var rotate_to_surface_max_angle: float = D.ROTATE_TO_SURFACE_MAX_ANGLE  # +0x64

@export_group("Grip and turning")
@export var tire_grip: float = D.TIRE_GRIP                          # +0x68
@export var turn_speed_compensation: float = D.TURN_SPEED_COMPENSATION  # +0x6C
@export var steer_min_to_drift: float = D.STEER_MIN_TO_DRIFT        # +0x70
@export var rot_speed_drift_ratio: float = D.ROT_SPEED_DRIFT_RATIO  # +0x74
@export var tire_grip_drift: float = D.TIRE_GRIP_DRIFT              # +0x78
@export var tire_grip_drift_max_steer: float = D.TIRE_GRIP_DRIFT_MAX_STEER  # +0x7C
@export var drift_steer_min_default: float = D.DRIFT_STEER_MIN_DEFAULT  # +0x80
@export var drift_steer_max_default: float = D.DRIFT_STEER_MAX_DEFAULT  # +0x84
@export var drift_steer_neutral_default: float = D.DRIFT_STEER_NEUTRAL_DEFAULT  # +0x88
@export var turn_speed_drift_compensation: float = D.TURN_SPEED_DRIFT_COMPENSATION  # +0x8C
@export var turn_speed_drift_side_force_ratio: float = D.TURN_SPEED_DRIFT_SIDE_FORCE_RATIO  # +0x90

@export_group("Drift visuals")
@export var drift_visual_yaw_vel_max: float = D.DRIFT_VISUAL_YAW_VEL_MAX  # +0x94
@export var drift_visual_angle_offset: float = D.DRIFT_VISUAL_ANGLE_OFFSET  # +0x98
@export var drift_visual_angle_speed_in: float = D.DRIFT_VISUAL_ANGLE_SPEED_IN  # +0x9C
@export var drift_visual_angle_speed_out: float = D.DRIFT_VISUAL_ANGLE_SPEED_OUT  # +0xA0

@export_group("Boost")
@export var boost_speed_offset: float = D.BOOST_SPEED_OFFSET  # +0xA4
@export var boost: float = D.BOOST                            # +0xA8
@export var boost_duration: float = D.BOOST_DURATION          # +0xAC
@export var small_boost_speed_offset: float = D.SMALL_BOOST_SPEED_OFFSET  # +0xB0
@export var hop_boost_ratio: float = D.HOP_BOOST_RATIO        # +0xB4
@export var hop_boost_duration: float = D.HOP_BOOST_DURATION  # +0xB8
@export var hop_boost_speed_threshold: float = D.HOP_BOOST_SPEED_THRESHOLD  # +0xBC
@export var hop_boost_gravity_scale: float = D.HOP_BOOST_GRAVITY_SCALE  # +0xC0
@export var mid_air_boost_accel_factor: float = D.MID_AIR_BOOST_ACCEL_FACTOR  # +0xC4
@export var gliding_boost_accel_factor: float = D.GLIDING_BOOST_ACCEL_FACTOR  # +0xC8

@export_group("Drift boost levels")
@export var drift_boost_levels: Array[DriftBoostLevel] = []

## Per-surface grip modifiers, recovered from `KartHandlingSurfaceTypes`
## (7 entries: road, off-track, boost pads, ice...). Each entry carries the
## multipliers that make a surface feel different -- surface 2 runs at half
## speed and 20% extra braking. Loaded by HandlingProfile from the recovered
## ScriptableObjects; see scripts/handling_profile.gd.
@export var surface_types: Array = []
@export var drift_in_boost_accumulation_factor: float = D.DRIFT_IN_BOOST_ACCUMULATION_FACTOR  # +0xD8
@export var drift_out_boost_accumulation_factor: float = D.DRIFT_OUT_BOOST_ACCUMULATION_FACTOR  # +0xDC

@export_group("Kart-to-kart collision")
@export var slower_kart_bounce_min: float = D.SLOWER_KART_BOUNCE_MIN  # +0x100
@export var slower_kart_bounce_max: float = D.SLOWER_KART_BOUNCE_MAX  # +0x104
@export var faster_kart_bounce_min: float = D.FASTER_KART_BOUNCE_MIN  # +0x108
@export var faster_kart_bounce_max: float = D.FASTER_KART_BOUNCE_MAX  # +0x10C
@export var kart_to_kart_separation_threshold: float = D.KART_TO_KART_SEPARATION_THRESHOLD  # +0x110

@export_group("Kart-to-static collision")
@export var kart_to_static_bounce: float = D.KART_TO_STATIC_BOUNCE  # +0x114
@export var kart_to_static_collision_enter_separation_threshold: float = D.KART_TO_STATIC_COLLISION_ENTER_SEPARATION_THRESHOLD  # +0x118
@export var kart_to_static_collision_stay_separation_threshold: float = D.KART_TO_STATIC_COLLISION_STAY_SEPARATION_THRESHOLD  # +0x11C
@export var kart_to_static_bounce_min: float = D.KART_TO_STATIC_BOUNCE_MIN  # +0x120
@export var kart_to_static_bounce_max: float = D.KART_TO_STATIC_BOUNCE_MAX  # +0x124
@export var kart_to_static_speed_penalty_factor: float = D.KART_TO_STATIC_SPEED_PENALTY_FACTOR  # +0x128
@export var kart_to_static_speed_penalty_min_threshold: float = D.KART_TO_STATIC_SPEED_PENALTY_MIN_THRESHOLD  # +0x12C

@export_group("Crash and air")
@export var crash_speed_threshold: float = D.CRASH_SPEED_THRESHOLD        # +0x130
@export var impact_speed_threshold: float = D.IMPACT_SPEED_THRESHOLD      # +0x134
@export var lost_control_timer_on_crash: float = D.LOST_CONTROL_TIMER_ON_CRASH  # +0x138
@export var collision_enter_cooldown: float = D.COLLISION_ENTER_COOLDOWN  # +0x13C
@export var air_rot_speed_ratio: float = D.AIR_ROT_SPEED_RATIO          # +0x140
@export var rotate_to_vertical_in_air_speed: float = D.ROTATE_TO_VERTICAL_IN_AIR_SPEED  # +0x144
@export var small_landing_speed_vertical: float = D.SMALL_LANDING_SPEED_VERTICAL  # +0xFC

@export_group("Gliding")
@export var gliding_accel_curve: Curve = D.curve_gliding_accel_curve()  # +0x148
@export var gliding_speed_pitch_up: float = D.GLIDING_SPEED_PITCH_UP  # +0x150
@export var gliding_yaw_speed: float = D.GLIDING_YAW_SPEED            # +0x154
## PORT-SIDE. `_glidingSpeed` is a recovered field but is NOT serialized in
## the asset (it is set at runtime from the glider prefab), so there is no
## value to recover. The recovered `_glidingAccelCurve` spans 0..47, which
## brackets this.
@export var gliding_speed: float = 14.0                               # +0x158
@export var gliding_ratio: float = D.GLIDING_RATIO                    # +0x15C
@export var gliding_ratio_pitch_up: float = D.GLIDING_RATIO_PITCH_UP  # +0x160
@export var gliding_pitch_up_max: float = D.GLIDING_PITCH_UP_MAX      # +0x164
@export var gliding_pitch_down_max: float = D.GLIDING_PITCH_DOWN_MAX  # +0x168
@export var gliding_pitch_speed: float = D.GLIDING_PITCH_SPEED        # +0x16C
@export var gliding_pitch_stabilisation: float = D.GLIDING_PITCH_STABILISATION  # +0x170
@export var gliding_roll_max: float = D.GLIDING_ROLL_MAX              # +0x174
@export var gliding_roll_speed: float = D.GLIDING_ROLL_SPEED          # +0x178
@export var gliding_roll_stabilisation: float = D.GLIDING_ROLL_STABILISATION  # +0x17C
@export var gliding_torque: float = D.GLIDING_TORQUE                  # +0x180
@export var gliding_updraft_force: float = D.GLIDING_UPDRAFT_FORCE    # +0x184
@export var gliding_yaw_from_roll: float = D.GLIDING_YAW_FROM_ROLL    # +0x188

@export_group("Hop")
@export var hop_force: float = D.HOP_FORCE                    # +0x18C
@export var hop_gravity_scale: float = D.HOP_GRAVITY_SCALE    # +0x190
@export var hop_timer: float = D.HOP_TIMER                    # +0x194

@export_group("Air control")
@export var lateral_air_grip: float = D.LATERAL_AIR_GRIP      # +0x198
@export var vertical_air_grip: float = D.VERTICAL_AIR_GRIP    # +0x19C

@export_group("Steering lock")
@export var initial_steeringlock: float = D.INITIAL_STEERINGLOCK        # +0x1A0
@export var steeringlock_release_time: float = D.STEERINGLOCK_RELEASE_TIME  # +0x1A4
@export var hit_state_steer_cap: float = D.HIT_STATE_STEER_CAP          # +0x1B4
## PORT-SIDE. `_topSpeed`, `_topAcceleration` and `_steerSpeed` are recovered
## FIELDS but are not serialized in any profile -- they are computed at
## runtime, so no value exists to recover.
@export var top_speed: float = D.SPEED_HARD_CAP                        # +0x1AC
## PORT-SIDE; the recovered forward-accel curve peaks at 17.5 (time 0).
@export var top_acceleration: float = 17.5                             # +0x1B0

@export_group("Port-side")
## PORT-SIDE. In the original this constant is folded into native code rather
## than stored as a field, so it is ours to choose. It sets the base yaw rate;
## the recovered curves modulate around it.
@export var steer_speed: float = 0.9


func _init() -> void:
	if drift_boost_levels.is_empty():
		drift_boost_levels = DriftBoostLevel.make_defaults()


## Engine force at an absolute speed, straight off the recovered curve.
##
## `_forwardAccelCurve` is NOT normalised -- the recovered keyframes are
## (0, 17.5), (8, 13.5), (20.05, 9.34), (30, 3.2), i.e. speed -> force in
## game units. Sampling it by normalised 0..1 would be wrong.
func forward_accel_force(speed: float) -> float:
	return maxf(forward_accel_curve.sample(maxf(speed, 0.0)), 0.0)


## Yaw authority multiplier at an absolute speed (recovered curve, 0..40 domain).
func rot_speed_factor(speed: float) -> float:
	return rot_speed_from_kart_speed_curve.sample(maxf(speed, 0.0))