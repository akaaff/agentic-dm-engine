You are designing a short D&D 5e campaign for a low-level party (2-3
characters, around level 1) using only 5e SRD content. Output a single JSON
object matching the provided schema exactly - nothing else.

Campaign size: {size}
{scene_guidance}

Every scene needs a `narrative_intro` (2-4 sentences of vivid DM narration
setting up that scene - do not resolve the outcome yourself, that's the
engine's job).

- `type: "narrative_beat"` or `"roleplay"` scenes need only their
  `narrative_intro` - no other fields.
- `type: "combat"` scenes must also set `combat`: pick exactly one
  `monster_index` from the list below (do not invent a monster not on this
  list) and a `monster_count` (2-4) that makes sense for the narrative_intro.
  Also pick a battle-map shape that matches the narrative_intro you just
  wrote: `layout` (`open_room` for an ordinary open space; `narrow_corridor`
  for a tunnel, hallway, or mountain pass; `two_rooms` for two connected
  chambers or a doorway/chokepoint; `cluttered` for wreckage, dense
  undergrowth, or a room full of obstacles), `size` (`small`/`medium`/
  `large` - a `large` room for a bigger fight or `monster_count` of 4, a
  `small` one for a cramped or intimate space), and how much `difficult_
  terrain` and `hazard` the ground itself has (`none`/`light`/`heavy` -
  e.g. rubble, mud, or brambles for difficult terrain; broken glass, hot
  coals, or caltrops-like debris for a hazard). Pick `none`/`open_room`/
  `medium` for an unremarkable space - don't force texture onto every fight.
- `type: "skill_challenge"` scenes must also set `skill_challenge`: pick one
  `skill` from the list below, a `dc` (10-15), and two short flavor lines -
  `success_text` and `failure_text` - each 1-2 sentences describing what
  happens if the party succeeds or fails, without stating game mechanics
  (no "roll a d20", no numbers).

Available monsters (index: name, CR, HP - flavor the narrative to match
whichever you choose, don't reuse the same monster for more than one combat
scene):
{monster_list}

Available skills: {skill_list}

Give the whole campaign a title and a 1-2 sentence description (shown to
the player before they start). Make the scenes connect into one coherent
story - each scene's narrative_intro should follow naturally from the one
before it.

Write every piece of text (title, description, narrative_intro, success_text,
failure_text) only in English - no other language, script, or mixed-language
text anywhere in your response.
