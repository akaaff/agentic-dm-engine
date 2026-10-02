import type { EquipmentSummary } from '../api/client'

// Issue #67: a TypeScript port of src/engine/rules.py's
// weapon_combo_is_legal, so the Character Creator's Equipment step can show
// *why* a pick wouldn't be equipped instead of silently accepting it and
// having character_creation.py's auto-equip loop quietly drop it later. Both
// read the same "light"/"two-handed" property tags from the same SRD data
// (EquipmentSummary.properties is straight off the vendored equipment
// entries), so there's no hardcoded weapon list on either side to drift -
// keep the check order and meaning below in lockstep with the Python.
//
// weaponComboProblem is the single source of truth: weaponComboIsLegal is
// defined as "no problem", so the yes/no answer and the explanation can
// never disagree.

// Matches equipmentDetail.ts's own shield detection (there's exactly one
// SRD shield, indexed "shield").
export function isShield(item: EquipmentSummary): boolean {
  return item.category === 'armor' && item.name.toLowerCase().includes('shield')
}

function hasProperty(item: EquipmentSummary, property: string): boolean {
  return item.properties.includes(property)
}

/** Why `weapons` (plus a shield, if `shieldEquipped`) can't all be wielded at
 * once, or null if it's a legal loadout - at most 2 weapons; a pair must be
 * two Light weapons with neither two-handed; and a shield takes one of the
 * two hands, so it can't join a pair or a two-handed weapon. */
export function weaponComboProblem(
  weapons: EquipmentSummary[],
  shieldEquipped: boolean,
): string | null {
  if (weapons.length > 2) return 'Only 2 weapons can be equipped at once.'

  if (weapons.length === 2) {
    const twoHanded = weapons.find((w) => hasProperty(w, 'two-handed'))
    if (twoHanded) {
      return `${twoHanded.name} is two-handed - it can't be paired with another weapon.`
    }
    const notLight = weapons.filter((w) => !hasProperty(w, 'light'))
    if (notLight.length > 0) {
      const names = notLight.map((w) => w.name).join(' and ')
      return `${names} can't be dual-wielded - only Light weapons can be paired.`
    }
    if (shieldEquipped) {
      return "Two weapons plus a shield is three hands - drop one of them, or the shield."
    }
  } else if (weapons.length === 1 && shieldEquipped) {
    if (hasProperty(weapons[0], 'two-handed')) {
      return `${weapons[0].name} is two-handed - it can't be used alongside a shield.`
    }
  }
  return null
}

export function weaponComboIsLegal(weapons: EquipmentSummary[], shieldEquipped: boolean): boolean {
  return weaponComboProblem(weapons, shieldEquipped) === null
}
