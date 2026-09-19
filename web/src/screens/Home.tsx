// Issue #45: the very first screen - host a brand-new game (creates a
// lobby immediately, per the campaign-first design) or join one someone
// else already created, via its shared code. Code-only discovery, chosen
// explicitly over an in-app "browse open lobbies" list - smaller attack
// surface, no new endpoint to expose which sessions exist.
export default function Home({
  onHost,
  onJoin,
}: {
  onHost: () => void
  onJoin: () => void
}) {
  return (
    <div className="wizard">
      <h1>Agentic DM</h1>
      <p>Host a new adventure, or join one with a code someone shared with you.</p>
      <div className="wizard-nav">
        <button type="button" onClick={onHost}>
          Host a New Game
        </button>
        <button type="button" className="secondary" onClick={onJoin}>
          Join with a Code
        </button>
      </div>
    </div>
  )
}
