## PORT-SIDE single-process profile, not recovered campaign/unlock behavior.
## Two independently checksummed generations keep the previous valid save intact
## while writing the next. Flush/read-back detects failures, not power-loss proof.
class_name LocalProfile
extends RefCounted

const Tracks := preload("res://scripts/data/tracks.gd")
const GameDB := preload("res://scripts/data/game_db.gd")
const FORMAT := 1
const MAX_BYTES := 262144
const MAX_EVENTS := 64
const AUDIO_BUSES := ["master", "music", "sfx", "voice", "ambience", "engine"]
var path: String
var data: Dictionary = defaults()
var revision := 0
var active_slot := -1
var read_only := false
var recovered_backup := false
var last_error := ""
var _loaded := false
var _checksum := ""

func _init(base_path: String = "user://profile") -> void:
	path = base_path

static func defaults() -> Dictionary:
	return {"selection": {"track_id": "map_racearlenspeedway", "mode": "race",
		"difficulty": "Hard", "laps": 0, "items": true},
		"settings": {"volumes": {"master": 0.8, "music": 1.0, "sfx": 1.0,
			"voice": 1.0, "ambience": 1.0, "engine": 1.0},
			"muted": false, "shadows": true, "touch_controls": true},
		"stats": {"races": 0, "wins": 0, "best_times": {}}, "recent_events": []}

static func difficulties() -> Array[String]:
	var found: Array[String] = []
	for label in ["VeryEasy", "Easy", "Medium", "Hard", "Legend"]:
		for id in GameDB.ids_of_class("RaceKartAIDefinition"):
			if str(id) == "RaceKartAIDefinition" + label or str(id).begins_with("RaceKartAIDefinition" + label + "_"):
				found.append(label)
				break
	return found

static func race_tracks() -> Array[String]:
	var found: Array[String] = []
	for id: String in Tracks.IDS:
		if not Tracks.is_battle(id) and Tracks.route_knots(id).size() >= 3:
			found.append(id)
	return found

static func _integer(value: Variant, low: int, high: int) -> bool:
	return (value is int or value is float) and is_finite(float(value)) and float(value) == floorf(float(value)) and float(value) >= low and float(value) <= high

static func valid_selection(value: Variant) -> bool:
	if not value is Dictionary or value.size() != 5:
		return false
	return value.get("track_id") is String and race_tracks().has(value.track_id) \
		and value.get("mode") in ["race", "race150"] \
		and value.get("difficulty") in difficulties() \
		and _integer(value.get("laps"), 0, 9) and value.get("items") is bool

static func valid_settings(value: Variant) -> bool:
	if not value is Dictionary or value.size() != 4 or not value.get("volumes") is Dictionary:
		return false
	if value.volumes.size() != AUDIO_BUSES.size():
		return false
	for bus: String in AUDIO_BUSES:
		var volume: Variant = value.volumes.get(bus)
		if not (volume is float or volume is int) or not is_finite(float(volume)) or float(volume) < 0.0 or float(volume) > 1.0:
			return false
	return value.get("muted") is bool and value.get("shadows") is bool and value.get("touch_controls") is bool

static func _valid_data(value: Variant) -> bool:
	if not value is Dictionary or value.size() != 4 or not valid_selection(value.get("selection")) or not valid_settings(value.get("settings")):
		return false
	var stats: Variant = value.get("stats")
	if not stats is Dictionary or stats.size() != 3 or not _integer(stats.get("races"), 0, 1000000000) or not _integer(stats.get("wins"), 0, int(stats.races)) or not stats.get("best_times") is Dictionary:
		return false
	if stats.best_times.size() > 512:
		return false
	for key: Variant in stats.best_times:
		if not key is String:
			return false
		var parts: PackedStringArray = key.split("|")
		var time: Variant = stats.best_times[key]
		if parts.size() != 4 or not race_tracks().has(parts[0]) or not parts[1] in ["race", "race150"] or not parts[2].is_valid_int() or not _integer(parts[2].to_int(), 1, 9) or not parts[3] in ["items", "clean"]:
			return false
		if not (time is int or time is float) or not is_finite(float(time)) or float(time) <= 0.0 or float(time) > 86400.0:
			return false
	var events: Variant = value.get("recent_events")
	if not events is Array or events.size() > MAX_EVENTS:
		return false
	var unique := {}
	for event: Variant in events:
		if not event is String or event.is_empty() or event.length() > 96 or unique.has(event):
			return false
		unique[event] = true
	return true

static func _normalize(value: Dictionary) -> Dictionary:
	# JSON numbers are floats; restore the schema's integral runtime fields.
	var normalized := value.duplicate(true)
	normalized.selection.laps = int(normalized.selection.laps)
	normalized.stats.races = int(normalized.stats.races)
	normalized.stats.wins = int(normalized.stats.wins)
	for bus: String in AUDIO_BUSES:
		normalized.settings.volumes[bus] = float(normalized.settings.volumes[bus])
	return normalized

func _slot(slot: int) -> String:
	return path + ".%d.json" % slot

func _read_slot(slot: int) -> Dictionary:
	if not FileAccess.file_exists(_slot(slot)):
		return {"code": ERR_FILE_NOT_FOUND}
	var file := FileAccess.open(_slot(slot), FileAccess.READ)
	if file == null:
		return {"code": FileAccess.get_open_error()}
	if file.get_length() <= 0 or file.get_length() > MAX_BYTES:
		return {"code": ERR_FILE_CORRUPT}
	var text := file.get_as_text()
	file.close()
	var parser := JSON.new()
	if parser.parse(text) != OK or not parser.data is Dictionary:
		return {"code": ERR_FILE_CORRUPT}
	var envelope: Dictionary = parser.data
	if _integer(envelope.get("format"), FORMAT + 1, 2147483647):
		return {"code": ERR_UNAVAILABLE}
	if envelope.size() != 4 or envelope.get("format") != FORMAT or not _integer(envelope.get("revision"), 1, 2147483647) or not envelope.get("payload") is String or not envelope.get("sha256") is String:
		return {"code": ERR_FILE_CORRUPT}
	if envelope.payload.sha256_text() != envelope.sha256:
		return {"code": ERR_FILE_CORRUPT}
	if parser.parse(envelope.payload) != OK or not _valid_data(parser.data):
		return {"code": ERR_FILE_CORRUPT}
	return {"code": OK, "revision": int(envelope.revision), "checksum": envelope.sha256,
		"data": _normalize(parser.data), "slot": slot}

func _snapshot() -> Dictionary:
	var slots: Array[Dictionary] = [_read_slot(0), _read_slot(1)]
	if slots[0].code == ERR_UNAVAILABLE or slots[1].code == ERR_UNAVAILABLE:
		return {"code": ERR_UNAVAILABLE}
	var valid: Array[Dictionary] = []
	var damaged := false
	for slot in slots:
		if slot.code == OK:
			valid.append(slot)
		elif slot.code != ERR_FILE_NOT_FOUND:
			damaged = true
	if valid.is_empty():
		return {"code": ERR_FILE_CORRUPT if damaged else ERR_FILE_NOT_FOUND}
	if valid.size() == 2 and valid[0].revision == valid[1].revision and valid[0].checksum != valid[1].checksum:
		return {"code": ERR_FILE_CORRUPT}
	valid.sort_custom(func(a: Dictionary, b: Dictionary): return a.revision > b.revision)
	valid[0]["recovered"] = damaged
	return valid[0]

func load_profile() -> Error:
	_loaded = true
	read_only = false
	recovered_backup = false
	last_error = ""
	var saved := _snapshot()
	if saved.code == ERR_FILE_NOT_FOUND:
		data = defaults()
		revision = 0
		active_slot = -1
		_checksum = ""
		return OK
	if saved.code != OK:
		read_only = true
		return _fail(saved.code, "A newer profile format was found; saves are disabled." if saved.code == ERR_UNAVAILABLE else "No valid profile generation could be read; existing files are preserved.")
	data = saved.data.duplicate(true)
	revision = saved.revision
	active_slot = saved.slot
	_checksum = saved.checksum
	recovered_backup = saved.recovered
	return OK

func _fail(code: Error, message: String) -> Error:
	last_error = message
	return code

func _save(candidate: Dictionary) -> Error:
	if not _loaded:
		return _fail(ERR_UNCONFIGURED, "Load the profile before saving.")
	if read_only:
		return _fail(ERR_UNAVAILABLE, "Profile is read-only; existing files were not changed.")
	if not _valid_data(candidate):
		return _fail(ERR_INVALID_DATA, "Invalid profile update was rejected.")
	var current := _snapshot()
	if (revision == 0 and current.code != ERR_FILE_NOT_FOUND) or (revision > 0 and (current.code != OK or current.get("revision", -1) != revision or current.get("checksum", "") != _checksum)):
		return _fail(ERR_BUSY, "Profile changed since loading; reload before saving.")
	if revision >= 2147483647:
		return _fail(ERR_UNAVAILABLE, "Profile revision limit reached.")
	var payload := JSON.stringify(candidate, "", true, true)
	var envelope := {"format": FORMAT, "revision": revision + 1,
		"payload": payload, "sha256": payload.sha256_text()}
	var text := JSON.stringify(envelope, "", true, true)
	if text.to_utf8_buffer().size() > MAX_BYTES:
		return _fail(ERR_INVALID_DATA, "Profile exceeds its storage budget.")
	var directory := ProjectSettings.globalize_path(path).get_base_dir()
	var error := DirAccess.make_dir_recursive_absolute(directory)
	if error != OK:
		return _fail(error, "Profile directory could not be created.")
	var next_slot := 0 if active_slot < 0 else 1 - active_slot
	var file := FileAccess.open(_slot(next_slot), FileAccess.WRITE)
	if file == null:
		return _fail(FileAccess.get_open_error(), "Profile could not be opened for writing.")
	file.store_string(text)
	file.flush()
	error = file.get_error()
	file.close()
	if error != OK:
		return _fail(error, "Profile write failed; the previous generation remains intact.")
	var verified := _read_slot(next_slot)
	if verified.code != OK or verified.get("revision", -1) != revision + 1 or verified.get("checksum", "") != envelope.sha256:
		return _fail(ERR_FILE_CORRUPT, "Profile read-back failed; the previous generation remains intact.")
	data = candidate.duplicate(true)
	revision += 1
	active_slot = next_slot
	_checksum = envelope.sha256
	last_error = ""
	return OK

func save_selection(selection: Dictionary) -> Error:
	var candidate := data.duplicate(true)
	candidate.selection = selection.duplicate(true)
	return _save(candidate)

func save_settings(settings: Dictionary) -> Error:
	var candidate := data.duplicate(true)
	candidate.settings = settings.duplicate(true)
	return _save(candidate)

static func record_key(selection: Dictionary) -> String:
	var laps := int(selection.laps)
	if laps == 0:
		laps = maxi(int(Tracks.get_track(selection.track_id).get("laps", 3)), 1)
	return "%s|%s|%d|%s" % [selection.track_id, selection.mode, laps, "items" if selection.items else "clean"]

func best_time(selection: Dictionary) -> float:
	return float(data.stats.best_times.get(record_key(selection), -1.0)) if valid_selection(selection) else -1.0

func record_result(event_id: String, selection: Dictionary, finished: bool, place: int, time: float) -> Error:
	if event_id.is_empty() or event_id.length() > 96 or not valid_selection(selection) or place < 1 or place > 10 or not is_finite(time) or (finished and (time <= 0.0 or time > 86400.0)) or (not finished and time != -1.0):
		return _fail(ERR_INVALID_DATA, "Invalid race result was rejected.")
	# Bounded replay protection for the latest 64 local events. The session
	# controller additionally rejects callbacks from replaced race instances.
	if data.recent_events.has(event_id):
		return OK
	var candidate := data.duplicate(true)
	candidate.stats.races = int(candidate.stats.races) + 1
	if finished and place == 1:
		candidate.stats.wins = int(candidate.stats.wins) + 1
	if finished:
		var key := record_key(selection)
		candidate.stats.best_times[key] = minf(float(candidate.stats.best_times.get(key, time)), time)
	candidate.recent_events.append(event_id)
	while candidate.recent_events.size() > MAX_EVENTS:
		candidate.recent_events.pop_front()
	return _save(candidate)
