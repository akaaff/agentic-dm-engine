/** The minimal slice of an HTMLAudioElement that `watchAudioUntilDone` uses - so it can be
 * exercised with a stand-in outside a browser. */
export interface AudioLike {
  duration: number
  addEventListener(type: string, listener: () => void, options?: { once?: boolean }): void
}

export interface AudioGateOptions {
  /** How long to wait for the clip to start loading before giving up on it. */
  loadTimeoutMs?: number
  /** How long past the clip's own length to wait for 'ended' before giving up on it. */
  graceMs?: number
}

/** Calls `onDone` exactly once, when the audio ends, errors, or a safety net trips.
 *
 * The safety net exists so a clip that never loads (a bad URL, a network hang) can't wedge
 * the narration log forever. It used to be a flat 20 s timer started with the clip - but a
 * long narration line is more than 20 s of speech (an 85-word line is ~28 s), so the timer
 * released the next line while the first was still playing and the two voices overlapped.
 * Now the timer only covers "the clip never started": once its metadata arrives, the net is
 * re-armed to the clip's real length plus a grace period, so a long clip is never cut short.
 * Returns a function that settles the gate immediately (used when the clip can't be played). */
export function watchAudioUntilDone(
  audio: AudioLike,
  onDone: () => void,
  options: AudioGateOptions = {},
): () => void {
  const loadTimeoutMs = options.loadTimeoutMs ?? 20000
  const graceMs = options.graceMs ?? 5000
  let finished = false
  let timer: ReturnType<typeof setTimeout> | undefined

  const finish = () => {
    if (finished) return
    finished = true
    if (timer !== undefined) clearTimeout(timer)
    onDone()
  }

  timer = setTimeout(finish, loadTimeoutMs)
  audio.addEventListener(
    'loadedmetadata',
    () => {
      if (finished) return
      if (timer !== undefined) clearTimeout(timer)
      // An unknown or infinite duration (a stream) falls back to the load timeout's length.
      const lengthMs = Number.isFinite(audio.duration) ? audio.duration * 1000 : loadTimeoutMs
      timer = setTimeout(finish, lengthMs + graceMs)
    },
    { once: true },
  )
  audio.addEventListener('ended', finish, { once: true })
  audio.addEventListener('error', finish, { once: true })
  return finish
}
