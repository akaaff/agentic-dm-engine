import { useState } from 'react'
import { ApiError, api } from '../api/client'
import { setAccessKey } from '../api/accessKey'

// Issue #42: the shared-passphrase login screen. App.tsx only renders this
// when /health reports passphrase_required and no already-stored key has
// been verified yet - a correct key is persisted (api/accessKey.ts) so this
// screen doesn't reappear on a reload.
export default function AccessGate({ onUnlocked }: { onUnlocked: () => void }) {
  const [key, setKey] = useState('')
  const [checking, setChecking] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setChecking(true)
    setError(null)
    try {
      await api.verifyAccessKey(key)
      setAccessKey(key)
      onUnlocked()
    } catch (err) {
      setError(err instanceof ApiError && err.status === 401 ? 'Incorrect passphrase.' : 'Could not reach the server.')
    } finally {
      setChecking(false)
    }
  }

  return (
    <div className="wizard">
      <h1>Enter Passphrase</h1>
      <p>This game is invite-only. Enter the passphrase you were given to continue.</p>
      <form onSubmit={handleSubmit}>
        <input
          type="password"
          value={key}
          onChange={(e) => setKey(e.target.value)}
          placeholder="Passphrase"
          autoFocus
        />
        <div className="wizard-nav">
          <button type="submit" disabled={checking || !key}>
            {checking ? 'Checking...' : 'Continue'}
          </button>
        </div>
      </form>
      {error && <div className="wizard-error">{error}</div>}
    </div>
  )
}
