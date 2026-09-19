import { useEffect, useState } from 'react'
import { api, type EquipmentSummary, type SpellSummary } from '../api/client'
import type { CombatAttackSummary, CombatSummary, LiveCharacter } from '../ws/sessionClient'
import { equipmentDetail } from '../utils/equipmentDetail'
import { portraitUrl } from '../utils/portraits'
import { nameWithSpellDetail } from '../utils/spellDetail'

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

// Mirrors rules.monk_martial_arts_die_sides exactly (same duplicate-in-TS
// precedent as abilityModifier above, mirroring rules.ability_modifier) -
// 1d4 through level 4, 1d6 from level 5 on (this project's roughly-level-
// 1-5 scope stops there; real SRD keeps scaling further).
function monkMartialArtsDieSides(level: number): number {
  return level >= 5 ? 6 : 4
}

// Same "label value" join convention formatEvent.ts's own debug-mode
// breakdown badges already use (issue #38), just without a natural die
// roll prefix - this is a static, no-roll-yet preview, not a resolved
// roll's own log entry.
function formatBreakdown(breakdown: [string, number][]): string {
  return breakdown.map(([label, value]) => `${label} ${value}`).join(' + ')
}

function formatDamage(attack: CombatAttackSummary): string {
  const dice =
    attack.damage_dice_count > 0 ? `${attack.damage_dice_count}d${attack.damage_dice_sides}` : null
  const needsBonus = attack.damage_bonus !== 0 || dice === null
  const bonus = needsBonus
    ? dice
      ? `${attack.damage_bonus >= 0 ? '+' : '-'} ${Math.abs(attack.damage_bonus)}`
      : `${attack.damage_bonus}`
    : null
  const notation = [dice, bonus].filter((p): p is string => p !== null).join(' ')
  return `${notation} ${attack.damage_type}`
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
 * interface) except cantrips and the known-spells detail pool, fetched once
 * per class and cached (character.known_spells itself - which spells this
 * character actually knows, issue #30 - is on the wire; only the enriched
 * per-spell detail to display alongside it comes from this fetch). The
 * sheet's own action list stays a frontend-derived summary of what's
 * mechanically available right now, not a server-computed list. */
export default function CharacterDetailSheet({
  character,
  combatSummary,
}: {
  character: LiveCharacter
  /** Base + modifiers for this character's current AC/attacks - computed
   * server-side (api/ws/session.py's _combat_summaries) so it can never
   * drift from what an actual roll uses. Undefined until the first
   * state_update arrives, or if the session has no srd set (offline/demo
   * fallback) - both render the sheet exactly as before this feature. */
  combatSummary?: CombatSummary
}) {
  const [cantrips, setCantrips] = useState<SpellSummary[]>([])
  const [knownSpellsPool, setKnownSpellsPool] = useState<SpellSummary[]>([])
  const [equipment, setEquipment] = useState<EquipmentSummary[]>([])

  useEffect(() => {
    if (!character.class_index) {
      setCantrips([])
      setKnownSpellsPool([])
      return
    }
    let cancelled = false
    api
      .getClass(character.class_index)
      .then((detail) => {
        if (cancelled) return
        setCantrips(detail.cantrips)
        // Issue #30 - the class's real level-1 spell pool, cross-referenced
        // below against character.known_spells (which classes actually know)
        // for display detail, same "pool + this character's own picks"
        // pattern equipment/itemDetail already use.
        setKnownSpellsPool(detail.known_spells_pool)
      })
      .catch(() => {
        if (!cancelled) {
          setCantrips([])
          setKnownSpellsPool([])
        }
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
  const equipmentByIndex = new Map(equipment.map((e) => [e.index, e]))
  const itemName = (idx: string) => equipmentByIndex.get(idx)?.name ?? NON_SRD_ITEM_NAMES[idx] ?? idx

  // Real SRD stats for an item (issue #15) - '' for anything with no
  // equipment-map entry (the non-SRD potion, plain gear like clothes-
  // common/pouch). Shared with CharacterCreator's Equipment step (#31).
  const itemDetail = (idx: string): string => {
    const item = equipmentByIndex.get(idx)
    return item ? equipmentDetail(item) : ''
  }
  const nameWithDetail = (idx: string): string => {
    const detail = itemDetail(idx)
    return detail ? `${itemName(idx)} (${detail})` : itemName(idx)
  }

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
  const knownSpellsByIndex = new Map(knownSpellsPool.map((s) => [s.index, s]))
  const knownSpells = character.known_spells
    .map((idx) => knownSpellsByIndex.get(idx))
    .filter((s): s is SpellSummary => s !== undefined)

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
      {combatSummary && combatSummary.ac_breakdown.length > 0 && (
        <p className="companion-meta">
          <strong>AC breakdown:</strong> {formatBreakdown(combatSummary.ac_breakdown)}
        </p>
      )}
      <p className="companion-meta">
        <strong>Equipped:</strong>{' '}
        {[
          ...character.equipped_weapons.map(nameWithDetail),
          ...(character.equipped_armor ? [nameWithDetail(character.equipped_armor)] : []),
          ...(character.equipped_shield ? [nameWithDetail(character.equipped_shield)] : []),
        ].join(', ') || 'nothing (unarmed, unarmored)'}
      </p>
      {combatSummary && combatSummary.attacks.length > 0 && (
        <div>
          <strong>Attack &amp; damage:</strong>
          <ul className="detail-action-list">
            {combatSummary.attacks.map((attack) => (
              <li key={attack.source_name}>
                {attack.source_name}: {attack.attack_bonus >= 0 ? '+' : ''}
                {attack.attack_bonus} to hit [{formatBreakdown(attack.attack_bonus_breakdown)}],{' '}
                {formatDamage(attack)} damage
              </li>
            ))}
          </ul>
        </div>
      )}

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
      {character.class_index === 'monk' && (
        // Issue #36: Martial Arts/Unarmored Defense are correctly applied
        // under the hood from level 1 (see _pc_attack_params's Monk branch
        // and rules.armor_ac's class_index="monk" branch) but had zero
        // visible confirmation anywhere on the sheet - a player had no way
        // to tell the mechanic was even active.
        <p>
          <strong>Martial Arts:</strong> unarmed strikes/monk weapons use a
          1d{monkMartialArtsDieSides(character.level)} die and may use DEX
          instead of STR; once per turn after attacking, a free bonus-action
          unarmed strike is available (no ki cost). Unarmored Defense: AC
          already includes your WIS bonus while unarmored and shieldless.
        </p>
      )}

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

      {knownSpells.length > 0 && (
        <div>
          <strong>Known spells:</strong>
          <ul className="detail-action-list">
            {knownSpells.map((s) => (
              <li key={s.index}>{nameWithSpellDetail(s)}</li>
            ))}
          </ul>
        </div>
      )}

      {inventoryCounts.size > 0 && (
        <div>
          <strong>Inventory:</strong>
          <ul className="detail-action-list">
            {[...inventoryCounts.entries()].map(([idx, count]) => (
              <li key={idx}>
                {nameWithDetail(idx)}
                {count > 1 && ` x${count}`}
              </li>
            ))}
          </ul>
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
          <li>Stabilize a dying ally</li>
          <li>Death Save (while unconscious)</li>
          <li className={character.equip_used_this_turn ? 'detail-action-unavailable' : ''}>
            Equip (switch weapons - doesn't cost your turn)
          </li>
          {character.class_index === 'monk' && (
            <li className={character.bonus_action_used ? 'detail-action-unavailable' : ''}>
              Martial Arts Strike (free bonus-action unarmed strike, no ki)
              {character.bonus_action_used && ' - bonus action already used this turn'}
            </li>
          )}
          {cantrips.length > 0 && (
            <li>
              Cast a Spell - cantrips: {cantrips.map(nameWithSpellDetail).join(', ')}
            </li>
          )}
        </ul>
      </div>
    </div>
  )
}
