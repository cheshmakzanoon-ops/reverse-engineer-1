## Verifies the recovered game database actually loads and is internally
## consistent.
##
## tools/dump_definitions.py + tools/game_defs_to_gdscript.py turn the game's
## 814 shipped ScriptableObjects into GDScript `const` data. That is a lot of
## generated source, and two things can silently go wrong: a definition that
## fails to parse, and a `_refID` pointing at an id the database does not
## actually contain. Both compile and run fine -- they just quietly lose data.
## So this asserts on content, not on "it loads".

extends SceneTree

const GameDB := preload("res://scripts/data/game_db.gd")

var _passed := 0
var _failed := 0


func _init() -> void:
	print("=== recovered game database ===")

	# --- the database as a whole -------------------------------------------
	expect_gt("index holds every definition", GameDB.INDEX.size(), 800)
	expect_eq("index has no duplicate ids", _duplicate_ids(), 0)

	# --- the handling profiles, which the port drives physics from ---------
	var profiles := GameDB.handling.BY_ID
	expect_eq("all 6 handling profiles present", profiles.size(), 6)
	for p in ["Default", "Battle", "Race150", "RaceAI", "Shooter",
			  "ShooterExtraSlow"]:
		var def: Dictionary = GameDB.lookup("KartPhysicsHandling" + p)
		expect_true("handling profile %s resolves" % p, not def.is_empty())
		if def.is_empty():
			continue
		# 93 fields is the count recovered from dump.cs; a profile missing
		# scalars means the typetree dump lost a field.
		expect_gt("  %s carries its tuning scalars" % p, def.size(), 80)
		var levels: Array = def.get("_driftBoostLevelsTable", [])
		expect_eq("  %s has 3 drift boost levels" % p, levels.size(), 3)
		if levels.size() == 3:
			# The shipped table is stored in the game's own order: level 3
			# first, then 2, then 1. It is ordered by `_driftBoostType`, and the
			# runtime picks the highest tier whose `_timeToActivate` the current
			# drift has already earned. So the ladder's meaning is checked by
			# sorting on the type -- asserting on array order instead would be
			# asserting the reverse of the design.
			var by_type := levels.duplicate()
			by_type.sort_custom(func(a, b):
				return float(a["_driftBoostType"]) < float(b["_driftBoostType"]))
			expect_eq("  %s ladder covers types 1-3" % p,
				[float(by_type[0]["_driftBoostType"]),
				 float(by_type[1]["_driftBoostType"]),
				 float(by_type[2]["_driftBoostType"])], [1.0, 2.0, 3.0])
			# Ascending tier must get strictly better: more time to earn it,
			# bigger payout, longer boost.
			expect_true("  %s boost ladder improves with tier" % p,
				float(by_type[0]["_timeToActivate"]) < float(by_type[1]["_timeToActivate"])
				and float(by_type[1]["_timeToActivate"]) < float(by_type[2]["_timeToActivate"]))
			expect_true("  %s boost ratio improves with tier" % p,
				float(by_type[0]["_driftBoostRatio"]) < float(by_type[1]["_driftBoostRatio"])
				and float(by_type[1]["_driftBoostRatio"]) < float(by_type[2]["_driftBoostRatio"]))
			expect_true("  %s boost duration improves with tier" % p,
				float(by_type[0]["_driftBoostDuration"]) < float(by_type[1]["_driftBoostDuration"])
				and float(by_type[1]["_driftBoostDuration"]) < float(by_type[2]["_driftBoostDuration"]))
			# Level 3 is the payoff: it must be worth roughly half again what
			# level 1 gives, or the whole three-step ladder is pointless.
			expect_gt("  %s top tier beats bottom tier" % p,
				float(by_type[2]["_driftBoostRatio"]) / float(by_type[0]["_driftBoostRatio"]), 1.5)
		expect_eq("  %s has 7 surface types" % p,
			def.get("KartHandlingSurfaceTypes", []).size(), 7)

	# Battle and Shooter must NOT share Default's physics -- if they did, the
	# port would be running one profile everywhere and look plausible.
	var dflt := GameDB.lookup("KartPhysicsHandlingDefault")
	var battle := GameDB.lookup("KartPhysicsHandlingBattle")
	var shooter := GameDB.lookup("KartPhysicsHandlingShooter")
	expect_ne("Battle differs from Default", battle["_speedHardCap"] == dflt["_speedHardCap"]
			and battle["_glidingTorque"] == dflt["_glidingTorque"], true)
	expect_true("Shooter caps speed lower than Default",
		float(shooter["_speedHardCap"]) < float(dflt["_speedHardCap"]))

	# --- cross-definition references --------------------------------------
	# The game's definitions reference each other by string id. A dangling id
	# means the port will look up a kart's wheels or a pickup table's usables
	# and find nothing, so the references are checked rather than trusted.
	var dangling := _dangling_refs()
	expect_eq("every _refID resolves to a real definition", dangling.size(), 0,
		"%d dangling: %s" % [dangling.size(), str(dangling.slice(0, 5))])

	# A real defect in the shipped data, and the reason lookup() is
	# case-insensitive: these chapters spell the ip id "IP_GENERIC" while the
	# definition ships as "IP_Generic". The original game still shows its
	# single-player menu, so it resolves the mismatch rather than choking.
	# Checked against the raw index, not has_def(): has_def() is deliberately
	# case-insensitive, so asking it would confirm nothing about the mismatch.
	expect_true("shipped data really does contain the IP_GENERIC mismatch",
		not GameDB.INDEX.has("IP_GENERIC")
		and GameDB.INDEX.has("IP_Generic")
		and (GameDB.lookup("Chapter_Legend_1").get("_ipDefinitionRef", {}) as Dictionary)
			.get("_refID", "") == "IP_GENERIC")
	expect_eq("  ...and lookup() resolves it anyway",
		GameDB.lookup("IP_GENERIC").get("_id", ""), "IP_Generic")

	# --- the content the port needs to build a race -----------------------
	expect_eq("karts", GameDB.catalog.BY_ID.size(), 371)
	expect_gt("maps are present", _count_class("MapDefinition"), 20)
	expect_gt("characters are present", _count_class("CharacterDefinition"), 40)
	expect_gt("pickup tables are present", _count_class("PickupTableDefinition"), 20)
	expect_eq("arcade modes", _count_class("ArcadeModeDefinition"), 3)

	# --- curves survive as real keyframes ---------------------------------
	# Godot's Curve clamps added points into 0..1 unless the range is widened.
	# A recovered curve collapsing to a single corner point is the exact bug
	# this guards: it looks fine and holds no data.
	var accel: Dictionary = dflt.get("_forwardAccelCurve", {})
	expect_true("forward accel curve is recovered data",
		GameDB.is_curve(accel))
	if GameDB.is_curve(accel):
		var keys: Array = accel["__curve__"]
		expect_gt("forward accel curve keeps its keyframes", keys.size(), 1)
		expect_gt("forward accel curve spans real time", float(keys[-1][0]), 1.0)
		var c: Curve = GameDB.curve(accel)
		expect_gt("forward accel curve domain was widened", c.max_domain, 1.0)
		expect_gt("forward accel curve value range was widened", c.max_value, 1.0)
		# Sample well past the domain edge: a collapsed curve returns the same
		# value everywhere, a real one keeps moving.
		expect_ne("forward accel curve is not collapsed flat",
			c.sample_baked(0.0), c.sample_baked(c.max_domain * 0.5))

	print("\nPASS: %d   FAIL: %d" % [_passed, _failed])
	quit(1 if _failed > 0 else 0)


func _duplicate_ids() -> int:
	var seen := {}
	var dupes := 0
	for id in GameDB.INDEX:
		if seen.has(id):
			dupes += 1
		seen[id] = true
	return dupes


func _count_class(short: String) -> int:
	var n := 0
	for id in GameDB.INDEX:
		if (GameDB.INDEX[id]["class"] as String).ends_with("." + short):
			n += 1
	return n


func _dangling_refs() -> Array:
	var out: Array = []
	for domain in ["handling", "catalog", "pickups", "modes", "misc"]:
		var table: Dictionary = GameDB.get_domain(domain)
		for id in table:
			_walk_refs(table[id], "%s/%s" % [domain, id], out)
	return out


func _walk_refs(node, where: String, out: Array) -> void:
	if node is Dictionary:
		if node.has("_refID"):
			var rid: String = node["_refID"]
			# Empty refs are legal and mean "generic" in this game's data.
			if rid != "" and not GameDB.has_def(rid):
				out.append("%s -> %s" % [where, rid])
			return
		for k in node:
			_walk_refs(node[k], where, out)
	elif node is Array:
		for v in node:
			_walk_refs(v, where, out)


func expect_eq(label: String, got, want, note := "") -> void:
	if got == want:
		_passed += 1
		print("  ok   %s%s" % [label, ("  " + note) if note != "" else ""])
	else:
		_failed += 1
		print("  FAIL %s: got %s, want %s%s"
			% [label, str(got), str(want), ("  " + note) if note != "" else ""])


func expect_ne(label: String, got, unwanted, note := "") -> void:
	if got != unwanted:
		_passed += 1
		print("  ok   %s" % label)
	else:
		_failed += 1
		print("  FAIL %s: unexpectedly equal%s" % [label, ("  " + note) if note != "" else ""])


func expect_gt(label: String, got, floor, note := "") -> void:
	if float(got) > float(floor):
		_passed += 1
		print("  ok   %s%s" % [label, ("  " + note) if note != "" else ""])
	else:
		_failed += 1
		print("  FAIL %s: %s <= %s%s"
			% [label, str(got), str(floor), ("  " + note) if note != "" else ""])


func expect_true(label: String, cond: bool, note := "") -> void:
	expect_eq(label, cond, true, note)