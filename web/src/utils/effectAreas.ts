import type { LiveCharacter, LiveEffectArea } from '../ws/sessionClient'

/** Hover text / legend line for a spell area: what it is, how long it lasts, who keeps it up. */
export function effectAreaTitle(
  area: LiveEffectArea,
  characters: Record<string, LiveCharacter>,
): string {
  const caster = characters[area.caster_id]?.name ?? area.caster_id
  const left =
    area.duration_rounds === null
      ? ''
      : ` - ${area.duration_rounds} ${area.duration_rounds === 1 ? 'round' : 'rounds'} left`
  const hold = area.concentration ? `, ends if ${caster} loses concentration` : ''
  return `${area.spell} (${caster}): difficult terrain - moving through costs double${left}${hold}`
}
