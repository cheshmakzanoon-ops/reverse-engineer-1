## Per-kart item slot; recovered counts/timers, PORT-SIDE activation policy.
## Integer equip/activate/reequip fields are interpreted as milliseconds.
## Native animation-event timing has not been recovered or validated.
class_name KartInventory
extends RefCounted
const DB := preload("res://scripts/data/game_db.gd")
signal changed()
var usable_id := ""
var definition: Dictionary = {}
var charges := 0
var cooldown := 0.0
var clock := 0.0
var last_error := ""
var received: Dictionary = {}
var received_at: Dictionary = {}

func is_empty() -> bool:
	return charges == 0

func can_use() -> bool:
	return charges > 0 and cooldown <= 0.0

func grant(id: String) -> bool:
	if not is_empty():
		return false
	var data := DB.lookup(id)
	var general: Dictionary = data.get("_generalData", {})
	var count := int(general.get("_numberOfUses", 0))
	if count <= 0:
		last_error = "Unresolved usable definition: " + id
		return false
	var canonical := str(data.get("_id", id))
	var limit := int(general.get("_pickupTotalLimit", -1))
	if limit >= 0 and int(received.get(canonical, 0)) >= limit:
		return false
	if clock - float(received_at.get(canonical, -INF)) < float(general.get("_pickupCooldown", 0.0)):
		return false
	usable_id = canonical
	definition = data
	charges = count
	cooldown = maxf(float(general.get("_equippingTime", 0)) * 0.001, 0.0)
	received[canonical] = int(received.get(canonical, 0)) + 1
	received_at[canonical] = clock
	last_error = ""
	changed.emit()
	return true

func tick(delta: float) -> void:
	if delta <= 0.0 or not is_finite(delta):
		return
	clock += delta
	cooldown = maxf(cooldown - delta, 0.0)

## A rejected or unavailable effect never spends a charge or disappears.
func activate(executor: Callable) -> bool:
	if not can_use():
		return false
	if not executor.is_valid() or not executor.call(usable_id, definition):
		last_error = "Effect unavailable: " + usable_id
		changed.emit()
		return false
	var general: Dictionary = definition["_generalData"]
	cooldown = maxf(float(general.get("_activatingTime", 0)), float(general.get("_reequipTime", 0))) * 0.001
	charges -= 1
	last_error = ""
	if charges == 0:
		usable_id = ""
		definition = {}
	changed.emit()
	return true

func clear_slot() -> void:
	# PORT-SIDE recovery drops the held item, but never resets race pickup caps.
	usable_id = ""
	definition = {}
	charges = 0
	cooldown = 0.0
	last_error = ""
	changed.emit()

func display_name() -> String:
	if is_empty():
		return "ITEM: empty"
	var family := "Item"
	if definition.has("_boostUsableData"):
		family = "Boost"
	elif definition.has("_superShieldUsableData"):
		family = "Super shield"
	elif definition.has("_shieldUsableData"):
		family = "Shield"
	elif definition.has("_goldenTurdData"):
		family = "Rear hazard"
	elif definition.has("_ricochetData"):
		family = "Ricochet"
	elif definition.has("_proximityHomingData"):
		family = "Homing"
	elif definition.has("_placementHomingData"):
		family = "Leader seeker"
	elif definition.has("_satelliteUsableData"):
		family = "Satellite"
	return "%s ×%d%s" % [family, charges, " (equipping)" if cooldown > 0.0 else ""]
