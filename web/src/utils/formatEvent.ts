import type { LiveCharacter, LiveEvent } from '../ws/sessionClient'
import { conditionDisplayName, isSpellEffectCondition } from './conditionDetail'

export const DAMAGE_COLOR = '#ff6b6b'
export const HEAL_COLOR = '#4dabff'
export const CRIT_COLOR = '#4caf50'
export const FUMBLE_COLOR = '#ff6b6b'

export interface FormattedEventBadge {
  key: string
  /** The acting character's color (from actorColors.ts) - a small colored
   * accent on the badge, matching that character's token/sheet color. */
  color: string
  /** Short mechanical summary, e.g. "Kael attacks Goblin 3 - hit (natural 18)". */
  label: string
  /** A single colored callout, when there is one - a damage/heal amount or
   * a crit/fumble tag. At most one per badge (keeps each entry scannable). */
  highlight?: { text: string; color: string }
}

function characterName(id: unknown, characters: Record<string, LiveCharacter>): string {
  if (typeof id !== 'string') return 'something'
  return characters[id]?.name ?? id
}

/** A [label, value] pair from turn_engine.py's own attack_bonus_breakdown/
 * modifier_breakdown (issue #38) - Python tuples serialize to 2-element JSON
 * arrays, so this is read defensively rather than assumed, same as every
 * other payload field in this file. */
function isBreakdownEntry(value: unknown): value is [string, number] {
  return (
    Array.isArray(value) &&
    value.length === 2 &&
    typeof value[0] === 'string' &&
    typeof value[1] === 'number'
  )
}

/** Debug-mode roll breakdown, e.g. "14 + STR mod 3 + proficiency 2 = 19" -
 * appended to a badge's label only when debugMode is on, so ordinary play
 * stays uncluttered. `natural` is the kept d20 (undefined for a flat/no-
 * modifier roll like a death save, which also has no breakdown to show). */
function formatBreakdown(natural: number | null, breakdown: unknown, total: number | null): string {
  if (!Array.isArray(breakdown) || breakdown.length === 0) return ''
  const entries = breakdown.filter(isBreakdownEntry)
  if (entries.length === 0) return ''
  const parts = [
    ...(natural !== null ? [String(natural)] : []),
    ...entries.map(([label, value]) => `${label} ${value}`),
  ]
  const totalText = total !== null ? ` = ${total}` : ''
  return ` [${parts.join(' + ')}${totalText}]`
}

/**
 * Formats one event into a small colored badge, or null for event types not
 * worth surfacing this way (dodge/disengage/rage/help - payload-empty or low
 * -value - and move, which would be noisy: every single companion
 * reposition). Deliberately only covers the "mechanically interesting"
 * subset confirmed live against turn_engine.py's real payload shapes (see
 * CLAUDE.md) - a payload field is read defensively (optional chaining /
 * type checks), not assumed, since the same event `type` can carry
 * different fields depending on which code path emitted it (e.g.
 * `saving_throw`'s `kind` sub-shapes, `hp_change` not always having a
 * `target` key).
 */
export function formatEvent(
  event: LiveEvent,
  characters: Record<string, LiveCharacter>,
  actorColors: Record<string, string>,
  debugMode: boolean = false,
): FormattedEventBadge | null {
  const color = actorColors[event.actor] ?? '#9d94ad'
  const actorName = characterName(event.actor, characters)
  const p = event.payload

  switch (event.type) {
    case 'initiative_rolled': {
      const natural = typeof p.natural === 'number' ? p.natural : null
      const modifier = typeof p.modifier === 'number' ? p.modifier : null
      const total = typeof p.total === 'number' ? p.total : null
      const rollText = natural !== null && modifier !== null ? ` (d20 ${natural}${modifier >= 0 ? '+' : ''}${modifier})` : ''
      return {
        key: event.id,
        color,
        label: `${actorName} rolls initiative: ${total ?? '?'}${rollText}`,
      }
    }
    case 'attack_roll': {
      const natural = typeof p.natural === 'number' ? p.natural : null
      const rollTotal = typeof p.roll_total === 'number' ? p.roll_total : null
      const hit = p.hit === true
      const target = characterName(p.target, characters)
      const naturalText = natural !== null ? ` (natural ${natural})` : ''
      const highlight =
        natural === 20
          ? { text: 'Critical Hit!', color: CRIT_COLOR }
          : natural === 1
            ? { text: 'Critical Miss!', color: FUMBLE_COLOR }
            : undefined
      const breakdownText = debugMode
        ? formatBreakdown(natural, p.attack_bonus_breakdown, rollTotal)
        : ''
      return {
        key: event.id,
        color,
        label: `${actorName} attacks ${target} - ${hit ? 'hit' : 'miss'}${naturalText}${breakdownText}`,
        highlight,
      }
    }
    case 'damage_dealt':
    case 'hazard_damage': {
      const amount = typeof p.amount === 'number' ? p.amount : 0
      const target = characterName(p.target, characters)
      return {
        key: event.id,
        color,
        label: event.type === 'hazard_damage' ? `${target} takes hazard damage` : `${actorName} hits ${target}`,
        highlight: { text: `-${amount} HP`, color: DAMAGE_COLOR },
      }
    }
    case 'hp_change': {
      const amount = typeof p.amount === 'number' ? p.amount : 0
      const targetId = typeof p.target === 'string' ? p.target : event.actor
      const target = characterName(targetId, characters)
      const source = typeof p.source === 'string' ? p.source : 'healing'
      return {
        key: event.id,
        color,
        label: `${target} recovers HP (${source})`,
        highlight: { text: `+${amount} HP`, color: HEAL_COLOR },
      }
    }
    case 'death':
      return { key: event.id, color, label: `${actorName} has fallen` }
    case 'relentless_endurance':
      return {
        key: event.id,
        color,
        label: `${actorName} refuses to fall - Relentless Endurance!`,
        highlight: { text: '1 HP', color: HEAL_COLOR },
      }
    case 'death_ward':
      // Death Ward (issue #59): the spell turns a would-be 0 HP into 1 HP,
      // then ends - payload is just the warded character's id (the actor).
      return {
        key: event.id,
        color,
        label: `${actorName}'s Death Ward holds - they stay on their feet!`,
        highlight: { text: '1 HP', color: HEAL_COLOR },
      }
    case 'mirror_image': {
      // Mirror Image (issue #61): an attack aimed at the image's owner was
      // redirected to a duplicate instead. actor is the attacker, target the
      // image's owner; image_hit says whether that attack then actually hit
      // (and so destroyed) the duplicate.
      const target = characterName(p.target, characters)
      const hit = p.image_hit === true
      const left = typeof p.images_remaining === 'number' ? ` (${p.images_remaining} left)` : ''
      return {
        key: event.id,
        color,
        label: `${actorName}'s attack ${hit ? 'destroys a mirror image of' : 'misses a mirror image of'} ${target}${left}`,
      }
    }
    case 'sanctuary_blocked': {
      // Sanctuary (issue #61): the attacker failed the Wisdom save and lost
      // the attack - the saving_throw badge just before this one has the roll.
      const target = characterName(p.target, characters)
      return {
        key: event.id,
        color,
        label: `${actorName}'s attack on ${target} is turned aside by Sanctuary`,
      }
    }
    case 'condition_applied': {
      const condition = typeof p.condition === 'string' ? p.condition : 'a condition'
      const targetId = typeof p.target === 'string' ? p.target : event.actor
      const target = characterName(targetId, characters)
      // A spell's ongoing effect has an internal snake_case id ("death_
      // warded") and reads better as "affected by Death Ward" than "now
      // death_warded"; an SRD condition keeps its plain "is now blinded".
      const text = isSpellEffectCondition(condition)
        ? `affected by ${conditionDisplayName(condition)}`
        : `now ${condition}`
      return { key: event.id, color, label: `${target} is ${text}` }
    }
    case 'condition_removed': {
      // Emitted when a caster's concentration ends (a failed save, a new
      // concentration spell, going unconscious) and strips what it was
      // sustaining, or when an effect is otherwise cut short - plain time
      // expiry stays silent.
      const condition = typeof p.condition === 'string' ? p.condition : 'a condition'
      const spell = typeof p.spell === 'string' ? p.spell : conditionDisplayName(condition)
      const reason = typeof p.reason === 'string' ? ` (${p.reason})` : ''
      const target = characterName(event.actor, characters)
      return { key: event.id, color, label: `${spell} ends on ${target}${reason}` }
    }
    case 'saving_throw': {
      const kind = typeof p.kind === 'string' ? p.kind : 'save'
      const success = p.success === true
      const natural = typeof p.natural === 'number' ? p.natural : null
      const rollTotal = typeof p.roll_total === 'number' ? p.roll_total : null
      const breakdownText = debugMode
        ? formatBreakdown(natural, p.modifier_breakdown, rollTotal)
        : ''
      return {
        key: event.id,
        color,
        label: `${actorName}'s ${kind.replace(/_/g, ' ')} - ${success ? 'success' : 'fail'}${breakdownText}`,
      }
    }
    case 'skill_check': {
      const skill = typeof p.skill === 'string' ? p.skill : 'check'
      const success = p.success === true
      const natural = typeof p.natural === 'number' ? p.natural : null
      const rollTotal = typeof p.roll_total === 'number' ? p.roll_total : null
      const breakdownText = debugMode
        ? formatBreakdown(natural, p.modifier_breakdown, rollTotal)
        : ''
      return {
        key: event.id,
        color,
        label: `${actorName}'s ${skill} check - ${success ? 'success' : 'fail'}${breakdownText}`,
      }
    }
    case 'grapple_attempt':
    case 'shove_attempt': {
      const success = p.success === true
      const target = characterName(p.target, characters)
      const verb = event.type === 'grapple_attempt' ? 'grapples' : 'shoves'
      return { key: event.id, color, label: `${actorName} ${verb} ${target} - ${success ? 'success' : 'fail'}` }
    }
    default:
      return null
  }
}
