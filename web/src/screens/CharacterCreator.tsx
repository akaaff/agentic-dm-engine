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
  const [classIndex, setClassIndex] = useState('')
  const [classDetail, setClassDetail] = useState<ClassDetail | null>(null)
  const [chosenSkills, setChosenSkills] = useState<string[]>([])
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
    api
      .getClass(classIndex)
      .then(setClassDetail)
      .catch((err: unknown) => setLoadError(err instanceof Error ? err.message : String(err)))
  }, [classIndex])

  const skillDesc = useMemo(
    () => new Map(skills.map((s) => [s.index, s.desc])),
    [skills],
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

  function toggleEquipment(index: string) {
    setChosenEquipment((prev) =>
      prev.includes(index) ? prev.filter((i) => i !== index) : [...prev, index],
    )
  }

  const canProceedFromBasics = name.trim().length > 0 && raceIndex !== ''
  const canProceedFromClass =
    classIndex !== '' && classDetail !== null && chosenSkills.length === classDetail.skill_choose
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
      })
      setCreated(character)
      setStep(5)
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
              setAssignments({ STR: '', DEX: '', CON: '', INT: '', WIS: '', CHA: '' })
              setClassIndex('')
              setChosenSkills([])
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
    <div className="wizard">
      <h1>Create a Character</h1>
      <ol className="steps">
        {['Basics', 'Class & Skills', 'Ability Scores', 'Background', 'Equipment'].map(
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
            <select value={raceIndex} onChange={(e) => setRaceIndex(e.target.value)}>
              <option value="">Choose a race...</option>
              {races.map((r) => (
                <option key={r.index} value={r.index}>
                  {r.name} (speed {r.speed} ft)
                </option>
              ))}
            </select>
          </label>
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
          <label>
            Background
            <select value={backgroundIndex} onChange={(e) => setBackgroundIndex(e.target.value)}>
              <option value="">Choose a background...</option>
              {backgrounds.map((b) => (
                <option key={b.index} value={b.index}>
                  {b.name}
                </option>
              ))}
            </select>
          </label>
          <div className="wizard-nav">
            <button type="button" onClick={() => setStep(2)}>
              Back
            </button>
            <button type="button" disabled={backgroundIndex === ''} onClick={() => setStep(4)}>
              Next
            </button>
          </div>
        </section>
      )}

      {step === 4 && (
        <section>
          <p>Optional extra gear, beyond your class/background's starting kit:</p>
          <fieldset>
            <legend>Weapons</legend>
            {equipment
              .filter((e) => e.category === 'weapon')
              .map((item) => (
                <label key={item.index} className="checkbox-row">
                  <input
                    type="checkbox"
                    checked={chosenEquipment.includes(item.index)}
                    onChange={() => toggleEquipment(item.index)}
                  />
                  {item.name}
                </label>
              ))}
          </fieldset>
          <fieldset>
            <legend>Armor</legend>
            {equipment
              .filter((e) => e.category === 'armor')
              .map((item) => (
                <label key={item.index} className="checkbox-row">
                  <input
                    type="checkbox"
                    checked={chosenEquipment.includes(item.index)}
                    onChange={() => toggleEquipment(item.index)}
                  />
                  {item.name}
                </label>
              ))}
          </fieldset>
          <div className="wizard-nav">
            <button type="button" onClick={() => setStep(3)}>
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
  )
}
