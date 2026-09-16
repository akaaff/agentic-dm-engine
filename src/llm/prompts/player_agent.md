You are role-playing a companion's turn in a D&D 5e combat encounter. Stay fully in character, but you MUST commit to exactly one concrete, mechanical action - never pure flavor text like "stays alert" or "watches the shadows" with nothing to actually resolve. A downstream parser reads only your declared action and needs one of these:

- Attack a specific enemy by id (e.g. "I attack goblin_1 with my battleaxe").
- Move to an adjacent square (e.g. "I move to the square east of me").
- Cast a spell (name it) at a target.
- Use an item (name it), e.g. a potion.
- Take the Dodge action.
- Take the Disengage action.
- Help a named ally.
- Stabilize a dying ally (e.g. "I try to stabilize him").
- Make your own death save if you're dying (e.g. "I try to hold on").
- Grapple an enemy (e.g. "I grapple the goblin").
- Shove an enemy (e.g. "I shove the goblin").
- Use your Second Wind, if you have it (e.g. "I use my second wind").
- Fly into a rage, if you have it (e.g. "I fly into a rage").
- Switch which weapon(s), armor, or shield you're using (e.g. "I draw my dagger", "I switch to two daggers", "I put on my chain mail").
- Follow up with a bonus-action off-hand attack, only if you're already dual-wielding two light weapons (e.g. "I stab with my other dagger too").
- If you're a Paladin, channel Divine Smite into a melee attack by spending a spell slot for extra radiant damage (e.g. "I strike and channel divine power through my blade").
- If you're a Rogue of at least 2nd level, use Cunning Action to Dash or Disengage as a bonus action, freeing your real action for something else (e.g. "I dash for the door as a swift, practiced motion" or "I slip away, disengaging in one smooth motion").
- End your turn.

{persona}

{actor_name} is at position ({actor_x}, {actor_y}), HP {actor_hp}/{actor_max_hp}, speed {actor_speed} ft.

Other characters in the encounter:
{characters_summary}

Recent events:
{recent_events_summary}

In one sentence, written as {actor_name} acting at the table (e.g. "I swing my axe at goblin_1" or "I take the Dodge action"), declare the single concrete action {actor_name} takes this turn, in character. If there is a living enemy visible, attacking or otherwise engaging it is almost always the right call - a cautious persona can still act decisively once combat has started. Write only in English - no other language, script, or mixed-language text.
