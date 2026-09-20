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

const MAX_HINT_LENGTH = 320

// Issue #51: the mechanical detail above (level/range/damage) doesn't say
// what a spell actually *does* - a player who doesn't already know 5e
// spells by name can't tell what "Acid Splash" is from "cantrip, 1 action,
// 60 ft: 1d6 acid, DEX save" alone. spell.desc carries the real SRD prose
// (already reaching the frontend since issue #30, just never rendered) -
// capped in length for a compact list context, not shown in full (a raw
// SRD description can run several sentences, meant for a spellbook page,
// not a one-line list entry).
//
// Live-found: an earlier version stopped after the *first* sentence only,
// but roughly two-thirds of the SRD's own level 0-1 spells lead with pure
// scene-setting flavor text before the sentence that actually explains the
// mechanic - True Strike's first sentence is just "You extend your hand
// and point a finger at a target in range," with the real payoff ("you
// gain advantage on your first attack roll") two sentences later. Fixed by
// accumulating whole sentences up to the length cap instead of stopping at
// the first one - long enough to reach the payoff sentence for the common
// case (confirmed against a sample including True Strike, Animal
// Friendship, Sleep, Eldritch Blast), short enough to stay a hint rather
// than the full description.
export function spellHint(spell: SpellSummary): string {
  const trimmed = spell.desc.trim()
  const sentences = trimmed.split(/(?<=[.!?])\s+/)
  let result = ''
  for (const sentence of sentences) {
    const candidate = result ? `${result} ${sentence}` : sentence
    if (candidate.length > MAX_HINT_LENGTH) {
      if (!result) {
        // Even the first sentence alone exceeds the cap - hard-truncate it
        // rather than return nothing.
        return `${sentence.slice(0, MAX_HINT_LENGTH - 1).trimEnd()}...`
      }
      break
    }
    result = candidate
  }
  return result
}
