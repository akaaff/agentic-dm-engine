import { useState } from 'react'

// Issue #45: a plain code-entry field, not a deep link scoped to one
// specific character slot (the earlier per-slot-invite-link sketch this
// session's own design conversation explicitly moved away from). App.tsx
// owns what actually happens with the code (resume via a stored token, or
// walk through character creation) - this screen only collects it.
export default function JoinByCode({
  onJoin,
  onBack,
  joining,
  joinError,
}: {
  onJoin: (code: string) => void
  onBack: () => void
  joining: boolean
  joinError: string | null
}) {
  const [code, setCode] = useState('')

  return (
    <div className="wizard">
      <h1>Join a Game</h1>
      <p>Enter the code the host shared with you.</p>
      <input
        type="text"
        value={code}
        onChange={(e) => setCode(e.target.value.trim())}
        placeholder="Session code"
        autoFocus
      />
      <div className="wizard-nav">
        <button type="button" onClick={onBack} disabled={joining}>
          Back
        </button>
        <button type="button" disabled={!code || joining} onClick={() => onJoin(code)}>
          {joining ? 'Joining...' : 'Join'}
        </button>
      </div>
      {joinError && <p className="wizard-error">{joinError}</p>}
    </div>
  )
}
