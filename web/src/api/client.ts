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

export interface RaceSummary {
  index: string
  name: string
  speed: number
  ability_bonuses: Record<string, number>
}

export interface ClassSummary {
  index: string
  name: string
  hit_die: number
}

export interface ClassDetail extends ClassSummary {
  skill_choose: number
  skill_options: string[]
}

export interface BackgroundSummary {
  index: string
  name: string
}

export interface EquipmentSummary {
  index: string
  name: string
  category: 'weapon' | 'armor'
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
}

export interface Character {
  id: string
  name: string
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
}

export const api = {
  listRaces: () => request<RaceSummary[]>('/characters/races'),
  listClasses: () => request<ClassSummary[]>('/characters/classes'),
  getClass: (classIndex: string) => request<ClassDetail>(`/characters/classes/${classIndex}`),
  listBackgrounds: () => request<BackgroundSummary[]>('/characters/backgrounds'),
  listEquipment: () => request<EquipmentSummary[]>('/characters/equipment'),
  createCharacter: (body: CreateCharacterRequest) =>
    request<Character>('/characters', { method: 'POST', body: JSON.stringify(body) }),
  getCharacter: (characterId: string) => request<Character>(`/characters/${characterId}`),
}
