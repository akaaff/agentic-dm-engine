import { useEffect, useState } from 'react'
import { api, type EquipmentSummary, type SpellSummary } from '../api/client'
import type { LiveCharacter } from '../ws/sessionClient'
import { portraitUrl } from '../utils/portraits'

const ABILITIES: (keyof LiveCharacter['stats'])[] = ['STR', 'DEX', 'CON', 'INT', 'WIS', 'CHA']

// Items granted at creation (character_creation.DEFAULT_STARTING_CONSUMABLES)
// that aren't real SRD equipment entries - see character_creation.py's
// EXTRA_EQUIPMENT_INDICES docstring for why. api.listEquipment() can never
// know their display name, so itemName() below falls back to this map
// before falling back to the raw index string.
const NON_SRD_ITEM_NAMES: Record<string, string> = {
  'potion-of-healing': 'Potion of Healing',
}

function abilityModifier(score: number): number {
  return Math.floor((score - 10) / 2)
}

function formatModifier(mod: number): string {
  return mod >= 0 ? `+${mod}` : `${mod}`
}

function resourceLabel(key: string): string {
  // "second_wind" -> "Second Wind"
  return key
    .split('_')
    .map((w) => w[0].toUpperCase() + w.slice(1))
    .join(' ')
}

/** The player's own full character sheet - a detailed, always-visible
 * counterpart to the compact per-combatant cards in the party sidebar
 * (CharacterSheet.tsx). Everything here reads directly from the
 * LiveCharacter already on the wire (see sessionClient.ts's widened
 * interface) except the class's known cantrips, fetched once per class
 * and cached - there's no per-character "known spells" concept in the
 * engine, so the sheet's own action list is a frontend-derived summary of
 * what's mechanically available right now, not a server-computed list. */
export default function CharacterDetailSheet({ character }: { character: LiveCharacter }) {
  const [cantrips, setCantrips] = useState<SpellSummary[]>([])
  const [equipment, setEquipment] = useState<EquipmentSummary[]>([])

  useEffect(() => {
    if (!character.class_index) {
      setCantrips([])
      return
    }
    let cancelled = false
    api
      .getClass(character.class_index)
      .then((detail) => {
        if (!cancelled) setCantrips(detail.cantrips)
      })
      .catch(() => {
        if (!cancelled) setCantrips([])
      })
    return () => {
      cancelled = true
    }
  }, [character.class_index])

  useEffect(() => {
    let cancelled = false
    api
      .listEquipment()
      .then((items) => {
        if (!cancelled) setEquipment(items)
      })
      .catch(() => {
        if (!cancelled) setEquipment([])
      })
    return () => {
      cancelled = true
    }
  }, [])

  const hpPct = character.max_hp > 0 ? Math.max(0, (character.hp / character.max_hp) * 100) : 0
  const portrait = portraitUrl(character)
  const resourceEntries = Object.entries(character.class_resources)
  const equipmentNames = new Map(equipment.map((e) => [e.index, e.name]))
  const itemName = (idx: string) => equipmentNames.get(idx) ?? NON_SRD_ITEM_NAMES[idx] ?? idx

  // Grouped/counted, not a naive listing - the engine's inventory is a flat
  // list of item *instances* (a quiver of 20 arrows is 20 separate "arrow"
  // entries, not one entry with a quantity - see CLAUDE.md's Day-18 note),
  // never fixed at the display layer until now.
  const inventoryCounts = new Map<string, number>()
  for (const idx of character.inventory) {
    inventoryCounts.set(idx, (inventoryCounts.get(idx) ?? 0) + 1)
  }
  const spellSlotEntries = Object.entries(character.spell_slots).sort(
    ([a], [b]) => Number(a) - Number(b),
  )

  return (
    <div className="character-detail-sheet sheet">
      <div className="preview-portrait detail-portrait">
        {portrait ? (
          <img
            src={portrait}
            alt={`${character.name} portrait`}
            onError={(e) => {
              e.currentTarget.style.display = 'none'
            }}
          />
        ) : (
          <div className="preview-portrait-placeholder">No portrait</div>
        )}
      </div>

      <h2>{character.name}</h2>
      <p className="companion-meta">
        {character.race} {character.class_} - Level {character.level}
      </p>

      <div className="hp-bar-track">
        <div className="hp-bar-fill" style={{ width: `${hpPct}%` }} />
      </div>
      <div className="hp-label">
        HP {character.hp}/{character.max_hp} - AC {character.ac}
        {character.is_dead && ' - dead'}
        {!character.is_dead &&
          character.hp <= 0 &&
          (character.is_stable ? ' - stable' : ' - unconscious')}
      </div>
      <p className="companion-meta">
        <strong>Equipped:</strong>{' '}
        {character.equipped_weapons.length > 0
          ? character.equipped_weapons.map(itemName).join(', ')
          : 'nothing (unarmed)'}
      </p>

      <table className="detail-stats-table">
        <tbody>
          {ABILITIES.map((a) => (
            <tr key={a}>
              <td>{a}</td>
              <td>{character.stats[a]}</td>
              <td>{formatModifier(abilityModifier(character.stats[a]))}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {character.exhaustion_level > 0 && (
        <p className="wizard-error">Exhaustion level {character.exhaustion_level}</p>
      )}
      {character.conditions.length > 0 && (
        <div className="conditions">{character.conditions.map((c) => c.name).join(', ')}</div>
      )}
      {character.concentrating_on && (
        <p>
          <strong>Concentrating on:</strong> {character.concentrating_on}
        </p>
      )}
      {character.fighting_style && (
        <p>
          <strong>Fighting Style:</strong>{' '}
          {character.fighting_style[0].toUpperCase() + character.fighting_style.slice(1)}
        </p>
      )}
      {character.is_raging && <p className="race-bonus-badge">Raging</p>}

      {resourceEntries.length > 0 && (
        <div>
          <strong>Class resources:</strong>
          <ul className="detail-action-list">
            {resourceEntries.map(([key, remaining]) => (
              <li key={key} className={remaining <= 0 ? 'detail-action-unavailable' : ''}>
                {resourceLabel(key)}: {remaining} remaining
                {key === 'rage' && character.is_raging && ' (already raging)'}
                {character.bonus_action_used && ' - bonus action already used this turn'}
              </li>
            ))}
          </ul>
        </div>
      )}

      {spellSlotEntries.length > 0 && (
        <div>
          <strong>Spell slots:</strong>
          <ul className="detail-action-list">
            {spellSlotEntries.map(([level, remaining]) => (
              <li key={level} className={remaining <= 0 ? 'detail-action-unavailable' : ''}>
                Level {level}: {remaining} remaining
              </li>
            ))}
          </ul>
          <p className="companion-meta">Cantrips are unlimited - no slot cost.</p>
        </div>
      )}

      {inventoryCounts.size > 0 && (
        <div>
          <strong>Inventory:</strong>
          <p className="companion-meta">
            {[...inventoryCounts.entries()]
              .map(([idx, count]) => (count > 1 ? `${itemName(idx)} x${count}` : itemName(idx)))
              .join(', ')}
          </p>
        </div>
      )}

      <div>
        <strong>Available actions:</strong>
        <ul className="detail-action-list">
          <li>Attack</li>
          <li>Move / Dash</li>
          <li>Dodge</li>
          <li>Disengage{character.disengaged_this_turn && ' (already used this turn)'}</li>
          <li>Help</li>
          <li>Grapple / Shove</li>
          <li>Skill Check</li>
          <li>Use Item</li>
          <li className={character.equip_used_this_turn ? 'detail-action-unavailable' : ''}>
            Equip (switch weapons - doesn't cost your turn)
          </li>
          {cantrips.length > 0 && (
            <li>
              Cast a Spell - cantrips: {cantrips.map((c) => c.name).join(', ')}
            </li>
          )}
        </ul>
      </div>
    </div>
  )
}
