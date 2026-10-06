# Live game: barbarian, bard, cleric (group 0)

Final status: **in_progress**

### 1. (monsters/companions)
- goblin_3 move {'from': {'x': 6, 'y': 4}, 'to': {'x': 2, 'y': 2}, 'dashed': False}
  > Goblin 3 swings his scimitar at Ilsa, but the blade glances off her shield with a clatter, leaving her unmarked. "Tsk, weak stuff," the goblin mutters, shaking his head.

### 2. (monsters/companions)
- goblin_3 attack Scimitar vs ilsa: 5 vs AC 16 miss
  > Goblin 1 slinks forward, its steps barely disturbing the dirt. "I'll be behind you, Grunna!" it hisses, positioning itself just out of sight.

### 3. (monsters/companions)
- goblin_1 move {'from': {'x': 6, 'y': 1}, 'to': {'x': 3, 'y': 1}, 'dashed': False}
  > Goblin 1 hesitates, unable to settle on an action, and the moment passes.

### 4. (monsters/companions)
  > Goblin 2 slinks away, crouching low as it moves between the rocks, leaving a brief shimmer in the air where its small form just was.

### 5. (monsters/companions)
- goblin_2 move {'from': {'x': 7, 'y': 3}, 'to': {'x': 4, 'y': 1}, 'dashed': False}
  > Goblin 2 hesitates, unable to settle on an action, and the moment passes.

### 6. (monsters/companions)

### 7. (monsters/companions)

### 8. Ilsa (cleric): "I cast bless on myself, Grunna and Lark"
- ilsa cast {'spell': 'Bless', 'targets': ['ilsa', 'grunna', 'lark']}
- ilsa condition_applied {'condition': 'blessed', 'source': 'Bless'}
- grunna condition_applied {'condition': 'blessed', 'source': 'Bless'}
- lark condition_applied {'condition': 'blessed', 'source': 'Bless'}
  > Ilsa raises her hands in a flash of light, a glow spreading over Grunna and Lark. "May your courage and strength be sharpened in this fight!"

### 9. Lark (bard): "I cast vicious mockery at the nearest goblin"
- lark saving_throw Vicious Mockery: 0 vs 13 -> False
- lark cast {'spell': 'Vicious Mockery', 'target': 'goblin_3', 'save_success': False, 'damage': 1}
-   dmg 1 psychic -> goblin_3
  > Grunna and Ilsa sized up their foes, muscles tensing. Lark's eyes glinted with malice as she unleashed a Vicious Mockery, her voice cutting through the air with a sharp, wit-filled taunt, but the goblin 3 merely snarled in response, barely flinching as Lark's spell left a psychic sting, reducing the goblin’s resilience by just a point.

### 10. Grunna (barbarian): "I rage and charge the nearest goblin, then chop it with my greataxe"
- grunna rage {}
- **REJECTED:** goblin_3 is 10ft away - out of range for Greataxe (max 5ft)
  > Grunna's eyes flash with fury as she unleashes a blood-curdling roar, her rage fueling her next move.

### 11. Grunna (barbarian): "I attack the nearest goblin with my greataxe"
- **REJECTED:** goblin_3 is 10ft away - out of range for Greataxe (max 5ft)

### 12. Grunna (barbarian): "I attack the nearest goblin"
- **REJECTED:** goblin_3 is 10ft away - out of range for Greataxe (max 5ft)

### 13. Grunna (barbarian): "I grapple the nearest goblin"
- grunna grapple_attempt {'target': 'goblin_3', 'actor_total': 13, 'target_total': 14, 'success': False}
- goblin_3 attack Scimitar vs ilsa: 8 vs AC 16 miss
  > Grunna grapples with Goblin 3, straining but unable to get a firm hold, as the goblin's muscles prove stronger than expected.
  > Goblin 3 swings his scimitar at Ilsa, but the blade glances off her armor with a dull thud. She barely flinches, her shield catching the edge of the attack. "Nice try, scrawny," Ilsa snorts, her eyes narrowing as she readies herself for the next strike.
  > Goblin 1 hesitates, unable to settle on an action, and the moment passes.
  > Goblin 2 hesitates, unable to settle on an action, and the moment passes.

### 14. Ilsa (cleric): "I cast guiding bolt at the nearest goblin"
- ilsa cast {'spell': 'Guiding Bolt', 'target': 'goblin_3', 'spell_level': 1, 'roll_total': 21, 'hit': True, 'critical': False}
-   dmg 6 radiant -> goblin_3
- goblin_3 death {'killed_by': 'ilsa'}
  > Ilsa's Guiding Bolt arcs through the air, a glowing streak of holy light, and finds its mark with a solid hit. With a yelp, Goblin 3 crumples to the ground, the radiant damage searing through its small, overconfident form. "Looks like your luck ran out, you lousy kobold!" Grunna snickers, crossing her arms.

### 15. Lark (bard): "I inspire Grunna"
- lark bardic_inspiration {'target': 'grunna', 'die_sides': 6}
  > Lark lets out a sharp whistle, her voice carrying a melody of encouragement. Grunna's eyes flash with renewed vigor as a surge of inspiration washes over her.

### 16. Lark (bard): "I cast sleep on the goblins"
- lark cast {'spell': 'Sleep', 'hp_pool': 24}
- goblin_1 condition_applied {'condition': 'unconscious', 'source': 'sleep'}
  > Grumbling under its breath, Lark waves her staff, and a shimmering mist erupts, settling over Goblin 1 like a thick, invisible blanket. The small, yippy creature stumbles and falls, unconscious, snoring softly. "You're next, big fellows," she taunts with a smirk.

### 17. Grunna (barbarian): "I throw a javelin at the nearest goblin"
- **REJECTED:** 'javelin' isn't in grunna's equipped weapon set (currently: Greataxe) - use 'equip' to switch weapons first

### 18. Grunna (barbarian): "I attack the nearest goblin"
- goblin_2 move {'from': {'x': 4, 'y': 1}, 'to': {'x': 2, 'y': 2}, 'dashed': False}
- goblin_2 attack Scimitar vs ilsa: 6 vs AC 16 miss
  > 
  > 
  > Goblin 2 slinks across the battlefield, its footsteps barely making a sound as it skitters to a stop just out of reach, leaving a trail of shadowy movement in the flickering torchlight. "You think you can hide from me, little elf?" Grunna taunts, though her words are drowned out by the growling snarl of Goblin 2 as it prepares for the next move.
  > Goblin 2 swings his scimitar at Ilsa, but the blade glances off her sturdy armor, a solid hit that fails to pierce the gap in her defenses. "Tsk, too tough for my little toy," he grumbles, his voice dripping with disappointment.

### 19. Ilsa (cleric): "I cast sacred flame on the nearest goblin"
- ilsa saving_throw Sacred Flame: 12 vs 13 -> False
- ilsa cast {'spell': 'Sacred Flame', 'target': 'goblin_2', 'save_success': False, 'damage': 7}
-   dmg 7 radiant -> goblin_2
- goblin_2 death {'killed_by': 'ilsa'}
  > Ilsa's Sacred Flame blazed forth, but the goblin just barely avoided it with a flinch, though not without a small, yippy yelp of pain as the radiant flames still managed to catch it. The goblin slumped to the ground, lifeless. "Looks like your dancing flames did the trick, Grunna!"

### 20. Lark (bard): "I cast healing word on Ilsa"
-   heal 0 (Healing Word) -> ilsa
  > Grunna steps forward, her eyes scanning the battlefield with a calculated gaze. "It won't be your turn to heal much longer, Ilsa," she taunts, her voice dripping with malice. Meanwhile, the goblins circle, their small frames twitching with anticipation. Goblin 1 lunges, its tiny claws bared, but Grunna's quick reflexes allow her to side-step the attack with a swift, evasive move. Goblin 2, more foolish than its kin, makes a solid hit on Lark, but Lark merely laughs, her healing magic restoring her friend Ilsa to full health, who now stands ready to face the challenge anew.

### 21. Lark (bard): "I stab the nearest goblin with my rapier"
  > 

### 22. Grunna (barbarian): "I attack the nearest goblin"
  > 
  > 

### 23. Ilsa (cleric): "I cast cure wounds on Grunna"
- **REJECTED:** ilsa has no level-1 spell slots remaining
