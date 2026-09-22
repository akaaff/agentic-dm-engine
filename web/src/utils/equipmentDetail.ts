import type { EquipmentSummary } from '../api/client'

// Issue #15's real SRD stats formatting (weapon damage/properties, armor
// AC/Dex-cap/stealth) - originally built inline in CharacterDetailSheet.tsx,
// extracted here (issue #31) so CharacterCreator's Equipment step can show
// the same detail while picking gear, not just after creation.
export function equipmentDetail(item: EquipmentSummary): string {
  if (item.category === 'weapon') {
    const parts: string[] = []
    if (item.damage_dice) parts.push([item.damage_dice, item.damage_type].filter(Boolean).join(' '))
    if (item.properties.length > 0) parts.push(item.properties.join(', '))
    return parts.join(', ')
  }
  if (item.ac_base !== null && item.ac_base !== undefined) {
    const isShield = item.name.toLowerCase().includes('shield')
    const dexNote = item.ac_dex_bonus
      ? item.ac_max_bonus !== null
        ? ` + Dex (max ${item.ac_max_bonus})`
        : ' + Dex'
      : ''
    const parts = [isShield ? `+${item.ac_base} AC${dexNote}` : `AC ${item.ac_base}${dexNote}`]
    if (item.stealth_disadvantage) parts.push('disadvantage on Stealth')
    return parts.join(', ')
  }
  return ''
}

export function nameWithEquipmentDetail(item: EquipmentSummary): string {
  const detail = equipmentDetail(item)
  return detail ? `${item.name} (${detail})` : item.name
}

// Follow-up to issue #51's spell/resource hints: equipmentDetail() above is
// already the compact *mechanical* line (the equivalent of spellDetail(),
// not spellHint()) - weapon/armor property jargon like "finesse"/
// "versatile"/"loading" was shown with zero explanation anywhere. Unlike
// spells, the vendored SRD equipment data has no narrative `desc` field at
// all for weapons/armor and this project doesn't vendor a separate
// weapon-properties reference file - so, same as RESOURCE_HINTS, these are
// hand-authored one-liners, not surfaced backend data. Covers every
// property index actually used across the vendored equipment data
// (confirmed directly, not guessed): ammunition, finesse, heavy, light,
// loading, monk, reach, special, thrown, two-handed, versatile.
const PROPERTY_HINTS: Record<string, string> = {
  ammunition: 'Needs ammunition (arrows, bolts, etc.) and a free hand to fire at range.',
  finesse: 'Can use Dexterity instead of Strength for its attack and damage rolls.',
  heavy: "Real SRD gives Small creatures disadvantage with heavy weapons - not modeled here, since this engine has no creature-size mechanic.",
  light: 'Light enough to dual-wield - attack with a second light weapon as a bonus action.',
  loading: "Can only be fired once per attack action, no matter how many attacks you'd otherwise get.",
  monk: "Counts as a monk weapon - a Monk can use their Martial Arts die with it instead of the weapon's own.",
  reach: "Extends this weapon's reach by 5 feet.",
  special: 'Has its own unique rules beyond the properties listed here.',
  thrown: "Can be thrown for a ranged attack, using the same ability modifier as swinging it.",
  'two-handed': 'Requires both hands to use.',
  versatile:
    'Can be wielded one- or two-handed - two-handed normally deals more damage, though this engine always uses the one-handed damage shown above.',
}

export function equipmentHint(item: EquipmentSummary): string {
  if (item.category === 'weapon') {
    return item.properties
      .map((p) => PROPERTY_HINTS[p])
      .filter((h): h is string => Boolean(h))
      .join(' ')
  }
  if (item.ac_base !== null && item.ac_base !== undefined) {
    const isShield = item.name.toLowerCase().includes('shield')
    const parts: string[] = []
    if (isShield) {
      parts.push('Worn on one hand: a flat AC bonus that applies regardless of Dexterity.')
    } else if (item.ac_dex_bonus) {
      parts.push(
        item.ac_max_bonus !== null
          ? `Your Dexterity modifier adds to this armor's AC, up to a cap of ${item.ac_max_bonus}.`
          : "Your full Dexterity modifier adds to this armor's AC.",
      )
    } else {
      parts.push("This armor's AC is fixed - Dexterity doesn't add to it.")
    }
    if (item.stealth_disadvantage) parts.push('Wearing it gives disadvantage on Stealth checks.')
    return parts.join(' ')
  }
  return ''
}
