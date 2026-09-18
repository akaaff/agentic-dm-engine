import { useEffect, useMemo, useState } from 'react'
import {
  api,
  ApiError,
  type AbilityScore,
  type BackgroundSummary,
  type Character,
  type ClassDetail,
  type ClassSummary,
  type EquipmentSummary,
  type RaceSummary,
  type SkillSummary,
} from '../api/client'
import CharacterPreviewSheet from '../components/CharacterPreviewSheet'
import { equipmentDetail } from '../utils/equipmentDetail'
import { nameWithSpellDetail } from '../utils/spellDetail'

// Mirrors character_creation.VALID_GENDERS - portrait-selection only, no
// mechanical weight (see that module's docstring).
const GENDERS = ['male', 'female'] as const

// Mirrors character_creation.VALID_FIGHTING_STYLES/FIGHTING_STYLE_CLASSES -
// not exposed via any endpoint (a small enough fixed set that hardcoding it
// here matches this wizard's existing CLASS_HINTS/ABILITY_HINTS precedent).
const FIGHTING_STYLE_CLASSES = new Set(['fighter', 'ranger', 'paladin'])
const FIGHTING_STYLES: { value: string; label: string; hint: string }[] = [
  { value: 'archery', label: 'Archery', hint: '+2 to attack rolls with ranged weapons.' },
  { value: 'defense', label: 'Defense', hint: '+1 AC while wearing armor.' },
  {
    value: 'dueling',
    label: 'Dueling',
    hint: '+2 damage with a one-handed melee weapon and no other weapon equipped.',
  },
]

const ABILITIES: AbilityScore[] = ['STR', 'DEX', 'CON', 'INT', 'WIS', 'CHA']
const STANDARD_ARRAY = [15, 14, 13, 12, 10, 8]

// Short, standard 5e explanations, shown as hover/focus tooltips rather than
// permanently-visible text - a new player doesn't have to already know what
// each ability governs, but it doesn't have to take up space for everyone
// who does.
const ABILITY_HINTS: Record<AbilityScore, string> = {
  STR: 'Melee attack/damage rolls, carrying capacity, Athletics.',
  DEX: 'Armor Class, ranged & finesse weapons, Stealth/Acrobatics, initiative.',
  CON: 'Hit points and concentration saves - rarely a dump stat.',
  INT: 'Arcane spellcasting (Wizard), Investigation/Arcana checks.',
  WIS: 'Divine/Nature spellcasting (Cleric/Druid/Ranger), Perception/Insight.',
  CHA: 'Charisma-based spellcasting (Bard/Sorcerer/Warlock/Paladin), Persuasion/Deception.',
}

// Short, original one-line role summaries - not SRD/PHB flavor text (this
// project's vendored SRD data has no class description field), same spirit
// as the hand-authored companion personas already elsewhere in the project.
const CLASS_HINTS: Record<string, string> = {
  barbarian: 'A fierce warrior who channels primal rage into reckless, unstoppable fury.',
  bard: 'A charismatic performer whose music and magic inspire allies and confound foes.',
  cleric: 'A holy warrior and healer, channeling divine power to smite foes and mend allies.',
  druid: 'A guardian of the natural world, wielding elemental and shapeshifting magic.',
  fighter: 'A versatile master of martial combat, skilled with a wide array of weapons and armor.',
  monk: 'A disciplined martial artist channeling inner energy into extraordinary unarmed feats.',
  paladin: 'A holy knight bound by a sacred oath, blending martial prowess with divine magic.',
  ranger: 'A skilled hunter and tracker at home in the wild, blending martial skill with nature magic.',
  rogue: 'A cunning, stealthy expert in precision strikes, traps, and subterfuge.',
  sorcerer: 'A spellcaster wielding instinctive magic drawn from an innate magical bloodline.',
  warlock: 'A spellcaster who draws power from a bargain with an otherworldly patron.',
  wizard: 'A scholarly spellcaster who masters arcane magic through rigorous study.',
}

// Now that Class & Skills comes before Ability Scores in the wizard, "fill
// recommended" can key off the actually-chosen class instead of asking the
// player to separately describe their build. CON stays high everywhere -
// rarely a dump stat for any class.
const CLASS_ABILITY_PRIORITY: Record<string, AbilityScore[]> = {
  barbarian: ['STR', 'CON', 'DEX', 'WIS', 'CHA', 'INT'],
  fighter: ['STR', 'CON', 'DEX', 'WIS', 'CHA', 'INT'],
  paladin: ['STR', 'CHA', 'CON', 'WIS', 'DEX', 'INT'],
  monk: ['DEX', 'WIS', 'CON', 'STR', 'CHA', 'INT'],
  ranger: ['DEX', 'WIS', 'CON', 'STR', 'CHA', 'INT'],
  rogue: ['DEX', 'CON', 'INT', 'WIS', 'CHA', 'STR'],
  bard: ['CHA', 'DEX', 'CON', 'WIS', 'INT', 'STR'],
  sorcerer: ['CHA', 'CON', 'DEX', 'WIS', 'STR', 'INT'],
  warlock: ['CHA', 'CON', 'DEX', 'WIS', 'STR', 'INT'],
  cleric: ['WIS', 'CON', 'STR', 'DEX', 'CHA', 'INT'],
  druid: ['WIS', 'CON', 'DEX', 'CHA', 'STR', 'INT'],
  wizard: ['INT', 'CON', 'DEX', 'WIS', 'CHA', 'STR'],
}
const DEFAULT_ABILITY_PRIORITY: AbilityScore[] = ['STR', 'DEX', 'CON', 'WIS', 'CHA', 'INT']

function slugify(name: string): string {
  return (
    name
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/(^-|-$)/g, '') || 'hero'
  )
}

function skillLabel(skillIndex: string): string {
  // "skill-sleight-of-hand" -> "Sleight Of Hand"
  return skillIndex
    .replace(/^skill-/, '')
    .split('-')
    .map((w) => w[0].toUpperCase() + w.slice(1))
    .join(' ')
}

/** Hover/focus tooltip - keyboard-accessible (tabIndex + :focus-within),
 * dependency-free. Used for ability/skill/class explanations so the wizard
 * stays compact for players who already know the rules. */
function InfoTip({ text }: { text: string }) {
  return (
    <span className="info-tip" tabIndex={0}>
      <span aria-hidden="true" className="info-tip-icon">
        ⓘ
      </span>
      <span role="tooltip" className="info-tip-bubble">
        {text}
      </span>
    </span>
  )
}

export default function CharacterCreator({ onCreated }: { onCreated: (character: Character) => void }) {
  const [step, setStep] = useState(0)

  const [races, setRaces] = useState<RaceSummary[]>([])
  const [classes, setClasses] = useState<ClassSummary[]>([])
  const [skills, setSkills] = useState<SkillSummary[]>([])
  const [backgrounds, setBackgrounds] = useState<BackgroundSummary[]>([])
  const [equipment, setEquipment] = useState<EquipmentSummary[]>([])
  const [loadError, setLoadError] = useState<string | null>(null)

  const [name, setName] = useState('')
  const [raceIndex, setRaceIndex] = useState('')
  const [gender, setGender] = useState('')
  const [classIndex, setClassIndex] = useState('')
  const [classDetail, setClassDetail] = useState<ClassDetail | null>(null)
  const [fightingStyle, setFightingStyle] = useState('')
  const [chosenSkills, setChosenSkills] = useState<string[]>([])
  // Half-Elf's Skill Versatility (issue #23): 2 skills of the player's
  // choice, any skill - unlike chosenSkills, not gated by the class's own
  // skill_options.
  const [chosenRacialSkills, setChosenRacialSkills] = useState<string[]>([])
  // "Spells Known" caster's level-1 spell choice (issue #30) - Bard/
  // Sorcerer only, gated by classDetail.spells_known the same way
  // chosenSkills is gated by skill_choose.
  const [chosenSpells, setChosenSpells] = useState<string[]>([])
  const [assignments, setAssignments] = useState<Record<AbilityScore, number | ''>>({
    STR: '',
    DEX: '',
    CON: '',
    INT: '',
    WIS: '',
    CHA: '',
  })
  const [backgroundIndex, setBackgroundIndex] = useState('')
  const [chosenEquipment, setChosenEquipment] = useState<string[]>([])

  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [created, setCreated] = useState<Character | null>(null)

  useEffect(() => {
    Promise.all([
      api.listRaces(),
      api.listClasses(),
      api.listSkills(),
      api.listBackgrounds(),
      api.listEquipment(),
    ])
      .then(([r, c, sk, b, e]) => {
        setRaces(r)
        setClasses(c)
        setSkills(sk)
        setBackgrounds(b)
        setEquipment(e)
        if (b.length === 1) setBackgroundIndex(b[0].index)
      })
      .catch((err: unknown) => setLoadError(err instanceof Error ? err.message : String(err)))
  }, [])

  useEffect(() => {
    if (!classIndex) {
      setClassDetail(null)
      return
    }
    setChosenSkills([])
    setChosenEquipment([])
    setFightingStyle('')
    setChosenSpells([])
    api
      .getClass(classIndex)
      .then(setClassDetail)
      .catch((err: unknown) => setLoadError(err instanceof Error ? err.message : String(err)))
  }, [classIndex])

  const skillDesc = useMemo(
    () => new Map(skills.map((s) => [s.index, s.desc])),
    [skills],
  )
  // Server-enforced too (create_character rejects a choice outside this
  // pool) - filtering the picker down to it here just avoids showing gear
  // the class would then get rejected for at submission.
  const proficientEquipment = useMemo(
    () =>
      classDetail
        ? equipment.filter((e) => classDetail.equipment_options.includes(e.index))
        : [],
    [equipment, classDetail],
  )
  const selectedRace = races.find((r) => r.index === raceIndex)
  const usedValues = Object.values(assignments).filter((v) => v !== '')
  const remainingValues = useMemo(() => {
    const remaining = [...STANDARD_ARRAY]
    for (const v of usedValues) remaining.splice(remaining.indexOf(v as number), 1)
    return remaining
  }, [usedValues])
  const allAbilitiesAssigned = usedValues.length === ABILITIES.length

  function raceBonus(ability: AbilityScore): number {
    return selectedRace?.ability_bonuses[ability.toLowerCase()] ?? 0
  }

  function finalScore(ability: AbilityScore): number | null {
    const base = assignments[ability]
    if (base === '') return null
    return base + raceBonus(ability)
  }

  function fillRecommended() {
    const priority = CLASS_ABILITY_PRIORITY[classIndex] ?? DEFAULT_ABILITY_PRIORITY
    const next = {} as Record<AbilityScore, number | ''>
    priority.forEach((ability, i) => {
      next[ability] = STANDARD_ARRAY[i]
    })
    setAssignments(next)
  }

  function toggleSkill(skill: string) {
    setChosenSkills((prev) => {
      if (prev.includes(skill)) return prev.filter((s) => s !== skill)
      if (classDetail && prev.length >= classDetail.skill_choose) return prev
      return [...prev, skill]
    })
  }

  function toggleSpell(spell: string) {
    setChosenSpells((prev) => {
      if (prev.includes(spell)) return prev.filter((s) => s !== spell)
      if (classDetail && prev.length >= classDetail.spells_known) return prev
      return [...prev, spell]
    })
  }

  function toggleRacialSkill(skill: string) {
    setChosenRacialSkills((prev) => {
      if (prev.includes(skill)) return prev.filter((s) => s !== skill)
      if (prev.length >= 2) return prev
      return [...prev, skill]
    })
  }

  function toggleEquipment(index: string) {
    setChosenEquipment((prev) =>
      prev.includes(index) ? prev.filter((i) => i !== index) : [...prev, index],
    )
  }

  const canProceedFromBasics =
    name.trim().length > 0 &&
    raceIndex !== '' &&
    (raceIndex !== 'half-elf' || chosenRacialSkills.length === 2)
  const canProceedFromClass =
    classIndex !== '' &&
    classDetail !== null &&
    chosenSkills.length === classDetail.skill_choose &&
    (classDetail.spells_known === 0 || chosenSpells.length === classDetail.spells_known)
  const canProceedFromAbilities = allAbilitiesAssigned
  const canSubmit = backgroundIndex !== ''

  async function handleSubmit() {
    setSubmitting(true)
    setSubmitError(null)
    try {
      const base_ability_scores = Object.fromEntries(
        ABILITIES.map((a) => [a, assignments[a] as number]),
      ) as Record<AbilityScore, number>

      const character = await api.createCharacter({
        character_id: slugify(name),
        name,
        race_index: raceIndex,
        class_index: classIndex,
        background_index: backgroundIndex,
        base_ability_scores,
        chosen_skills: chosenSkills,
        chosen_equipment: chosenEquipment,
        gender: gender || undefined,
        fighting_style: fightingStyle || undefined,
        chosen_racial_skills: raceIndex === 'half-elf' ? chosenRacialSkills : undefined,
        chosen_spells: classDetail?.spells_known ? chosenSpells : undefined,
      })
      setCreated(character)
      setStep(4)
    } catch (err) {
      setSubmitError(err instanceof ApiError ? err.message : 'Failed to reach the server')
    } finally {
      setSubmitting(false)
    }
  }

  if (loadError) {
    return (
      <div className="wizard-error">
        Could not load character-creation data from the API: {loadError}
        <br />
        Is the backend running at http://localhost:8000?
      </div>
    )
  }

  if (created) {
    return (
      <div className="wizard">
        <h1>{created.name} is ready!</h1>
        <div className="sheet">
          <p>
            {created.race} {created.class_} - {created.background}
          </p>
          <p>
            HP {created.hp}/{created.max_hp} - AC {created.ac} - Speed {created.speed} ft
          </p>
          <table>
            <tbody>
              {ABILITIES.map((a) => (
                <tr key={a}>
                  <td>{a}</td>
                  <td>{created.stats[a]}</td>
                  <td>
                    {raceBonus(a) > 0 && (
                      <span className="race-bonus-badge" title={`${created.race} racial bonus`}>
                        +{raceBonus(a)} {created.race}
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {selectedRace && selectedRace.traits.length > 0 && (
            <>
              <p>Racial traits:</p>
              <div className="race-trait-row">
                {selectedRace.traits.map((trait) => (
                  <span key={trait.index} className="trait-chip">
                    {trait.name}
                    <InfoTip text={trait.desc} />
                  </span>
                ))}
              </div>
            </>
          )}
          <p>Skills: {created.skill_proficiencies.map(skillLabel).join(', ') || 'none'}</p>
          <p>Inventory: {created.inventory.join(', ') || 'none'}</p>
        </div>
        <div className="wizard-nav">
          <button
            type="button"
            onClick={() => {
              setCreated(null)
              setStep(0)
              setName('')
              setRaceIndex('')
              setGender('')
              setAssignments({ STR: '', DEX: '', CON: '', INT: '', WIS: '', CHA: '' })
              setClassIndex('')
              setFightingStyle('')
              setChosenSkills([])
              setChosenRacialSkills([])
              setChosenEquipment([])
            }}
          >
            Create another character
          </button>
          <button type="button" onClick={() => onCreated(created)}>
            Continue to Party Setup
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="character-creator-layout">
      <div className="wizard">
        <h1>Create a Character</h1>
        <ol className="steps">
          {['Basics', 'Class & Skills', 'Ability Scores', 'Equipment'].map(
            (label, i) => (
              <li key={label} className={i === step ? 'active' : i < step ? 'done' : ''}>
                {label}
              </li>
            ),
          )}
        </ol>

        {step === 0 && (
          <section>
            <label>
              Name
              <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Thorin" />
            </label>
            <label>
              Race
              <select
                value={raceIndex}
                onChange={(e) => {
                  setRaceIndex(e.target.value)
                  setChosenRacialSkills([])
                }}
              >
                <option value="">Choose a race...</option>
                {races.map((r) => (
                  <option key={r.index} value={r.index}>
                    {r.name} (speed {r.speed} ft)
                  </option>
                ))}
              </select>
            </label>
            <label>
              Gender
              <select value={gender} onChange={(e) => setGender(e.target.value)}>
                <option value="">Choose...</option>
                {GENDERS.map((g) => (
                  <option key={g} value={g}>
                    {g[0].toUpperCase() + g.slice(1)}
                  </option>
                ))}
              </select>
            </label>
            <p className="companion-meta">
              Portrait-selection only, no mechanical effect - used to pick your character's
              generated portrait once race, class and gender are all chosen.
            </p>
            {selectedRace && Object.keys(selectedRace.ability_bonuses).length > 0 && (
              <div className="race-bonus-row">
                {ABILITIES.filter((a) => raceBonus(a) > 0).map((a) => (
                  <span key={a} className="race-bonus-badge">
                    +{raceBonus(a)} {a}
                  </span>
                ))}
              </div>
            )}
            {selectedRace && selectedRace.traits.length > 0 && (
              <div className="race-trait-row">
                {selectedRace.traits.map((trait) => (
                  <span key={trait.index} className="trait-chip">
                    {trait.name}
                    <InfoTip text={trait.desc} />
                  </span>
                ))}
              </div>
            )}
            {raceIndex === 'half-elf' && (
              <fieldset>
                <legend>
                  Skill Versatility - choose 2 skills ({chosenRacialSkills.length}/2 selected)
                </legend>
                {skills.map((skill) => (
                  <label key={skill.index} className="checkbox-row">
                    <input
                      type="checkbox"
                      checked={chosenRacialSkills.includes(skill.index)}
                      onChange={() => toggleRacialSkill(skill.index)}
                    />
                    {skillLabel(skill.index)}
                    <InfoTip text={skill.desc} />
                  </label>
                ))}
              </fieldset>
            )}
            <div className="wizard-nav">
              <button type="button" disabled={!canProceedFromBasics} onClick={() => setStep(1)}>
                Next
              </button>
            </div>
          </section>
        )}

        {step === 1 && (
          <section>
            <label>
              Class
              <span className="select-row">
                <select value={classIndex} onChange={(e) => setClassIndex(e.target.value)}>
                  <option value="">Choose a class...</option>
                  {classes.map((c) => (
                    <option key={c.index} value={c.index}>
                      {c.name} (d{c.hit_die} hit die)
                    </option>
                  ))}
                </select>
                {classIndex && CLASS_HINTS[classIndex] && <InfoTip text={CLASS_HINTS[classIndex]} />}
              </span>
            </label>
            {classIndex && FIGHTING_STYLE_CLASSES.has(classIndex) && (
              <fieldset>
                <legend>Fighting Style</legend>
                {FIGHTING_STYLES.map((style) => (
                  <label key={style.value} className="checkbox-row">
                    <input
                      type="radio"
                      name="fighting-style"
                      checked={fightingStyle === style.value}
                      onChange={() => setFightingStyle(style.value)}
                    />
                    {style.label}
                    <InfoTip text={style.hint} />
                  </label>
                ))}
              </fieldset>
            )}
            {classDetail && (
              <fieldset>
                <legend>
                  Choose {classDetail.skill_choose} skill
                  {classDetail.skill_choose === 1 ? '' : 's'} ({chosenSkills.length}/
                  {classDetail.skill_choose} selected)
                </legend>
                {classDetail.skill_options.map((skill) => (
                  <label key={skill} className="checkbox-row">
                    <input
                      type="checkbox"
                      checked={chosenSkills.includes(skill)}
                      onChange={() => toggleSkill(skill)}
                    />
                    {skillLabel(skill)}
                    {skillDesc.has(skill) && <InfoTip text={skillDesc.get(skill) as string} />}
                  </label>
                ))}
              </fieldset>
            )}
            {classDetail && classDetail.spells_known > 0 && (
              // "Spells Known" caster's level-1 spell choice (issue #30) -
              // Bard/Sorcerer only (classDetail.spells_known is 0 for
              // everyone else). Mirrors the skill-choice fieldset above
              // exactly, showing each spell's real detail (#31/#32's same
              // "show real stats while picking, not just after" pattern).
              <fieldset>
                <legend>
                  Choose {classDetail.spells_known} spell
                  {classDetail.spells_known === 1 ? '' : 's'} ({chosenSpells.length}/
                  {classDetail.spells_known} selected)
                </legend>
                {classDetail.known_spells_pool.map((spell) => (
                  <label key={spell.index} className="checkbox-row">
                    <input
                      type="checkbox"
                      checked={chosenSpells.includes(spell.index)}
                      onChange={() => toggleSpell(spell.index)}
                    />
                    {nameWithSpellDetail(spell)}
                  </label>
                ))}
              </fieldset>
            )}
            <div className="wizard-nav">
              <button type="button" onClick={() => setStep(0)}>
                Back
              </button>
              <button type="button" disabled={!canProceedFromClass} onClick={() => setStep(2)}>
                Next
              </button>
            </div>
          </section>
        )}

        {step === 2 && (
          <section>
            <p>Assign the standard array ({STANDARD_ARRAY.join(', ')}) to your abilities.</p>

            <div className="recommend-row">
              <button type="button" className="secondary" onClick={fillRecommended}>
                Fill recommended for {classes.find((c) => c.index === classIndex)?.name ?? 'your class'}
              </button>
            </div>

            {ABILITIES.map((ability) => (
              <label key={ability} className="ability-row">
                {ability}
                <InfoTip text={ABILITY_HINTS[ability]} />
                {raceBonus(ability) > 0 && (
                  <span className="race-bonus-badge" title={`${selectedRace?.name} racial bonus`}>
                    +{raceBonus(ability)}
                  </span>
                )}
                <select
                  value={assignments[ability]}
                  onChange={(e) =>
                    setAssignments((prev) => ({
                      ...prev,
                      [ability]: e.target.value === '' ? '' : Number(e.target.value),
                    }))
                  }
                >
                  <option value="">-</option>
                  {(assignments[ability] === ''
                    ? remainingValues
                    : [assignments[ability] as number, ...remainingValues]
                  ).map((v) => (
                    <option key={v} value={v}>
                      {v}
                    </option>
                  ))}
                </select>
                {finalScore(ability) !== null && raceBonus(ability) > 0 && (
                  <span className="final-score">-&gt; {finalScore(ability)} total</span>
                )}
              </label>
            ))}
            <div className="wizard-nav">
              <button type="button" onClick={() => setStep(1)}>
                Back
              </button>
              <button type="button" disabled={!canProceedFromAbilities} onClick={() => setStep(3)}>
                Next
              </button>
            </div>
          </section>
        )}

        {step === 3 && (
          <section>
            <p>
              Optional extra gear, beyond your class/background's starting kit - restricted to what{' '}
              {classes.find((c) => c.index === classIndex)?.name ?? 'your class'} is actually
              proficient with.
            </p>
            <fieldset>
              <legend>Weapons</legend>
              {proficientEquipment.filter((e) => e.category === 'weapon').length === 0 && (
                <p className="companion-meta">No weapon proficiencies for this class.</p>
              )}
              {proficientEquipment
                .filter((e) => e.category === 'weapon')
                .map((item) => {
                  const detail = equipmentDetail(item)
                  return (
                    <label key={item.index} className="checkbox-row">
                      <input
                        type="checkbox"
                        checked={chosenEquipment.includes(item.index)}
                        onChange={() => toggleEquipment(item.index)}
                      />
                      {item.name}
                      {detail && <span className="companion-meta"> ({detail})</span>}
                    </label>
                  )
                })}
            </fieldset>
            <fieldset>
              <legend>Armor</legend>
              {proficientEquipment.filter((e) => e.category === 'armor').length === 0 && (
                <p className="companion-meta">No armor proficiencies for this class.</p>
              )}
              {proficientEquipment
                .filter((e) => e.category === 'armor')
                .map((item) => {
                  const detail = equipmentDetail(item)
                  return (
                    <label key={item.index} className="checkbox-row">
                      <input
                        type="checkbox"
                        checked={chosenEquipment.includes(item.index)}
                        onChange={() => toggleEquipment(item.index)}
                      />
                      {item.name}
                      {detail && <span className="companion-meta"> ({detail})</span>}
                    </label>
                  )
                })}
            </fieldset>
            <div className="wizard-nav">
              <button type="button" onClick={() => setStep(2)}>
                Back
              </button>
              <button type="button" disabled={!canSubmit || submitting} onClick={handleSubmit}>
                {submitting ? 'Creating...' : 'Create Character'}
              </button>
            </div>
            {submitError && <p className="wizard-error">{submitError}</p>}
          </section>
        )}
      </div>
      <CharacterPreviewSheet
        name={name}
        race={selectedRace}
        gender={gender}
        classDetail={classDetail}
        classIndex={classIndex}
        className={classes.find((c) => c.index === classIndex)?.name ?? null}
        fightingStyle={fightingStyle}
        chosenSkills={chosenSkills}
        assignments={assignments}
        raceBonus={raceBonus}
        finalScore={finalScore}
        background={backgrounds.find((b) => b.index === backgroundIndex)}
        chosenEquipment={chosenEquipment}
        equipment={equipment}
        chosenSpells={chosenSpells}
      />
    </div>
  )
}
