// Thin typed fetch wrapper around the FastAPI backend (src/api/routes/characters.py).
// No generated OpenAPI client - the surface is small enough that hand-written
// types are less overhead than adding a codegen step for Day 17's scope.

const API_BASE_URL = 'http://localhost:8000'

export class ApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new ApiError(response.status, body.detail ?? response.statusText)
  }
  return response.json() as Promise<T>
}

export interface RaceTrait {
  index: string
  name: string
  desc: string
}

export interface RaceSummary {
  index: string
  name: string
  speed: number
  ability_bonuses: Record<string, number>
  traits: RaceTrait[]
}

export interface ClassSummary {
  index: string
  name: string
  hit_die: number
}

export interface SpellSummary {
  index: string
  name: string
  desc: string
  // Real mechanical detail (issue #30) - level/casting_time/range/
  // components/material always present, the rest set only for whichever of
  // damage/heal/DC the spell actually has.
  level: number
  casting_time: string
  range: string
  components: string[]
  material: string | null
  damage_dice: string | null
  damage_type: string | null
  heal_dice: string | null
  dc_type: string | null
  dc_success: string | null
}

export interface StartingEquipmentItem {
  index: string
  name: string
  quantity: number
}

export interface ClassDetail extends ClassSummary {
  skill_choose: number
  skill_options: string[]
  equipment_options: string[]
  cantrips: SpellSummary[]
  // The class's fixed starting kit (issue #32) - separate from
  // equipment_options, which is just the proficiency-gated *optional* pool.
  starting_equipment: StartingEquipmentItem[]
  // Issue #30: >0 only for a "Spells Known" caster (Bard/Sorcerer) - the
  // wizard's spell-choice picker offers exactly known_spells_pool, capped
  // at spells_known.
  spells_known: number
  known_spells_pool: SpellSummary[]
}

export interface SkillSummary {
  index: string
  name: string
  ability: string
  desc: string
}

export interface BackgroundSummary {
  index: string
  name: string
  starting_equipment: StartingEquipmentItem[]
}

export interface EquipmentSummary {
  index: string
  name: string
  category: 'weapon' | 'armor'
  // Weapon-only (issue #15) - real SRD damage/property data, previously
  // computed server-side for actual mechanics but never exposed here.
  damage_dice: string | null
  damage_type: string | null
  properties: string[]
  // Armor-only (issue #15).
  ac_base: number | null
  ac_dex_bonus: boolean
  ac_max_bonus: number | null
  stealth_disadvantage: boolean
}

export type AbilityScore = 'STR' | 'DEX' | 'CON' | 'INT' | 'WIS' | 'CHA'

export interface CreateCharacterRequest {
  character_id: string
  name: string
  race_index: string
  class_index: string
  background_index: string
  base_ability_scores: Record<AbilityScore, number>
  chosen_skills: string[]
  chosen_equipment: string[]
  gender?: string
  fighting_style?: string
  chosen_racial_skills?: string[]
  chosen_spells?: string[]
}

export interface Character {
  id: string
  name: string
  is_pc: boolean
  race: string
  class_: string
  background: string
  hp: number
  max_hp: number
  ac: number
  speed: number
  proficiency_bonus: number
  stats: Record<AbilityScore, number>
  inventory: string[]
  skill_proficiencies: string[]
  saving_throw_proficiencies: string[]
  known_spells: string[]
  is_companion: boolean
  persona: string | null
  monster_index: string | null
  exhaustion_level: number
  class_index: string | null
  spell_slots: Record<string, number>
  hit_die_sides: number
  hit_dice_remaining: number
  class_resources: Record<string, number>
  fighting_style: string | null
  is_raging: boolean
  level: number
  concentrating_on: string | null
  is_dead: boolean
  is_stable: boolean
  // New for the character-sheet + portrait feature (Phase 2).
  race_index: string | null
  gender: string | null
  // New for equipped-weapon tracking (Phase C).
  equipped_weapons: string[]
  // New for armor swapping (issue #13).
  equipped_armor: string | null
  equipped_shield: string | null
}

export type CampaignSize = 'one_shot' | 'short_arc' | 'full'

export interface CampaignSummary {
  id: string
  title: string
  size: CampaignSize
  description: string
}

export interface StartSessionRequest {
  campaign_id: string
  character_id: string
  companion_ids: string[]
}

export interface StartSessionResponse {
  session_id: string
}

export const api = {
  listRaces: () => request<RaceSummary[]>('/characters/races'),
  listClasses: () => request<ClassSummary[]>('/characters/classes'),
  getClass: (classIndex: string) => request<ClassDetail>(`/characters/classes/${classIndex}`),
  listSkills: () => request<SkillSummary[]>('/characters/skills'),
  listBackgrounds: () => request<BackgroundSummary[]>('/characters/backgrounds'),
  listEquipment: () => request<EquipmentSummary[]>('/characters/equipment'),
  createCharacter: (body: CreateCharacterRequest) =>
    request<Character>('/characters', { method: 'POST', body: JSON.stringify(body) }),
  getCharacter: (characterId: string) => request<Character>(`/characters/${characterId}`),
  listCompanions: () => request<Character[]>('/companions'),
  listCampaigns: () => request<CampaignSummary[]>('/campaigns'),
  startSession: (body: StartSessionRequest) =>
    request<StartSessionResponse>('/sessions', { method: 'POST', body: JSON.stringify(body) }),
}
