## Local persistence behavior; fixtures are isolated from the user's profile.
extends SceneTree
var passed := 0
var failed := 0
var base := ""

func _init() -> void:
	call_deferred("run")

func check(label: String, value: bool) -> void:
	passed += 1 if value else 0
	failed += 0 if value else 1
	print("  ", "ok " if value else "FAIL ", label)

func write_file(path: String, text: String) -> void:
	var file := FileAccess.open(path, FileAccess.WRITE)
	file.store_string(text)
	file.close()

func run() -> void:
	if not ResourceLoader.exists("res://scripts/local_profile.gd"):
		check("local profile persistence is implemented", false)
		print("PASS: %d FAIL: %d" % [passed, failed])
		quit(1)
		return
	var Script = load("res://scripts/local_profile.gd")
	base = "user://profile-test-%d-%d" % [OS.get_process_id(), Time.get_ticks_usec()]
	var profile = Script.new(base)
	check("write before load cannot overwrite an unseen save", profile.save_selection(profile.data.selection) == ERR_UNCONFIGURED)
	check("new profile initializes without fabricating race results", profile.load_profile() == OK and profile.data.stats.races == 0)
	var selection: Dictionary = profile.data.selection.duplicate(true)
	selection["mode"] = "race150"
	selection["laps"] = 1
	check("selection saves successfully", profile.save_selection(selection) == OK)
	check("first write creates one verified generation", profile.revision == 1 and FileAccess.file_exists(base + ".0.json"))
	var reload = Script.new(base)
	check("fresh instance restores selection", reload.load_profile() == OK and reload.data.selection == selection)
	var settings: Dictionary = reload.data.settings.duplicate(true)
	settings["muted"] = true
	settings["volumes"]["music"] = 0.35
	check("settings save to the other slot", reload.save_settings(settings) == OK and reload.revision == 2 and FileAccess.file_exists(base + ".1.json"))
	check("stale instance cannot overwrite newer progress", profile.save_selection(selection) == ERR_BUSY and profile.revision == 1)
	var latest = Script.new(base)
	check("highest verified generation wins", latest.load_profile() == OK and latest.revision == 2 and latest.data.settings.muted)
	check("volume round-trips", is_equal_approx(float(latest.data.settings.volumes.music), 0.35))
	var invalid: Dictionary = selection.duplicate(true)
	invalid.track_id = "map_battlebobsfunland"
	check("arena cannot be saved as a race selection", latest.save_selection(invalid) == ERR_INVALID_DATA)
	invalid.track_id = "missing-track"
	check("unknown track is rejected", latest.save_selection(invalid) == ERR_INVALID_DATA)
	invalid = selection.duplicate(true)
	invalid.laps = -1
	check("negative lap count is rejected", latest.save_selection(invalid) == ERR_INVALID_DATA)
	settings.volumes.music = NAN
	check("non-finite volume is rejected", latest.save_settings(settings) == ERR_INVALID_DATA)
	settings = latest.data.settings.duplicate(true)
	settings.volumes.music = 2.0
	check("out-of-range volume is rejected", latest.save_settings(settings) == ERR_INVALID_DATA)
	check("invalid updates leave saved revision untouched", latest.revision == 2)
	check("completed event is recorded", latest.record_result("event-1", selection, true, 1, 105.25) == OK)
	check("race count and win count increment", latest.data.stats.races == 1 and latest.data.stats.wins == 1)
	var revision: int = latest.revision
	check("duplicate finish is idempotent", latest.record_result("event-1", selection, true, 1, 105.25) == OK and latest.revision == revision and latest.data.stats.races == 1)
	check("slower finish does not replace best time", latest.record_result("event-2", selection, true, 2, 120.0) == OK and is_equal_approx(latest.best_time(selection), 105.25))
	check("DNF records participation but never a best time", latest.record_result("event-3", selection, false, 6, -1.0) == OK and latest.data.stats.races == 3 and latest.data.stats.wins == 1 and is_equal_approx(latest.best_time(selection), 105.25))
	check("invalid finish time cannot enter records", latest.record_result("event-4", selection, true, 1, INF) == ERR_INVALID_DATA)
	var other_laps: Dictionary = selection.duplicate(true)
	other_laps.laps = 3
	check("records do not mix lap counts", latest.best_time(other_laps) == -1.0)
	var restored = Script.new(base)
	check("results survive a new instance", restored.load_profile() == OK and restored.data.stats.races == 3 and restored.data.stats.wins == 1)
	check("saved idempotency survives restart", restored.record_result("event-3", selection, false, 6, -1.0) == OK and restored.data.stats.races == 3)
	var good_revision: int = restored.revision - 1
	var broken_path: String = base + ".%d.json" % restored.active_slot
	write_file(broken_path, '{"payload": "truncated')
	var fallback = Script.new(base)
	check("truncated latest write recovers prior generation", fallback.load_profile() == OK and fallback.recovered_backup and fallback.revision == good_revision)
	check("recovery can make a new verified save", fallback.save_selection(selection) == OK)
	var changed: String = FileAccess.get_file_as_string(base + ".%d.json" % fallback.active_slot)
	changed = changed.replace('"sha256":', '"wrong_checksum_key":')
	write_file(base + ".%d.json" % fallback.active_slot, changed)
	var checksum = Script.new(base)
	check("invalid checksum envelope falls back rather than trusting payload", checksum.load_profile() == OK and checksum.recovered_backup)
	for slot in 2:
		write_file(base + ".%d.json" % slot, "corrupted")
	var corrupt = Script.new(base)
	check("two corrupt generations are reported, not silently reset", corrupt.load_profile() == ERR_FILE_CORRUPT and corrupt.read_only)
	check("corrupt profile cannot overwrite the evidence", corrupt.save_selection(selection) == ERR_UNAVAILABLE and FileAccess.get_file_as_string(base + ".0.json") == "corrupted")
	for slot in 2:
		DirAccess.remove_absolute(base + ".%d.json" % slot)
	var future = Script.new(base)
	check("fresh profile can be saved for future-format fixture", future.load_profile() == OK and future.save_selection(selection) == OK)
	var envelope: Dictionary = JSON.parse_string(FileAccess.get_file_as_string(base + ".0.json"))
	envelope["format"] = 2
	write_file(base + ".1.json", JSON.stringify(envelope))
	var newer = Script.new(base)
	check("newer save format blocks downgrade even with an older valid slot", newer.load_profile() == ERR_UNAVAILABLE and newer.read_only)
	var blocked = Script.new("res://project.godot/profile-test")
	check("unwritable destination reports failure", blocked.load_profile() == OK and blocked.save_selection(selection) != OK and blocked.revision == 0)
	for slot in 2:
		DirAccess.remove_absolute(base + ".%d.json" % slot)
	print("PASS: %d FAIL: %d" % [passed, failed])
	quit(1 if failed else 0)
