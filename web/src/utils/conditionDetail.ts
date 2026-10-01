import type { LiveCondition } from '../ws/sessionClient'

// Condensed from the real SRD condition text (data/srd/5e-SRD-Conditions.json)
// - this is a closed, SRD-fixed vocabulary that never changes with game
// state, so a small static map here (same precedent as CharacterDetailSheet.
// tsx's own RESOURCE_HINTS) is simpler than a new backend endpoint for
// something this static. "blessed" isn't a real SRD condition (it's the
// Bless spell's own effect, issue #57's reuse of this same condition
// machinery) - hand-written since there's no SRD text for it.
const CONDITION_DESCRIPTIONS: Record<string, string> = {
  blinded:
    "Can't see - automatically fails sight-based checks. Attacks against it have advantage; its own attacks have disadvantage.",
  charmed:
    "Can't attack the charmer or target them with harmful effects. The charmer has advantage on social checks against it.",
  deafened: "Can't hear - automatically fails hearing-based checks.",
  frightened:
    'Disadvantage on ability checks and attack rolls while the source of fear is in sight. Can’t willingly move closer to it.',
  grappled: "Speed becomes 0 and it can't benefit from speed bonuses. Ends if the grappler is incapacitated or removed.",
  incapacitated: "Can't take actions or reactions.",
  invisible:
    'Impossible to see without magic. Attacks against it have disadvantage; its own attacks have advantage.',
  paralyzed:
    "Incapacitated, can't move or speak, auto-fails STR/DEX saves. Attacks against it have advantage and auto-crit within 5ft.",
  petrified:
    'Turned to stone - incapacitated, unaware of surroundings, auto-fails STR/DEX saves, resistant to all damage, immune to poison/disease.',
  poisoned: 'Disadvantage on attack rolls and ability checks.',
  prone:
    'Can only crawl unless it stands up. Disadvantage on its own attacks; attacks against it have advantage within 5ft, disadvantage beyond.',
  restrained:
    "Speed becomes 0. Attacks against it have advantage, its own attacks have disadvantage, and it has disadvantage on DEX saves.",
  stunned: "Incapacitated, can't move, speaks only falteringly, auto-fails STR/DEX saves. Attacks against it have advantage.",
  unconscious:
    'Incapacitated, unaware of surroundings, drops what it’s holding and falls prone. Attacks against it have advantage and auto-crit within 5ft.',
  blessed: "A d4 bonus die added to this creature's attack rolls and saving throws, until the spell ends.",
}

export function conditionDescription(name: string): string | null {
  return CONDITION_DESCRIPTIONS[name] ?? null
}

/** "Blinded" or "Blessed (3 rounds left)" - duration_rounds ticks down each
 * round server-side (see engine/conditions.py), so this is always rounds
 * *remaining*, not total duration. Omitted entirely for an indefinite
 * condition (duration_rounds: null - e.g. unconscious, which only ever
 * clears via an explicit effect, not time). */
export function conditionLabel(condition: LiveCondition): string {
  const name = condition.name.charAt(0).toUpperCase() + condition.name.slice(1)
  if (condition.duration_rounds == null) return name
  const unit = condition.duration_rounds === 1 ? 'round' : 'rounds'
  return `${name} (${condition.duration_rounds} ${unit} left)`
}

// SRD exhaustion (data/srd/5e-SRD-Conditions.json) - a leveled 1-6 effect,
// tracked separately from the conditions list above (Character.
// exhaustion_level, not a Condition entry - see engine/state.py's own
// ConditionName docstring for why). Each level's own added effect, per the
// real SRD table - levels stack, so a level 3 creature also has levels 1-2's
// effects, but the tooltip only names what *this* level specifically adds.
const EXHAUSTION_LEVEL_EFFECTS: Record<number, string> = {
  1: 'Disadvantage on ability checks.',
  2: 'Speed halved.',
  3: 'Disadvantage on attack rolls and saving throws.',
  4: 'Hit point maximum halved.',
  5: 'Speed reduced to 0.',
  6: 'Death.',
}

export function exhaustionDescription(level: number): string {
  const thisLevel = EXHAUSTION_LEVEL_EFFECTS[level] ?? ''
  return `${thisLevel} Earlier levels' effects still apply too. A long rest (with food and drink) reduces this by 1.`
}
