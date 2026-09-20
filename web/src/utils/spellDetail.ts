import type { SpellSummary } from '../api/client'

// Issue #30's real SRD spell mechanical detail (level/casting time/range,
// plus whichever of damage/heal/DC applies) - same spirit and shape as
// equipmentDetail.ts's issue #15 formatting.

function ordinalLevel(level: number): string {
  if (level === 0) return 'cantrip'
  if (level === 1) return '1st'
  if (level === 2) return '2nd'
  if (level === 3) return '3rd'
  return `${level}th`
}

function shortRange(range: string): string {
  return range.replace(/\bfeet\b/i, 'ft').replace(/\bfoot\b/i, 'ft')
}

export function spellDetail(spell: SpellSummary): string {
  const header = [ordinalLevel(spell.level), spell.casting_time, shortRange(spell.range)]
    .filter(Boolean)
    .join(', ')

  const effectParts: string[] = []
  if (spell.damage_dice) {
    effectParts.push([spell.damage_dice, spell.damage_type].filter(Boolean).join(' '))
  }
  if (spell.heal_dice) {
    effectParts.push(`heals ${spell.heal_dice}`)
  }
  if (spell.dc_type) {
    effectParts.push(`${spell.dc_type} save${spell.dc_success === 'half' ? ' half' : ''}`)
  }
  const effect = effectParts.join(', ')
  return effect ? `${header}: ${effect}` : header
}

export function nameWithSpellDetail(spell: SpellSummary): string {
  const detail = spellDetail(spell)
  return detail ? `${spell.name} (${detail})` : spell.name
}

// Issue #51: the mechanical detail above (level/range/damage) doesn't say
// what a spell actually *does* - a player who doesn't already know 5e
// spells by name can't tell what "Acid Splash" is from "cantrip, 1 action,
// 60 ft: 1d6 acid, DEX save" alone. spell.desc carries the real SRD prose
// (already reaching the frontend since issue #30, just never rendered).
//
// An earlier version rendered a truncated, first-sentence-only excerpt as
// an always-visible line under every entry - but that cut off before the
// actual mechanic for most spells (True Strike's first sentence is pure
// flavor; the real payoff, "you gain advantage on your first attack
// roll," is two sentences later) and cluttered the list either way. Now
// shown via the same hidden InfoTip tooltip the skill-choice picker
// already uses (CharacterCreator's skillDesc/InfoTip pairing) - full,
// untruncated text, hidden until hovered/focused - so callers pass
// spell.desc.trim() straight to InfoTip rather than through a formatting
// helper here.
