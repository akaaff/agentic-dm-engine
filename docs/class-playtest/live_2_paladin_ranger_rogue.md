# Live game: paladin, ranger, rogue (group 2)

Final status: **in_progress**

### 1. (monsters/companions)

### 2. Silvia (ranger): "I shoot the nearest goblin with my longbow"
- silvia attack Longbow vs goblin_1: 18 vs AC 15 HIT
-   dmg 7 piercing -> goblin_1
- goblin_1 death {'killed_by': 'silvia'}
- goblin_2 move {'from': {'x': 7, 'y': 3}, 'to': {'x': 3, 'y': 1}, 'dashed': False}
  > Silvia loosed an arrow with a steady hand, its flight true and true. With a satisfying thud, the arrow found its mark, piercing Goblin 1's chest with a solid hit. The tiny creature crumpled to the ground, its brief battle over as Silvia's well-aimed shot cut it down to zero hit points.
  > Goblin 2 slinks across the battlefield, closing the distance between itself and Aurelio with a series of quick, cautious steps. "Looks like someone's about to meet their maker!" it yips, its eyes gleaming with a mix of fear and excitement.
  > Goblin 2 hesitates, unable to settle on an action, and the moment passes.

### 3. Fenn (rogue): "I hide and then shoot the nearest goblin with my shortbow"
- fenn move {'from': {'x': 1, 'y': 3}, 'to': {'x': 2, 'y': 2}, 'dashed': False}
- **REJECTED:** 'shortbow' isn't in fenn's equipped weapon set (currently: Shortsword, Dagger) - use 'equip' to switch weapons first
  > Aurelio and Silvia prepare their stances, eyes locked on the three goblins. Fenn moves swiftly, his boots barely making a sound as he slides from one square to another, positioning himself for the coming skirmish.

### 4. Fenn (rogue): "I sneak up on the nearest goblin and stab it with my shortsword"
- fenn move {'from': {'x': 2, 'y': 2}, 'to': {'x': 2, 'y': 2}, 'dashed': False}
- fenn attack Shortsword vs goblin_2: 12 vs AC 15 miss
- goblin_3 move {'from': {'x': 6, 'y': 4}, 'to': {'x': 4, 'y': 2}, 'dashed': False}
  > Aurelio and Fenn, tense and ready, circle the three goblins, their weapons at the ready. The goblins crouch low, their eyes darting between the two humans. Suddenly, Fenn shifts slightly, but not enough to make the goblins notice. He's positioned perfectly, ready to strike when the time is right.
  > Aurelio twitches and Fenn snarls, his shortsword ready but his attempt falls short. With a yip, Goblin 2 dodges Fenn's swipe, the blade glancing off its armor with a faint clink.
  > Goblin 3 slinks across the battlefield, dodging in a half-circle to get behind the adventurers. "Too slow for my little toes, humans!" it yips, its overconfident grin showing sharp teeth.
  > Goblin 3 hesitates, unable to settle on an action, and the moment passes.

### 5. Aurelio (paladin): "I charge the nearest goblin and attack it with my longsword"
- aurelio move {'from': {'x': 0, 'y': 2}, 'to': {'x': 2, 'y': 0}, 'dashed': False}
- aurelio attack Longsword vs goblin_2: 22 vs AC 15 HIT
-   dmg 6 slashing -> goblin_2
  > Aurelio dashes to the left, his boots scuffing the ground as he maneuvers into position. "I'll show you the way, little goblins," he taunts, though his voice carries a hint of nervousness.
  > Aurelio's longsword slices through the air with a practiced grace, catching Goblin 2 off guard with a solid hit. "Feel the weight of my blade!" he growls. The steel finds its mark, dealing a glancing blow that still leaves the goblin with a mere six hit points.

### 6. Silvia (ranger): "I hide behind cover and shoot the nearest goblin with my longbow"
- silvia move {'from': {'x': 0, 'y': 3}, 'to': {'x': 2, 'y': 1}, 'dashed': False}
- silvia attack Longbow vs goblin_2: 6 vs AC 15 miss
- goblin_2 attack Scimitar vs aurelio: 11 vs AC 18 miss
  > Silvia crouches low, her movements fluid and stealthy, creeping through the dim light. She slides between the goblins, her boots barely making a sound on the stone floor.
  > Silvia draws back her longbow, her aim true but fate unkind. The arrow slices through the air, only to glance off Goblin 2's scrawny frame with a soft thud. "Missed me, big girl!" the goblin yips, grinning wickedly.
  > Aurelio stands his ground, ready for the goblin's scimitar. With a swift slash, Goblin 2's blade misses its mark, clanging harmlessly against Aurelio's armor. "Tsk, human," the goblin mutters, shaking its head in mock disappointment.

### 7. Fenn (rogue): "I throw a dagger at the nearest goblin"
- fenn attack Dagger vs goblin_2: 21 vs AC 15 HIT +sneak 5
-   dmg 1 piercing -> goblin_2
- goblin_2 death {'killed_by': 'fenn'}
- goblin_3 move {'from': {'x': 4, 'y': 2}, 'to': {'x': 3, 'y': 1}, 'dashed': False}
- goblin_3 attack Scimitar vs aurelio: 9 vs AC 18 miss
  > Fenn's dagger slices through Goblin 2's armor with a solid hit, the goblin's eyes widen in surprise as it falls silent, its life draining away. "You shouldn't have come here," Fenn growls, though his voice is barely audible over the goblin's final gasp.
  > Goblin 3 slinks forward, its yippy bark barely audible as it moves to a more advantageous position.
  > Aurelio readies himself, muscles tensing as he faces the advancing goblins. Goblin 3 swings his scimitar, the blade slicing through the air with a barely perceptible swish, coming up short as Aurelio's armor deflects the strike. "Nice try, puny scrapper," Aurelio snarls, his voice low and dangerous.

### 8. Aurelio (paladin): "I lay hands on Silvia"
- **REJECTED:** use_item action requires item_or_spell (the item name)

### 9. Aurelio (paladin): "I attack the nearest goblin with my longsword"
  > 

### 10. Silvia (ranger): "I shoot the nearest goblin with my longbow"
  > 

### 11. Fenn (rogue): "I stab the nearest goblin with my shortsword"
- goblin_3 attack Scimitar vs aurelio: 24 vs AC 18 HIT
-   dmg 7 slashing -> aurelio
  > 
  > Goblin 3 swings his scimitar with a yippy flourish, and Aurelio feels the blade slice through his armor, a critical hit dealing a solid hit that leaves him with only 5 hit points left. "Tsk, that was easy!" gloats Goblin 3.

### 12. Aurelio (paladin): "I attack the nearest goblin"
  > 

### 13. Silvia (ranger): "I attack the nearest goblin"
  > 

### 14. Fenn (rogue): "I attack the nearest goblin"
- goblin_3 attack Scimitar vs aurelio: 23 vs AC 18 HIT
-   dmg 3 slashing -> aurelio
  > 
  > Goblin 3 swings his scimitar with a yippy flourish, and the blade slices through Aurelio's armor, drawing a thin line of blood. "Feel the bite of my blade, mortal!" Aurelio staggers but manages to hold his ground, his breath coming in ragged gasps.

### 15. Aurelio (paladin): "I attack the nearest goblin"
  > 

### 16. Silvia (ranger): "I attack the nearest goblin"
  > 

### 17. Fenn (rogue): "I attack the nearest goblin"
- goblin_3 attack Scimitar vs aurelio: 16 vs AC 18 miss
  > 
  > Aurelio readies himself, muscles tensing as he sizes up the goblin, but the blade of Goblin 3's scimitar glances off his sturdy chainmail, sending a spark flying. "Nice try, fella," Aurelio grins, though the goblin just snarls in frustration, its small frame barely denting his defenses.
