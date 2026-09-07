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
