/** Hover/focus tooltip - keyboard-accessible (tabIndex + :focus-within),
 * dependency-free. Originally built for the skill-choice picker's
 * ability/skill/class explanations so the wizard stays compact for players
 * who already know the rules; shared here so spell/cantrip hints (issue
 * #51's follow-up) can use the same "hidden until asked for" pattern
 * instead of an always-visible line under every entry. */
export default function InfoTip({ text }: { text: string }) {
  return (
    <span className="info-tip" tabIndex={0}>
      <span aria-hidden="true" className="info-tip-icon">
        ⓘ
      </span>
      <span role="tooltip" className="info-tip-bubble">
        {text}
      </span>
    </span>
  )
}
