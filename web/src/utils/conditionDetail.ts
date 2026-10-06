import type { LiveCondition } from '../ws/sessionClient'

// Condensed from the real SRD condition text (data/srd/5e-SRD-Conditions.json)
// - this is a closed, SRD-fixed vocabulary that never changes with game
// state, so a small static map here (same precedent as CharacterDetailSheet.
// tsx's own RESOURCE_HINTS) is simpler than a new backend endpoint for
// something this static. The spell-effect entries after "unconscious" aren't
// real SRD conditions (they're a spell's own ongoing effect, reusing the same
// condition machinery - see engine/state.py's ConditionName) - hand-written
// to describe what this engine actually implements for each, including its
// own documented simplifications, rather than the full spell text.
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
  baned:
    "A d4 is subtracted from every attack roll and saving throw it makes (death saves excepted), until the spell ends.",
  blurred: 'Attackers have disadvantage on attack rolls against it, until the spell ends.',
  longstrider: 'Speed increased by 10 feet.',
  death_warded:
    'The first time it would drop to 0 hit points, it drops to 1 instead and the spell ends.',
  barkskin: "Its AC can't be lower than 16, whatever armor it wears.",
  stoneskinned: 'Resistance to bludgeoning, piercing, and slashing damage - halves it.',
  poison_protected: 'Resistance to poison damage - halves it.',
  divine_favor: 'Its weapon attacks deal an extra 1d4 radiant damage on a hit.',
  hunters_marked:
    "A marked quarry: the one who marked it deals an extra 1d6 damage whenever they hit it with a weapon attack.",
  warded:
    'Any creature that attacks it must first succeed on a Wisdom saving throw, or lose that attack. The ward ends if it attacks.',
  protected_from_evil:
    'Aberrations, celestials, elementals, fey, fiends, and undead have disadvantage on attack rolls against it.',
  outlined: 'Outlined in shimmering light: attack rolls against it have advantage.',
}

// Names that aren't a plain capitalized form of the condition id - the spell
// effects, whose ids are snake_case internal tags ("death_warded") rather than
// something to show a player.
const CONDITION_DISPLAY_NAMES: Record<string, string> = {
  baned: 'Bane',
  blurred: 'Blur',
  death_warded: 'Death Ward',
  stoneskinned: 'Stoneskin',
  energy_resistant: 'Energy resistance',
  poison_protected: 'Poison protection',
  divine_favor: 'Divine Favor',
  hunters_marked: "Hunter's Mark",
  warded: 'Sanctuary',
  mirror_image: 'Mirror Image',
  protected_from_evil: 'Protection from Evil and Good',
  outlined: 'Outlined (Faerie Fire)',
}

// A spell's ongoing effect, as opposed to one of the SRD's 15 real
// conditions - reads as "affected by X" rather than "now X" in the log.
const SPELL_EFFECT_CONDITIONS = new Set([
  'baned',
  'blurred',
  'longstrider',
  'death_warded',
  'barkskin',
  'stoneskinned',
  'energy_resistant',
  'poison_protected',
  'divine_favor',
  'hunters_marked',
  'warded',
  'mirror_image',
  'protected_from_evil',
  'outlined',
])

export function conditionDisplayName(name: string): string {
  return CONDITION_DISPLAY_NAMES[name] ?? name.charAt(0).toUpperCase() + name.slice(1)
}

export function isSpellEffectCondition(name: string): boolean {
  return SPELL_EFFECT_CONDITIONS.has(name)
}

/** Hover text for a condition. `detail` is the condition's own per-spell
 * payload (see LiveCondition.detail) - only Protection from Energy and
 * Mirror Image carry one, and only the former changes what the text says. */
export function conditionDescription(name: string, detail?: string | null): string | null {
  if (name === 'energy_resistant') {
    return `Resistance to ${detail ? `${detail} ` : ''}damage - halves it, until the spell ends.`
  }
  if (name === 'mirror_image') {
    const n = detail ?? '?'
    return `${n} illusory duplicate(s) may absorb attacks aimed at it - fewer duplicates means they absorb less often. Each one an attack hits is destroyed.`
  }
  return CONDITION_DESCRIPTIONS[name] ?? null
}

// A round is 6 seconds, so a 1-minute spell is 10 rounds, 10 minutes 100, an
// hour 600. A long spell's exact round count ("599 rounds left") is noise -
// show it the way a player thinks of the duration instead. Only combat
// rounds tick it down, so it's approximate by nature anyway.
function durationText(rounds: number): string {
  if (rounds >= 600) return `${Math.round(rounds / 600)} hr`
  if (rounds >= 20) return `${Math.round(rounds / 10)} min`
  return `${rounds} ${rounds === 1 ? 'round' : 'rounds'}`
}

/** "Blinded" or "Blessed (3 rounds left)" - duration_rounds ticks down each
 * round server-side (see engine/conditions.py), so this is always time
 * *remaining*, not total duration. Omitted entirely for an indefinite
 * condition (duration_rounds: null - e.g. unconscious, which only ever
 * clears via an explicit effect, not time). A condition's own `detail`
 * (Protection from Energy's damage type, Mirror Image's image count) follows
 * the name. */
export function conditionLabel(condition: LiveCondition): string {
  const base = conditionDisplayName(condition.name)
  const detail = condition.detail
    ? condition.name === 'mirror_image'
      ? `: ${condition.detail} ${condition.detail === '1' ? 'image' : 'images'}`
      : `: ${condition.detail}`
    : ''
  if (condition.duration_rounds == null) return `${base}${detail}`
  return `${base}${detail} (${durationText(condition.duration_rounds)} left)`
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
