// Issue #45: personal per-lobby tokens (issue #44's backend concept),
// keyed by session id rather than one global value like accessKey.ts's
// passphrase - a player can join more than one lobby over time, each with
// its own token identifying them within that specific session.
const STORAGE_PREFIX = 'dm-lobby-token:'

export function getLobbyToken(sessionId: string): string | null {
  try {
    return localStorage.getItem(STORAGE_PREFIX + sessionId)
  } catch {
    return null
  }
}

export function setLobbyToken(sessionId: string, token: string): void {
  try {
    localStorage.setItem(STORAGE_PREFIX + sessionId, token)
  } catch {
    // Best-effort, same as accessKey.ts - a private window or blocked site
    // data just means this browser won't auto-resume next time.
  }
}
