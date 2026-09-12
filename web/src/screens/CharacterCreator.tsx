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
} from '../api/client'

const ABILITIES: AbilityScore[] = ['STR', 'DEX', 'CON', 'INT', 'WIS', 'CHA']
const STANDARD_ARRAY = [15, 14, 13, 12, 10, 8]

// Short, standard 5e explanations - shown inline so a new player doesn't have
// to already know what each ability governs before assigning scores.
const ABILITY_HINTS: Record<AbilityScore, string> = {
  STR: 'Melee attack/damage rolls, carrying capacity, Athletics.',
  DEX: 'Armor Class, ranged & finesse weapons, Stealth/Acrobatics, initiative.',
  CON: "Hit points and concentration saves - rarely a dump stat.",
  INT: 'Arcane spellcasting (Wizard), Investigation/Arcana checks.',
  WIS: 'Divine/Nature spellcasting (Cleric/Druid/Ranger), Perception/Insight.',
  CHA: 'Charisma-based spellcasting (Bard/Sorcerer/Warlock/Paladin), Persuasion/Deception.',
}

// A recommended standard-array assignment is necessarily a guess at this step
// (class isn't chosen until the next one) - offered as a starting point per
// broad archetype, not a single "best" answer. CON is kept high across the
// board since it's rarely a dump stat for any build.
const RECOMMENDED_PRIORITIES: Record<string, AbilityScore[]> = {
  'Melee (Fighter/Barbarian/Paladin)': ['STR', 'CON', 'DEX', 'WIS', 'CHA', 'INT'],
  'Finesse/Ranged (Rogue/Ranger/Monk)': ['DEX', 'CON', 'WIS', 'STR', 'CHA', 'INT'],
  'Arcane caster (Wizard)': ['INT', 'CON', 'DEX', 'WIS', 'CHA', 'STR'],
  'Divine/Nature caster (Cleric/Druid)': ['WIS', 'CON', 'DEX', 'STR', 'CHA', 'INT'],
  'Charisma caster (Bard/Sorcerer/Warlock)': ['CHA', 'CON', 'DEX', 'WIS', 'STR', 'INT'],
}

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

export default function CharacterCreator({ onCreated }: { onCreated: (character: Character) => void }) {
  const [step, setStep] = useState(0)

  const [races, setRaces] = useState<RaceSummary[]>([])
  const [classes, setClasses] = useState<ClassSummary[]>([])
  const [backgrounds, setBackgrounds] = useState<BackgroundSummary[]>([])
  const [equipment, setEquipment] = useState<EquipmentSummary[]>([])
  const [loadError, setLoadError] = useState<string | null>(null)

  const [name, setName] = useState('')
  const [raceIndex, setRaceIndex] = useState('')
  const [assignments, setAssignments] = useState<Record<AbilityScore, number | ''>>({
    STR: '',
    DEX: '',
    CON: '',
    INT: '',
    WIS: '',
    CHA: '',
  })
  const [recommendedArchetype, setRecommendedArchetype] = useState(
    Object.keys(RECOMMENDED_PRIORITIES)[0],
  )
  const [classIndex, setClassIndex] = useState('')
  const [classDetail, setClassDetail] = useState<ClassDetail | null>(null)
  const [chosenSkills, setChosenSkills] = useState<string[]>([])
  const [backgroundIndex, setBackgroundIndex] = useState('')
  const [chosenEquipment, setChosenEquipment] = useState<string[]>([])

  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [created, setCreated] = useState<Character | null>(null)

  useEffect(() => {
    Promise.all([api.listRaces(), api.listClasses(), api.listBackgrounds(), api.listEquipment()])
      .then(([r, c, b, e]) => {
        setRaces(r)
        setClasses(c)
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

  const selectedRace = races.find((r) => r.index === raceIndex)
  const usedValues = Object.values(assignments).filter((v) => v !== '')
  const remainingValues = useMemo(() => {
    const remaining = [...STANDARD_ARRAY]
    for (const v of usedValues) remaining.splice(remaining.indexOf(v as number), 1)
    return remaining
  }, [usedValues])
  const allAbilitiesAssigned = usedValues.length === ABILITIES.length

  function finalScore(ability: AbilityScore): number | null {
    const base = assignments[ability]
    if (base === '') return null
    return base + (selectedRace?.ability_bonuses[ability.toLowerCase()] ?? 0)
  }

  function fillRecommended() {
    const priority = RECOMMENDED_PRIORITIES[recommendedArchetype]
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
  const canProceedFromAbilities = allAbilitiesAssigned
  const canProceedFromClass =
    classIndex !== '' && classDetail !== null && chosenSkills.length === classDetail.skill_choose
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
        {['Basics', 'Ability Scores', 'Class & Skills', 'Background', 'Equipment'].map(
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
          <p>Assign the standard array ({STANDARD_ARRAY.join(', ')}) to your abilities.</p>

          <div className="recommend-row">
            <select
              value={recommendedArchetype}
              onChange={(e) => setRecommendedArchetype(e.target.value)}
              aria-label="Recommended build archetype"
            >
              {Object.keys(RECOMMENDED_PRIORITIES).map((archetype) => (
                <option key={archetype} value={archetype}>
                  {archetype}
                </option>
              ))}
            </select>
            <button type="button" className="secondary" onClick={fillRecommended}>
              Fill recommended
            </button>
          </div>

          {ABILITIES.map((ability) => (
            <div key={ability} className="ability-row">
              <label>
                {ability}
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
                {finalScore(ability) !== null && (
                  <span className="final-score">-&gt; {finalScore(ability)} with racial bonus</span>
                )}
              </label>
              <p className="ability-hint">{ABILITY_HINTS[ability]}</p>
            </div>
          ))}
          <div className="wizard-nav">
            <button type="button" onClick={() => setStep(0)}>
              Back
            </button>
            <button type="button" disabled={!canProceedFromAbilities} onClick={() => setStep(2)}>
              Next
            </button>
          </div>
        </section>
      )}

      {step === 2 && (
        <section>
          <label>
            Class
            <select value={classIndex} onChange={(e) => setClassIndex(e.target.value)}>
              <option value="">Choose a class...</option>
              {classes.map((c) => (
                <option key={c.index} value={c.index}>
                  {c.name} (d{c.hit_die} hit die)
                </option>
              ))}
            </select>
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
                </label>
              ))}
            </fieldset>
          )}
          <div className="wizard-nav">
            <button type="button" onClick={() => setStep(1)}>
              Back
            </button>
            <button type="button" disabled={!canProceedFromClass} onClick={() => setStep(3)}>
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
