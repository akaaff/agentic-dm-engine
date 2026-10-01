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

// Live-found: a plain page refresh always dropped back to the Home
// screen, even though the token above is everything needed to resume -
// App.tsx's own `flow` state starts fresh on every mount with nothing
// telling it *which* session to resume. This is that pointer: the most
// recently joined/resumed session id, updated alongside every
// setLobbyToken call (a new join) and every successful manual resume via
// JoinByCode (an existing token, re-entered), so a later plain refresh
// can look this up and attempt the exact same resume flow automatically
// instead of only ever working when the player manually re-types the
// code. A single value, not a list - a player might hold tokens for
// several past lobbies, but only the one they were just in should
// auto-resume.
const LAST_SESSION_KEY = 'dm-last-session-id'

export function getLastSessionId(): string | null {
  try {
    return localStorage.getItem(LAST_SESSION_KEY)
  } catch {
    return null
  }
}

export function setLastSessionId(sessionId: string): void {
  try {
    localStorage.setItem(LAST_SESSION_KEY, sessionId)
  } catch {
    // Best-effort, same as above.
  }
}
