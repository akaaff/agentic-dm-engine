import { useEffect, useState } from 'react'
import { api, type Character } from '../api/client'

const MAX_COMPANIONS = 4

export default function PartySetup({
  onNext,
  onBack,
}: {
  onNext: (companionIds: string[]) => void
  onBack: () => void
}) {
  const [companions, setCompanions] = useState<Character[]>([])
  const [selected, setSelected] = useState<string[]>([])
  const [loadError, setLoadError] = useState<string | null>(null)

  useEffect(() => {
    api
      .listCompanions()
      .then(setCompanions)
      .catch((err: unknown) => setLoadError(err instanceof Error ? err.message : String(err)))
  }, [])

  function toggle(id: string) {
    setSelected((prev) => {
      if (prev.includes(id)) return prev.filter((i) => i !== id)
      if (prev.length >= MAX_COMPANIONS) return prev
      return [...prev, id]
    })
  }

  if (loadError) {
    return <div className="wizard-error">Could not load companions: {loadError}</div>
  }

  return (
    <div className="wizard">
      <h1>Choose Your Party</h1>
      <p>
        Pick up to {MAX_COMPANIONS} companions to adventure with ({selected.length}/
        {MAX_COMPANIONS} selected). You can also go it alone.
      </p>
      <div className="companion-grid">
        {companions.map((c) => (
          <label
            key={c.id}
            className={`companion-card ${selected.includes(c.id) ? 'selected' : ''}`}
          >
            <input
              type="checkbox"
              checked={selected.includes(c.id)}
              onChange={() => toggle(c.id)}
            />
            <div>
              <strong>{c.name}</strong>
              <div className="companion-meta">
                {c.race} {c.class_} - HP {c.hp}, AC {c.ac}
              </div>
              <p className="companion-persona">{c.persona}</p>
            </div>
          </label>
        ))}
      </div>
      <button type="button" onClick={onBack}>
        Back
      </button>
      <button type="button" onClick={() => onNext(selected)}>
        Next
      </button>
    </div>
  )
}
