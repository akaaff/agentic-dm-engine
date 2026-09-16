import type { LiveCharacter } from '../ws/sessionClient'

// Issue #27: class_resources (Rage, Second Wind, Ki, Wild Shape, Bardic
// Inspiration) were only ever visible as a passive stat list in
// CharacterDetailSheet - nothing told a player *what to type* to actually
// use one, or that it's usable right now. Each entry here mirrors the exact
// gate turn_engine.py's own resolver checks (bonus_action_used/is_raging/
// wild_shape_beast_index - see _resolve_rage/_resolve_second_wind/
// _resolve_flurry_of_blows/_resolve_wild_shape/_resolve_bardic_inspiration),
// so a suggestion is never shown for a resource the engine would actually
// reject right now.
export interface ResourceQuickAction {
  key: string
  label: string
  remaining: number
  suggestedText: string
}

// arcane_recovery is deliberately absent - it isn't a turn action at all,
// resolved automatically by apply_short_rest (see resting.py), so there's
// nothing to suggest typing for it.
export function computeResourceQuickActions(
  me: LiveCharacter,
  characters: Record<string, LiveCharacter>,
): ResourceQuickAction[] {
  const actions: ResourceQuickAction[] = []
  const remaining = (key: string) => me.class_resources[key] ?? 0

  if (remaining('rage') > 0 && !me.is_raging && !me.bonus_action_used) {
    actions.push({
      key: 'rage',
      label: 'Rage',
      remaining: remaining('rage'),
      suggestedText: 'I fly into a rage',
    })
  }

  if (remaining('second_wind') > 0 && !me.bonus_action_used) {
    actions.push({
      key: 'second_wind',
      label: 'Second Wind',
      remaining: remaining('second_wind'),
      suggestedText: 'I use my second wind',
    })
  }

  if (remaining('ki') > 0 && !me.bonus_action_used) {
    const enemy = Object.values(characters).find((c) => !c.is_pc && !c.is_dead)
    if (enemy) {
      actions.push({
        key: 'ki',
        label: 'Flurry of Blows',
        remaining: remaining('ki'),
        suggestedText: `I use Flurry of Blows on ${enemy.name}`,
      })
    }
  }

  if (remaining('wild_shape') > 0 && !me.wild_shape_beast_index) {
    actions.push({
      key: 'wild_shape',
      label: 'Wild Shape',
      remaining: remaining('wild_shape'),
      suggestedText: 'I wild shape into a wolf',
    })
  }

  if (remaining('bardic_inspiration') > 0 && !me.bonus_action_used) {
    const ally = Object.values(characters).find(
      (c) => c.id !== me.id && (c.is_pc || c.is_companion) && !c.is_dead,
    )
    if (ally) {
      actions.push({
        key: 'bardic_inspiration',
        label: 'Bardic Inspiration',
        remaining: remaining('bardic_inspiration'),
        suggestedText: `I give ${ally.name} Bardic Inspiration`,
      })
    }
  }

  return actions
}
