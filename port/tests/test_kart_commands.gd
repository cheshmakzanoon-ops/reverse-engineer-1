## Synthetic multi-touch, input ownership and physics regressions; not a device test.
extends SceneTree
var passed := 0
var failed := 0
const DT := 1.0 / 60.0

func _init() -> void:
	call_deferred("run")

func check(label: String, condition: bool) -> void:
	if condition:
		passed += 1
		print("  ok   ", label)
	else:
		failed += 1
		print("  FAIL ", label)

func press(controls: TouchControls, id: int, action: String) -> void:
	var event := InputEventScreenTouch.new()
	event.index = id
	event.position = controls.control_center(action)
	event.pressed = true
	controls._input(event)

func release(controls: TouchControls, id: int, cancel: bool = false) -> void:
	var event := InputEventScreenTouch.new()
	event.index = id
	event.canceled = cancel
	controls._input(event)

func run() -> void:
	var kart := Kart.new()
	root.add_child(kart)
	kart.set_physics_process(false)
	var other := Kart.new()
	root.add_child(other)
	other.set_physics_process(false)
	var controls := TouchControls.new()
	root.add_child(controls)
	await process_frame
	var driver := PlayerDriver.new()
	driver.kart = kart
	driver.touch = controls
	kart.add_child(driver)
	kart.driver = driver
	var ai := KartAI.new()
	ai.kart = other
	other.add_child(ai)
	other.driver = ai
	check("each kart owns a different command object", kart.command != other.command)

	kart.command.set_drive(INF, NAN, true)
	check("non-finite analog inputs become safe zero", kart.command.throttle == 0.0 and kart.command.steer == 0.0)
	kart.command.set_drive(2.0, -4.0)
	check("analog input is clamped", kart.command.throttle == 1.0 and kart.command.steer == -1.0)
	kart.command.reset_requested = true
	check("reset is consumed once", kart.command.consume_reset() and not kart.command.consume_reset())
	kart.command.use_item_requested = true
	check("item activation is consumed once", kart.command.consume_item() and not kart.command.consume_item())

	Input.action_press("accelerate", 0.4)
	driver.update_command(DT)
	check("keyboard analog throttle reaches only player", is_equal_approx(kart.command.throttle, 0.4) and other.command.throttle == 0.0)
	Input.action_release("accelerate")
	press(controls, 1, "accelerate")
	press(controls, 2, "steer")
	var drag := InputEventScreenDrag.new()
	drag.index = 2
	drag.position = controls.control_center("steer") + Vector2(controls.control_radius("steer") * 0.375, 0)
	controls._input(drag)
	press(controls, 3, "drift")
	driver.update_command(DT)
	check("three fingers preserve throttle, analog steer and drift", kart.command.throttle == 1.0 and is_equal_approx(kart.command.steer, 0.375) and kart.command.drift)
	check("touch never writes keyboard actions", not Input.is_action_pressed("accelerate") and not Input.is_action_pressed("handbrake"))
	ai._drive(-0.2)
	check("AI steer does not overwrite touch player steer", is_equal_approx(kart.command.steer, 0.375) and is_equal_approx(other.command.steer, -0.2))
	release(controls, 3)
	driver.update_command(DT)
	check("releasing drift does not release other fingers", kart.command.throttle == 1.0 and kart.command.steer > 0.3 and not kart.command.drift)
	press(controls, 4, "brake")
	driver.update_command(DT)
	check("brake wins while accelerator is held", kart.command.throttle == -1.0)
	release(controls, 4)
	driver.update_command(DT)
	check("releasing brake restores held accelerator", kart.command.throttle == 1.0)
	release(controls, 2, true)
	driver.update_command(DT)
	check("cancelled steering finger clears only steering", kart.command.steer == 0.0 and kart.command.throttle == 1.0)
	controls.notification(Node.NOTIFICATION_APPLICATION_FOCUS_OUT)
	driver.update_command(DT)
	check("focus loss clears all touch input", kart.command.throttle == 0.0 and not controls.has_active_input())
	press(controls, 5, "reset")
	driver.update_command(DT)
	check("touch reset is a one-shot command", kart.command.consume_reset())
	driver.update_command(DT)
	check("held reset does not repeat", not kart.command.consume_reset())
	controls.set_enabled(false)
	press(controls, 6, "accelerate")
	check("disabled controls reject touch input", not controls.has_active_input() and controls.throttle == 0.0)
	controls.set_enabled(true)
	check("re-enabling does not resurrect held fingers", not controls.has_active_input())
	controls.set_virtual(0.7, -0.4, true)
	driver.update_command(DT)
	check("virtual adapter uses the real command path", is_equal_approx(kart.command.throttle, 0.7) and is_equal_approx(kart.command.steer, -0.4) and kart.command.drift)
	kart.set_driving_enabled(false)
	driver.update_command(DT)
	check("countdown lock suppresses driving commands", kart.command.throttle == 0.0 and not kart.command.drift)
	kart.set_driving_enabled(true)
	controls.release_all()

	kart.global_basis = Basis.IDENTITY
	kart.velocity = Vector3(0, 0, 20)
	kart.speed = 20.0
	kart._steer_input = 0.5
	kart.command.set_drive(1, 0.5, true)
	kart._update_drift(DT)
	kart.drift_elapsed = 7.0
	kart._update_drift(DT)
	check("charging a drift does not pay boost every frame", kart.is_drifting and not kart.is_boosting)
	kart.command.drift = false
	kart._update_drift(DT)
	var boost_velocity := kart.velocity
	check("drift release awards the earned tier once", not kart.is_drifting and kart.is_boosting and kart._boost_timer > 0.0)
	kart._update_drift(DT)
	check("released drift cannot award a second impulse", kart.velocity.is_equal_approx(boost_velocity))
	kart.is_drifting = true
	kart.drift_elapsed = 8.0
	var target := Transform3D(Basis(Vector3.UP, 1.2), Vector3(12, 15, -8))
	kart.respawn_at(target)
	check("respawn clears boost, drift, velocity and commands", not kart.is_boosting and not kart.is_drifting and kart.velocity == Vector3.ZERO and kart.command.throttle == 0.0)
	check("respawn preserves requested position and orientation", kart.global_transform.is_equal_approx(target))

	ai.set_line(PackedVector3Array([Vector3.ZERO, Vector3(0, 0, 8), Vector3(0, 0, 16), Vector3(0, 0, 24)]))
	other.global_position = Vector3(0, 0, 12)
	other.speed = 0.0
	ai._advance_cursor(DT)
	check("AI cursor uses actual segment and sub-segment position", ai._cursor == 1 and is_equal_approx(ai._segment_fraction, 0.5))
	var cursor := ai._cursor
	var fraction := ai._segment_fraction
	other.speed = 100.0
	for i in 120:
		ai._advance_cursor(DT)
	check("reported speed alone cannot advance AI route progress", ai._cursor == cursor and is_equal_approx(ai._segment_fraction, fraction))

	kart.queue_free()
	other.queue_free()
	controls.queue_free()
	await process_frame
	print("PASS: %d   FAIL: %d" % [passed, failed])
	quit(1 if failed else 0)
