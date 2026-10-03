## Regression test for the recovered kart tuning.
##
##   godot --headless --path . --script res://tests/test_recovered_tuning.gd
##
## Exits 0 on success, 1 on the first mismatch.
##
## This test exists because a silent failure got through once already: Godot's
## `Curve.add_point` clamps to min/max domain and min/max value, which both
## default to 0..1. These curves are in absolute game units, so every keyframe
## collapsed onto (1,1) -- the script compiled, imported and ran with zero
## errors while throwing away all 15 recovered keyframes. Asserting on actual
## values, not just "it runs", is the only way that shows up.
##
## Expected values are the game's own, extracted from
## definitions_assets_all_*.bundle profile `KartPhysicsHandlingDefault`.

extends SceneTree

const EPS := 0.01

var failures := 0
var checks := 0


func _init() -> void:
	var h := KartPhysicsHandling.new()

	# --- scalars: the game's own values ---------------------------------
	_expect("speed_hard_cap", h.speed_hard_cap, 55.0)
	_expect("braking", h.braking, 21.0)
	_expect("tire_grip", h.tire_grip, 3.992)
	_expect("tire_grip_drift", h.tire_grip_drift, 2.1)
	_expect("drift_speed_min", h.drift_speed_min, 16.0)
	_expect("boost", h.boost, 0.8)
	_expect("boost_speed_offset", h.boost_speed_offset, 10.0)
	_expect("kart_to_kart_separation_threshold", h.kart_to_kart_separation_threshold, 99.0)
	_expect("crash_speed_threshold", h.crash_speed_threshold, 17.0)
	_expect("slope_compensation_angle_start", h.slope_compensation_angle_start, 45.0)
	_expect("slope_compensation_angle_max", h.slope_compensation_angle_max, 60.0)
	_expect("gliding_torque", h.gliding_torque, 48.0)

	# --- forward accel curve: absolute speed -> engine force -------------
	var c := h.forward_accel_curve
	_expect("forwardAccelCurve.point_count", c.point_count, 4)
	var want := [Vector2(0.0, 17.5), Vector2(8.0, 13.5),
		Vector2(20.05, 9.34), Vector2(30.0, 3.2)]
	if c.point_count == want.size():
		for i in want.size():
			var p := c.get_point_position(i)
			_expect("forwardAccelCurve[%d].x" % i, p.x, want[i].x)
			_expect("forwardAccelCurve[%d].y" % i, p.y, want[i].y)
	else:
		_fail("forwardAccelCurve has %d points, expected %d" % [c.point_count, want.size()])

	# Sampling the curve must reproduce the keyframe values, not a clamped 1.0.
	_expect("force@0", h.forward_accel_force(0.0), 17.5)
	_expect("force@8", h.forward_accel_force(8.0), 13.5)
	_expect("force@20", h.forward_accel_force(20.0), 9.34, 0.05)
	_expect("force@30", h.forward_accel_force(30.0), 3.2)

	# --- yaw authority curve --------------------------------------------
	_expect("rotfac@0", h.rot_speed_factor(0.0), 1.3)
	_expect("rotfac@40", h.rot_speed_factor(40.0), 0.931357, 0.001)

	# --- other curves must not be empty either --------------------------
	_expect("reverseAccelCurve.point_count", h.reverse_accel_curve.point_count, 2)
	_expect("glidingAccelCurve.point_count", h.gliding_accel_curve.point_count, 2)
	_expect("rotSpeedFromKartSpeedCurve.point_count",
		h.rot_speed_from_kart_speed_curve.point_count, 5)
	_expect("rotSpeedFromSteerAngleCurve.point_count",
		h.rot_speed_from_steer_angle_curve.point_count, 2)

	# --- drift boost levels ---------------------------------------------
	_expect("drift_boost_levels.size", h.drift_boost_levels.size(), 4)
	if h.drift_boost_levels.size() == 4:
		_expect("levels[0].is_valid", h.drift_boost_levels[0].is_valid(), false)
		_expect("levels[3].is_valid", h.drift_boost_levels[3].is_valid(), true)
		# Real recovered levels, smallest boost first.
		_expect("levels[1].time_to_activate", h.drift_boost_levels[1].time_to_activate, 2.0)
		_expect("levels[1].drift_boost_ratio", h.drift_boost_levels[1].drift_boost_ratio, 0.24)
		_expect("levels[2].time_to_activate", h.drift_boost_levels[2].time_to_activate, 4.2)
		_expect("levels[3].time_to_activate", h.drift_boost_levels[3].time_to_activate, 6.9)
		_expect("levels[3].drift_boost_ratio", h.drift_boost_levels[3].drift_boost_ratio, 0.52)
		_expect("levels[3].drift_steer_max", h.drift_boost_levels[3].drift_steer_max, 1.21)

	print("---")
	if failures == 0:
		print("PASS: %d checks" % checks)
		quit(0)
	else:
		print("FAIL: %d of %d checks failed" % [failures, checks])
		quit(1)


func _expect(label: String, got, want, eps: float = EPS) -> void:
	checks += 1
	var ok := false
	if typeof(got) == TYPE_BOOL or typeof(want) == TYPE_BOOL:
		ok = (got == want)
	else:
		ok = absf(float(got) - float(want)) <= eps
	if not ok:
		failures += 1
		print("FAIL %s: got %s, expected %s" % [label, got, want])


func _fail(msg: String) -> void:
	checks += 1
	failures += 1
	print("FAIL %s" % msg)