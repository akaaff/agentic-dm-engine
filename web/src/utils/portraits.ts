// Single source of truth for the pre-generated portrait library's naming
// convention (src/cli/generate_portraits.py writes files this same way,
// src/api/main.py mounts them at /media/portraits) - every place that wants
// to show a portrait (CombatGrid, PartySetup, CharacterPreviewSheet,
// CharacterDetailSheet) goes through this one function rather than
// duplicating the filename-building logic.

const MEDIA_BASE_URL = 'http://localhost:8000'

// A structural subset shared by both LiveCharacter (sessionClient.ts) and
// Character (api/client.ts) - deliberately not importing either type here,
// so this utility has no dependency direction on either.
export interface PortraitSubject {
  race_index?: string | null
  class_index?: string | null
  gender?: string | null
  hair_color?: string | null
  monster_index?: string | null
}

/**
 * Returns the portrait image URL for a character/monster, or null if the
 * required fields aren't set yet (race/class/gender/hair_color not all
 * chosen during creation, or a pre-Phase-2 character with none of them) -
 * every caller falls back to the existing colored-circle rendering in that
 * case, and the same fallback covers a combination the batch job hasn't
 * generated yet (via the <img>/<image> element's own onError handler,
 * since this function has no way to know what's actually on disk).
 */
export function portraitUrl(subject: PortraitSubject): string | null {
  if (subject.monster_index) {
    return `${MEDIA_BASE_URL}/media/portraits/monsters/${subject.monster_index}.png`
  }
  const { race_index, class_index, gender, hair_color } = subject
  if (race_index && class_index && gender && hair_color) {
    return `${MEDIA_BASE_URL}/media/portraits/pc/${race_index}_${class_index}_${gender}_${hair_color}.png`
  }
  return null
}
