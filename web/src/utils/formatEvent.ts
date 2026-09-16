import type { LiveCharacter, LiveEvent } from '../ws/sessionClient'

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
      const hit = p.hit === true
      const target = characterName(p.target, characters)
      const naturalText = natural !== null ? ` (natural ${natural})` : ''
      const highlight =
        natural === 20
          ? { text: 'Critical Hit!', color: CRIT_COLOR }
          : natural === 1
            ? { text: 'Critical Miss!', color: FUMBLE_COLOR }
            : undefined
      return {
        key: event.id,
        color,
        label: `${actorName} attacks ${target} - ${hit ? 'hit' : 'miss'}${naturalText}`,
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
    case 'condition_applied': {
      const condition = typeof p.condition === 'string' ? p.condition : 'a condition'
      const targetId = typeof p.target === 'string' ? p.target : event.actor
      const target = characterName(targetId, characters)
      return { key: event.id, color, label: `${target} is now ${condition}` }
    }
    case 'saving_throw': {
      const kind = typeof p.kind === 'string' ? p.kind : 'save'
      const success = p.success === true
      return {
        key: event.id,
        color,
        label: `${actorName}'s ${kind.replace(/_/g, ' ')} - ${success ? 'success' : 'fail'}`,
      }
    }
    case 'skill_check': {
      const skill = typeof p.skill === 'string' ? p.skill : 'check'
      const success = p.success === true
      return {
        key: event.id,
        color,
        label: `${actorName}'s ${skill} check - ${success ? 'success' : 'fail'}`,
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
