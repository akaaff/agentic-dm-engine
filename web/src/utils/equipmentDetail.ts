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
