import { useEffect, useRef } from 'react'
import type { NarrationEntry } from '../ws/sessionClient'

export default function NarrationFeed({ entries }: { entries: NarrationEntry[] }) {
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [entries.length])

  return (
    <div className="narration-feed">
      {entries.length === 0 && <p className="narration-empty">The adventure is about to begin...</p>}
      {entries.map((entry, i) => (
        <p
          key={i}
          className={entry.kind === 'scene' ? 'narration-entry narration-scene' : 'narration-entry'}
        >
          {entry.text}
        </p>
      ))}
      <div ref={bottomRef} />
    </div>
  )
}
