// Issue #42: the shared-passphrase gate's per-browser storage. localStorage
// (not a cookie - this app has no server-side session concept to tie a
// cookie to) so it survives a page reload but stays scoped to this one
// browser, matching the app's other per-viewer-convenience storage (e.g.
// LivePlay's dm-debug-mode toggle).
const STORAGE_KEY = 'dm-access-key'

export function getAccessKey(): string | null {
  try {
    return localStorage.getItem(STORAGE_KEY)
  } catch {
    return null
  }
}

export function setAccessKey(key: string): void {
  try {
    localStorage.setItem(STORAGE_KEY, key)
  } catch {
    // Best-effort - a private window or blocked site data just means the
    // gate re-prompts next reload, not a hard failure.
  }
}
