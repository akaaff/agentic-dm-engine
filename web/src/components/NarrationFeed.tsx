import { useEffect, useRef } from 'react'
import type { LiveCharacter, NarrationEntry } from '../ws/sessionClient'
import { formatEvent } from '../utils/formatEvent'

export default function NarrationFeed({
  entries,
  characters,
  actorColors,
}: {
  entries: NarrationEntry[]
  characters: Record<string, LiveCharacter>
  actorColors: Record<string, string>
}) {
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [entries.length])

  return (
    <div className="narration-feed">
      {entries.length === 0 && <p className="narration-empty">The adventure is about to begin...</p>}
      {entries.map((entry, i) => {
        const badges = (entry.events ?? [])
          .map((event) => formatEvent(event, characters, actorColors))
          .filter((badge) => badge !== null)
        return (
          <div key={i}>
            {entry.text && (
              <p
                className={
                  entry.kind === 'scene' ? 'narration-entry narration-scene' : 'narration-entry'
                }
              >
                {entry.text}
              </p>
            )}
            {badges.length > 0 && (
              <div className="event-badge-row">
                {badges.map((badge) => (
                  <span key={badge.key} className="event-badge" style={{ borderLeftColor: badge.color }}>
                    {badge.label}
                    {badge.highlight && (
                      <strong className="event-badge-highlight" style={{ color: badge.highlight.color }}>
                        {' '}
                        {badge.highlight.text}
                      </strong>
                    )}
                  </span>
                ))}
              </div>
            )}
          </div>
        )
      })}
      <div ref={bottomRef} />
    </div>
  )
}
