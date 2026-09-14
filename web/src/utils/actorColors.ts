import type { LiveCharacter } from '../ws/sessionClient'

// Single source of truth for "whose color is this" - reused by CombatGrid's
// tokens, CharacterSheet's party-sidebar cards, and the narration feed's
// event badges, so the same character reads as the same color everywhere.
// Previously CombatGrid and CharacterSheet each had their own independent
// ally(blue)/enemy(red) ternary - this consolidates that AND adds a
// distinct color per party member (not just one shared "ally blue"), per
// the user's explicit ask after playtesting.

export const ENEMY_COLOR = '#ff6b6b'

const PC_PALETTE = ['#4dabff', '#b388ff', '#4ddbc4', '#ffd166', '#ff8fb3']
/** Blue (the original ally color, kept as the first slot so a solo PC's
 * color doesn't change), violet, teal, gold, pink - 5 slots covers a PC
 * plus up to 4 companions (this project's own PartySetup cap) without
 * repeating. Picked to stay legible against the existing dark theme
 * (index.css's #14121a/#1c1924 backgrounds). */

/**
 * Every PC/companion's color, assigned once from their position among
 * `is_pc` characters in `turnOrder` (stable for the whole encounter - turn
 * order doesn't change mid-fight) and cycling through PC_PALETTE. Enemies
 * all share ENEMY_COLOR - the "each goblin needs its own color" case wasn't
 * asked for and would make the log noisier, not clearer.
 */
export function buildActorColorMap(
  turnOrder: string[],
  characters: Record<string, LiveCharacter>,
): Record<string, string> {
  const colors: Record<string, string> = {}
  let pcIndex = 0
  for (const id of turnOrder) {
    const character = characters[id]
    if (!character) continue
    colors[id] = character.is_pc ? PC_PALETTE[pcIndex++ % PC_PALETTE.length] : ENEMY_COLOR
  }
  return colors
}
