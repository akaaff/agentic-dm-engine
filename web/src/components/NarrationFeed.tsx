import { useEffect, useRef } from 'react'

export default function NarrationFeed({ entries }: { entries: string[] }) {
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [entries.length])

  return (
    <div className="narration-feed">
      {entries.length === 0 && <p className="narration-empty">The adventure is about to begin...</p>}
      {entries.map((text, i) => (
        <p key={i} className="narration-entry">
          {text}
        </p>
      ))}
      <div ref={bottomRef} />
    </div>
  )
}
