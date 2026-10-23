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

function stringList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === 'string') : []
}

/**
 * How the d20 was rolled, from an attack event's roll_mode / reasons: a short tag
 * always (so a roll with advantage or disadvantage never looks like an ordinary one,
 * and a cancelled pair says so), and the reasons behind each kind in debug mode.
 */
function formatRollMode(p: Record<string, unknown>, debugMode: boolean): string {
  const advantage = stringList(p.advantage_reasons)
  const disadvantage = stringList(p.disadvantage_reasons)
  if (advantage.length === 0 && disadvantage.length === 0) return ''
  const mode = p.roll_mode
  const tag =
    mode === 'advantage'
      ? 'advantage'
      : mode === 'disadvantage'
        ? 'disadvantage'
        : 'advantage and disadvantage cancel out'
  if (!debugMode) return `, ${tag}`
  if (mode === 'advantage') return `, advantage: ${advantage.join(', ')}`
  if (mode === 'disadvantage') return `, disadvantage: ${disadvantage.join(', ')}`
  const reasons = [
    ...(advantage.length > 0 ? [`advantage: ${advantage.join(', ')}`] : []),
    ...(disadvantage.length > 0 ? [`disadvantage: ${disadvantage.join(', ')}`] : []),
  ]
  return `, ${tag} (${reasons.join('; ')})`
}

/** Debug-mode damage breakdown, e.g. "damage: Dagger 1d4 4 + DEX mod 3 + Sneak Attack 1d6 4 = 11". */
function formatDamageBreakdown(breakdown: unknown): string {
  if (!Array.isArray(breakdown)) return ''
  const entries = breakdown.filter(isBreakdownEntry)
  if (entries.length === 0) return ''
  const total = entries.reduce((sum, [, value]) => sum + value, 0)
  return ` [damage: ${entries.map(([label, value]) => `${label} ${value}`).join(' + ')} = ${total}]`
}

/**
 * The extra dice a hit carried and why: Sneak Attack (with what triggered it), Divine
 * Smite, Divine Favor and Hunter's Mark. Always shown, since they are what explains a
 * hit that looks too big for the weapon. In debug mode a Rogue's hit that did NOT
 * sneak attack also says why not.
 */
function formatRiders(p: Record<string, unknown>, debugMode: boolean): string {
  const parts: string[] = []
  const sneak = p.sneak_attack
  if (sneak && typeof sneak === 'object') {
    const info = sneak as Record<string, unknown>
    if (info.applied === true) {
      const triggers = stringList(info.triggers)
      parts.push(
        `Sneak Attack +${info.damage}${triggers.length > 0 ? ` (${triggers.join(' and ')})` : ''}`,
      )
    } else if (debugMode && typeof info.reason === 'string') {
      parts.push(`no Sneak Attack: ${info.reason}`)
    }
  }
  const flat: Array<[string, string]> = [
    ['divine_smite_damage', 'Divine Smite'],
    ['divine_favor_damage', 'Divine Favor'],
    ['hunters_mark_damage', "Hunter's Mark"],
  ]
  for (const [key, label] of flat) {
    if (typeof p[key] === 'number') parts.push(`${label} +${p[key]}`)
  }
  return parts.length > 0 ? ` - ${parts.join(', ')}` : ''
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
    case 'spell_cast': {
      const spell = typeof p.spell === 'string' ? p.spell : 'a spell'
      const natural = typeof p.natural === 'number' ? p.natural : null
      const damage = typeof p.damage === 'number' ? p.damage : null
      const targetIds = Array.isArray(p.targets) ? p.targets : p.target ? [p.target] : []
      const targetText =
        targetIds.length > 0
          ? ` at ${targetIds.map((t) => characterName(t, characters)).join(', ')}`
          : ''
      if (p.auto_hit === true) {
        // Magic Missile: one badge per dart, no roll to make.
        return {
          key: event.id,
          color,
          label: `${actorName}'s ${spell} dart${targetText} - auto-hit`,
          highlight: damage !== null ? { text: `${damage}`, color: DAMAGE_COLOR } : undefined,
        }
      }
      if (typeof p.save_success === 'boolean') {
        // The target's save has its own badge; this one is what the spell then did.
        const rolled = typeof p.damage_rolled === 'number' ? p.damage_rolled : null
        const dice = typeof p.damage_dice === 'string' ? p.damage_dice : null
        const halved = p.save_success && rolled !== null && rolled !== damage
        const detail = debugMode
          ? ` [${dice ?? 'damage'}${rolled !== null ? ` rolled ${rolled}` : ''}${halved ? ', halved by the save' : ''}]`
          : halved
            ? ' (halved by the save)'
            : ''
        return {
          key: event.id,
          color,
          label: `${actorName} casts ${spell}${targetText} - ${p.save_success ? 'target saved' : 'target failed the save'}${detail}`,
          highlight: damage !== null ? { text: `${damage}`, color: DAMAGE_COLOR } : undefined,
        }
      }
      if (typeof p.hit === 'boolean') {
        const rollTotal = typeof p.roll_total === 'number' ? p.roll_total : null
        const modeText = formatRollMode(p, debugMode)
        const naturalText = natural !== null ? ` (natural ${natural}${modeText})` : ''
        const breakdownText = debugMode
          ? formatBreakdown(natural, p.attack_bonus_breakdown, rollTotal)
          : ''
        const damageText = debugMode && p.hit ? formatDamageBreakdown(p.damage_breakdown) : ''
        return {
          key: event.id,
          color,
          label: `${actorName} casts ${spell}${targetText} - ${p.hit ? 'hit' : 'miss'}${naturalText}${breakdownText}${damageText}`,
          highlight:
            natural === 20
              ? { text: 'Critical Hit!', color: CRIT_COLOR }
              : natural === 1
                ? { text: 'Critical Miss!', color: FUMBLE_COLOR }
                : undefined,
        }
      }
      return {
        key: event.id,
        color,
        label: `${actorName} casts ${spell}${targetText.replace(' at ', ' on ')}`,
      }
    }
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
      const modeText = formatRollMode(p, debugMode)
      const naturalText =
        natural !== null ? ` (natural ${natural}${modeText})` : modeText ? ` (${modeText.slice(2)})` : ''
      const highlight =
        natural === 20
          ? { text: 'Critical Hit!', color: CRIT_COLOR }
          : natural === 1
            ? { text: 'Critical Miss!', color: FUMBLE_COLOR }
            : undefined
      const breakdownText = debugMode
        ? formatBreakdown(natural, p.attack_bonus_breakdown, rollTotal)
        : ''
      const riderText = hit ? formatRiders(p, debugMode) : ''
      const damageText = debugMode && hit ? formatDamageBreakdown(p.damage_breakdown) : ''
      return {
        key: event.id,
        color,
        label: `${actorName} attacks ${target} - ${hit ? 'hit' : 'miss'}${naturalText}${breakdownText}${riderText}${damageText}`,
        highlight,
      }
    }
    case 'damage_dealt':
    case 'hazard_damage': {
      const amount = typeof p.amount === 'number' ? p.amount : 0
      const target = characterName(p.target, characters)
      // What changed the hit on its way in (resistance, temporary HP, a target with less HP
      // left than the damage). "Only N HP left" happens on every kill, so it stays debug-only.
      const adjustments = stringList(p.adjustments).filter(
        (a) => debugMode || !a.startsWith('only '),
      )
      const rolled = typeof p.rolled === 'number' ? `rolled ${p.rolled}, ` : ''
      const adjustText = adjustments.length > 0 ? ` (${rolled}${adjustments.join('; ')})` : ''
      return {
        key: event.id,
        color,
        label:
          (event.type === 'hazard_damage'
            ? `${target} takes hazard damage`
            : `${actorName} hits ${target}`) + adjustText,
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
    case 'ready':
      return { key: event.id, color, label: `${actorName} readies an attack` }
    case 'readied_attack': {
      const target = characterName(p.target, characters)
      return { key: event.id, color, label: `${actorName}'s readied attack triggers on ${target}` }
    }
    case 'grapple_escape': {
      const success = p.success === true
      return {
        key: event.id,
        color,
        label: `${actorName} ${success ? 'breaks free of the grapple' : 'fails to break free'}`,
      }
    }
    case 'divine_sense': {
      // Paladin's Divine Sense (issue #87): who it picked up within 60 ft.
      const detected: { id?: unknown }[] = Array.isArray(p.detected) ? p.detected : []
      const names = detected
        .map((d) => characterName(typeof d.id === 'string' ? d.id : '', characters))
        .join(', ')
      return {
        key: event.id,
        color,
        label: detected.length
          ? `${actorName}'s Divine Sense detects: ${names}`
          : `${actorName}'s Divine Sense finds nothing within 60 ft`,
      }
    }
    case 'spell_ruling': {
      // The referee's verdict on a no-combat utility spell (issue #55).
      const outcome = String(p.outcome)
      const words = outcome === 'success' ? 'worked' : outcome === 'partial' ? 'partly worked' : 'failed'
      const ruling = typeof p.ruling === 'string' && p.ruling ? ` - ${p.ruling}` : ''
      return {
        key: event.id,
        color,
        label: `${actorName}'s ${String(p.spell)} ${words}${ruling}`,
        highlight: {
          text: outcome,
          color: outcome === 'success' ? HEAL_COLOR : outcome === 'partial' ? '#e0b030' : DAMAGE_COLOR,
        },
      }
    }
    case 'summoned': {
      // A summoning spell added a creature to the fight (issue #56).
      const name = typeof p.name === 'string' ? p.name : characterName(String(p.summoned), characters)
      return { key: event.id, color, label: `${actorName} conjures ${name}` }
    }
    case 'summon_ended': {
      const name = typeof p.name === 'string' ? p.name : String(p.summoned)
      return { key: event.id, color, label: `${name} vanishes as ${actorName}'s spell ends` }
    }
    case 'temp_hp': {
      // Temporary hit points (issue #94): gained from a spell/feature, or soaked up
      // by damage (a negative change).
      const change = typeof p.change === 'number' ? p.change : 0
      const target = characterName(typeof p.target === 'string' ? p.target : event.actor, characters)
      if (change < 0) {
        return {
          key: event.id,
          color,
          label: `${target}'s temporary HP soak up damage`,
          highlight: { text: `${change} temp HP`, color: HEAL_COLOR },
        }
      }
      const source = typeof p.source === 'string' ? ` (${p.source})` : ''
      return {
        key: event.id,
        color,
        label: `${target} gains temporary HP${source}`,
        highlight: { text: `+${change} temp HP`, color: HEAL_COLOR },
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
    case 'protection': {
      // Protection fighting style (issue #92): the reaction that gave the
      // coming attack disadvantage - the attack_roll badge after it has the roll.
      const target = characterName(p.target, characters)
      return { key: event.id, color, label: `${actorName} protects ${target}` }
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
      if (p.reason === 'healed') {
        return { key: event.id, color, label: `${target} regains consciousness` }
      }
      if (p.reason === 'stood up') {
        return { key: event.id, color, label: `${target} stands up` }
      }
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
      // A spell save is logged under the caster, but it is the target's roll.
      const saver =
        kind === 'spell_save' && typeof p.target === 'string'
          ? characterName(p.target, characters)
          : actorName
      const abilityText = typeof p.ability === 'string' ? `${p.ability} ` : ''
      const dcText = typeof p.dc === 'number' ? ` vs DC ${p.dc}` : ''
      return {
        key: event.id,
        color,
        label: `${saver}'s ${abilityText}${kind.replace(/_/g, ' ')}${dcText} - ${success ? 'success' : 'fail'}${breakdownText}`,
      }
    }
    case 'skill_check': {
      const skill = typeof p.skill === 'string' ? p.skill : 'check'
      const success = p.success === true
      // A social check aimed at an enemy (issue #102) names who it was aimed at.
      const targetText = typeof p.target === 'string' ? ` on ${characterName(p.target, characters)}` : ''
      const natural = typeof p.natural === 'number' ? p.natural : null
      const rollTotal = typeof p.roll_total === 'number' ? p.roll_total : null
      const breakdownText = debugMode
        ? formatBreakdown(natural, p.modifier_breakdown, rollTotal)
        : ''
      return {
        key: event.id,
        color,
        label: `${actorName}'s ${skill} check${targetText} - ${success ? 'success' : 'fail'}${breakdownText}`,
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
