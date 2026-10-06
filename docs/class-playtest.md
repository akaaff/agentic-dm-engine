# Class playability playtest (level 1, SRD)

Goal: for each of the 12 SRD classes, work out how it is actually played, then play it that
way against the real app and file an issue for every failing case.

- **Scope:** level-1 characters (the only level the UI creates - `level_up` exists in the engine
  but nothing in the app reaches it), SRD 5.1 content only. Several famous level-1 options are
  PHB-only and therefore *not* in scope: Hex, Armor of Agathys, Witch Bolt, Dissonant Whispers,
  Tasha's Hideous Laughter variants, most subclasses (the SRD has exactly one per class: Life
  domain, Draconic Bloodline, Fiend patron, ...).
- **Results:** [`class-playtest/results.md`](class-playtest/results.md) (per-play tables, linked to
  issues), raw data in `class-playtest/*.json`, live transcripts in `class-playtest/live_*.md`.
- **Issues filed:** #75-#107 (label `class-playtest`; priority labels `priority: high/medium/low`),
  plus comments adding class-level data to the pre-existing #55, #56, #62.

## How it was tested

| Layer | Tool | What it proves |
|---|---|---|
| Plays | `scripts/class_playtest.py` | 97 utterances a player of that class really types, each run through the **live intent parser (Ollama) -> `parse_intent_sequence` -> `resolve_action`** against a fresh hand-built encounter (the class + a fighter buddy vs the enemies the play names). Dice are rigged (d20 fixed, other dice at their rounded-up average) so mechanics can be asserted exactly; each play parses twice because the parser is the only non-deterministic part. |
| Static | `--static` | Creation-time facts: does the class start with the features the SRD says it does? |
| Spell sweep | `--sweep` | Every level-0/1 spell on each caster's SRD list cast through the real `cast_spell` path: castable vs "not supported". |
| Live games | `scripts/class_playtest_live.py` | Three classes **in one real game**, one player driving all three seats over the real WebSocket (multi-character play), real parser/engine/narrator, a scripted-but-realistic turn plan per class. Four groups: barbarian+bard+cleric, druid+fighter+monk, paladin+ranger+rogue, sorcerer+warlock+wizard, against `goblin_ambush_oneshot`. |

Re-run: see the docstrings (`uv run python scripts/class_playtest.py --classes rogue --trials 2`;
the live driver needs a throwaway backend on :8001 - command in its docstring;
`uv run python scripts/class_playtest_report.py` rebuilds `results.md`).

Playtest traps the harness itself hit (worth knowing): spell attacks emit `spell_cast` not
`attack_roll`; the real UI uses the *name slug* as the character id (class-prefixed ids made the
narrator confuse characters); the server blocks its event loop during LLM calls, so a client with
keepalive pings is dropped mid-turn (#105).

## Headline

97 plays: 57 pass, 1 flaky, 39 fail. Fighting and basic casting work; the failures cluster in:

1. **Turn-flow plumbing that every class hits** - dead targets accepted (#75; in the live games
   four spell casts (two of them 1st-level slots) and six attack turns were silently spent on corpses), bonus actions named
   after the main action dropped (#76), plural/area targets collapsing to one creature (#78) or
   N casts, `Self (15-foot cone)` spells limited to 5 ft (#79).
2. **Missing level-1 class identity** - Paladin (Lay on Hands, Divine Sense), Rogue (Expertise,
   Hide), Cleric (Life domain), Sorcerer (Draconic Resilience), Warlock (Pact Magic recharge,
   spells/patron), Fighter (3 of 6 fighting styles), Ranger (nothing at all at L1).
3. **Spells that "cast" but do nothing** - the whole save-or-effect family (#96) and dropped
   riders (#97); Shield/Shillelagh/Goodberry/utility cantrips unsupported.
4. **Engine rules bugs** - Sneak Attack twice per turn (#81), prone never ends (#82), ranged
   spell attacks ignore point-blank disadvantage (#80).

## Per class

Typical play, signature mechanics, strengths (general 5e knowledge cross-checked against class
guides such as the [Bell of Lost Souls fighter/paladin guides](https://www.belloflostsouls.net/?p=356855),
[Arcane Eye barbarian/monk/rogue/ranger guides](https://arcaneeye.com/class-guides/5e-Barbarian-guide/),
[gamersdecide's level-1 spell list](https://gamersdecide.com/articles/dnd-best-level-1-spells)) and
what the playtest found. "Works" lists plays that passed.

### Barbarian
- **Play:** rage first, wade in, hit hard, soak damage; thrown javelins when the target is out of
  reach; Athletics grapple/shove; Intimidation.
- **Strengths:** highest HP/die, damage resistance while raging, +2 rage damage, unarmored AC.
- **Works:** rage (bonus action, uses tracked), rage + move + attack in one sentence, grapple,
  shove prone, intimidate, javelin when drawn explicitly.
- **Found:** javelin/shortbow style attacks need an explicit `equip` (#83); "attack, then rage"
  drops the rage (#76); an out-of-reach charge sometimes drops the move (#107).

### Bard
- **Play:** Bardic Inspiration on the party's striker, Healing Word as a bonus-action pick-up,
  Vicious Mockery / Sleep / Thunderwave / Charm Person, social checks.
- **Strengths:** support + control, most skills, Jack-of-all-trades.
- **Works:** inspire an ally, healing word, cure wounds, vicious mockery damage, thunderwave
  damage, rapier attacks.
- **Found:** Sleep hits one creature of a pack (#78); Charm Person rolls the save and does nothing
  (#96); riders missing (Mockery disadvantage, Thunderwave push, #97); persuasion has no effect
  (#102); 3 of 9 cantrips usable, Minor Illusion etc. error (#55).

### Cleric
- **Play:** Bless, Cure/Healing Word, Guiding Bolt, Sacred Flame; frontline in medium/heavy armor.
- **Strengths:** healing, buffs, armor; Life domain heals extra.
- **Works:** Sacred Flame, Bless (three targets), heal-then-fight, Spare the Dying, mace.
- **Found:** Life domain absent (no Disciple of Life: Cure Wounds heals 8 not 11; no heavy armor;
  domain spells not auto-prepared, #88); Command applies nothing (#96); Guiding Bolt advantage
  missing (#97); "cure wounds on myself" sometimes target-less (#99); Guidance unsupported (#55).

### Druid
- **Play:** Produce Flame/Shillelagh cantrips, Entangle opener, Healing Word, Goodberry,
  Faerie Fire for the party's attackers.
- **Strengths:** control and utility casting; Wild Shape arrives at L2.
- **Works:** healing word, poison spray, quarterstaff.
- **Found:** Shillelagh unsupported (#95); Entangle/Faerie Fire single-target and effect-less
  (#78, #96); "conjure a flame" parsed as flamestrike/flame blade (#98); Goodberry unsupported
  (#55 bucket 7).

### Fighter
- **Play:** weapon + armor + fighting style, Second Wind when hurt, shove/grapple, Help.
- **Strengths:** durability, consistent damage, Action Surge/extra attacks at L2/L5.
- **Works:** longsword, Second Wind, shove, Help, Dodge, Archery (+2), potion, dash/disengage.
- **Found:** Great Weapon Fighting, Protection, Two-Weapon Fighting rejected (#92); "attack, then
  second wind" drops the heal (#76); shoved target stays prone forever (#82); no Ready / escape
  grapple / stand up (#100, #82).

### Monk
- **Play:** unarmed Martial Arts with DEX, bonus-action strike after attacking, Unarmored
  Defense, mobility (Acrobatics); Ki starts at L2.
- **Strengths:** speed, AC from WIS, many attacks later.
- **Works:** AC 15 (Unarmored Defense), move + attack.
- **Found:** punches resolve as the equipped Dart (#77); the signature punch + kick turn is
  dropped (#76/#77); "throw a dart" -> `cast_spell dart` (#98); tumble -> move (#101).

### Paladin
- **Play:** armored frontliner, Lay on Hands to heal, Divine Sense, smites from L2.
- **Strengths:** AC, healing pool, burst damage later.
- **Works:** longsword, Help.
- **Found:** Lay on Hands missing (#86) and Divine Sense missing (#87) - the paladin has no L1
  class identity in the app.

### Ranger
- **Play:** longbow at range, swap to shortsword in melee, Survival/tracking and Stealth
  out of combat.
- **Strengths:** ranged damage, scouting, exploration skills.
- **Works:** longbow at range and at point blank (disadvantage applied), switching weapons
  with an explicit draw, perception checks.
- **Found:** Hide has no mechanic (parsed as dodge, #84); "track" maps to Perception (#101);
  Favored Enemy/Natural Explorer not modeled or shown (#103).

### Rogue
- **Play:** hide or flank for Sneak Attack, shortbow/shortsword/dagger throws, Expertise
  skills, Cunning Action from L2.
- **Strengths:** top single-target damage, skills.
- **Works:** Sneak Attack with an adjacent ally (and correctly not when alone), dagger throw,
  dual-wield split.
- **Found:** Sneak Attack twice a turn when dual-wielding (#81); Hide missing (#84); Expertise
  missing (#85); shortbow needs an equip first (#83).

### Sorcerer
- **Play:** Fire Bolt / Magic Missile / Burning Hands, few slots, squishy.
- **Strengths:** highest-damage cantrips, Draconic Resilience toughness.
- **Works:** Fire Bolt, Magic Missile, Ray of Frost/Shocking Grasp casts.
- **Found:** Draconic Resilience missing (AC 12 not 15, #89); Burning Hands is a single target or
  N casts (#78) and only reaches 5 ft (#79); Fire Bolt at point blank has no disadvantage (#80);
  Shield unsupported (#93).

### Warlock
- **Play:** Eldritch Blast every turn, one or two Pact slots recharged on short rests, patron
  features. (Hex and Armor of Agathys - the usual picks - are not SRD.)
- **Strengths:** reliable at-will damage, slots refresh quickly.
- **Works:** Eldritch Blast, Hellish Rebuke, dagger.
- **Found:** Pact slots don't recharge on short rest (#90); no spell choice/list, any spell is
  castable (#91); Charm Person effect-less (#96).

### Wizard
- **Play:** Fire Bolt, Magic Missile, Sleep, Mage Armor/Shield for defense, Find Familiar;
  Arcane Recovery on short rests.
- **Strengths:** widest spell list, area control.
- **Works:** Fire Bolt, Magic Missile, Mage Armor (AC 16), Arcane Recovery.
- **Found:** Sleep hits one creature of a pack (#78, plus slot burned on a dead one, #75); Shield
  unsupported (#93); Find Familiar unsupported (#56); 22 of 41 L0/1 spells unsupported (#55).

## What the live three-class games added

Things the isolated plays could not show:
- **Dead targets (#75)** - the single most damaging finding: "the nearest goblin" resolved to a
  corpse over and over, silently ending turns and burning slots.
- **Area spells in practice (#78, #79)** - Burning Hands rejected at 10 ft; Sleep slept one of three.
- **Narrator attribution (#104)** - with three PCs the narrator mixed up who did what.
- **Monster approach turns (#106)** - "hesitates, unable to settle on an action" on most monster
  turns; **parse timeouts (#105)** dropping the connection.
- Group 3 (sorcerer/warlock/wizard) and group 1 (druid/fighter/monk) ended in victory; group 0
  and 2 hit the turn cap with goblins still up (largely because of the wasted turns above).

## Not tested / out of scope

Levels 2+ (Action Surge, Extra Attack, Cunning Action, Wild Shape, Channel Divinity and
Metamagic are partly implemented, partly not - see CLAUDE.md - and none were exercised here),
companions' AI play, multiplayer with several humans, and the spell backlog beyond levels 0-1
(#55).
