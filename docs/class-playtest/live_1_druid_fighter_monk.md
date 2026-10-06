# Live game: druid, fighter, monk (group 1)

Final status: **victory**

### 1. (monsters/companions)

### 2. Wen (monk): "I run up to the nearest goblin and punch it, then kick it as a bonus action"
- wen move {'from': {'x': 1, 'y': 3}, 'to': {'x': 5, 'y': 0}, 'dashed': False}
- wen attack Dart vs goblin_1: 9 vs AC 15 miss
- goblin_3 move {'from': {'x': 6, 'y': 4}, 'to': {'x': 4, 'y': 1}, 'dashed': False}
- goblin_3 attack Scimitar vs wen: 14 vs AC 15 miss
- goblin_1 attack Scimitar vs wen: 20 vs AC 15 HIT
-   dmg 4 slashing -> wen
- goblin_2 move {'from': {'x': 7, 'y': 3}, 'to': {'x': 7, 'y': 2}, 'dashed': False}
  > Wen dashes across the battlefield, the dirt spraying up in his wake as he moves with surprising speed. "You think you can keep up, little goblins?" he taunts, his boots thudding on the ground.
  > Wen hurls his dart with a practiced flick of the wrist, but it falls short, lodging itself in the dirt with a soft thud. "Maybe next time, little fella," he mocks, though the goblin 1 just yips and scuttles back with a snarl.
  > Goblin 3 slinks away, skittering across the battlefield with a yippy, "Got to keep an eye on those sneaky adventurers!" Meanwhile, Brannock and Fern draw their weapons, sizing up their foes with determined glances.
  > Wen readies himself, muscles tensing. The goblin 3 swings its scimitar, but the blade glances off Wen's shield with a clink, leaving him unscathed. "Tsk, not so tough now, are you?" goblin 3 snarls, its voice high and yippy.
  > Goblin 1 snarls and swings his scimitar at Wen, landing a solid hit with a glancing blow that slashes across Wen's arm, leaving a shallow cut. "Tsk, that'll teach you to stand in the way!"
  > Goblin 2 crouches lower, snatching at the air as he shuffles a pace backward, his small form barely noticeable against the shadows.
  > Goblin 2 hesitates, unable to settle on an action, and the moment passes.

### 3. Brannock (fighter): "I charge the nearest goblin and attack it with my longsword"
- brannock move {'from': {'x': 0, 'y': 3}, 'to': {'x': 3, 'y': 0}, 'dashed': False}
- brannock attack Longsword vs goblin_3: 9 vs AC 15 miss
  > Brannock darts to the side, leaving a trail of dust as he edges closer to the goblins, his boots barely making a sound on the forest floor.
  > Brannock swings his longsword at Goblin 3, but his blow falls short, slicing only the air where the goblin just was. "You're quicker than I thought, scamp!"

### 4. Fern (druid): "I cast entangle on the goblins"
- fern saving_throw Entangle: 5 vs 13 -> False
  > Fern's Entangle spell fails to ensnare the third goblin, the plant tendrils twitching weakly before the creature easily sidesteps them with a yip of triumph.

### 5. Wen (monk): "I throw a dart at the nearest goblin"
- **REJECTED:** Unknown spell: 'dart'

### 6. Wen (monk): "I punch the nearest goblin"
- wen attack Dart vs goblin_1: 12 vs AC 15 miss
- goblin_3 attack Scimitar vs brannock: 16 vs AC 19 miss
- goblin_1 attack Scimitar vs wen: 17 vs AC 15 HIT
-   dmg 4 slashing -> wen
  > Wen's dart whizzes through the air, but the Goblin 1 is too quick, dodging with a sneer. "Nice throw, but you aimed wrong, friend!"
  > Brannock readies himself, muscles coiling like a cat about to pounce. Across the way, Goblin 3 swings his scimitar with a yippy flourish, but the blade glances off Brannock's sturdy armor with a clink, leaving the goblin sputtering a disappointed, "Tsk, tsk, too tough for my little toy!"
  > Goblin 1's scimitar swings in a fluid motion, connecting with a solid hit. "Feel the bite of the goblin's blade!" It grazes Wen, leaving a shallow cut that drains his vitality, leaving him with a mere 2 hit points remaining.
  > Goblin 2 hesitates, unable to settle on an action, and the moment passes.

### 7. Brannock (fighter): "I shove the nearest goblin to knock it prone"
- brannock shove_attempt {'target': 'goblin_3', 'actor_total': 21, 'target_total': 20, 'success': True}
- brannock condition_applied {'condition': 'prone', 'target': 'goblin_3'}
  > Brannock grunts as he shoves Goblin 3 to the ground, sending the small creature sprawling with a yelp.

### 8. Fern (druid): "I cast shillelagh on my quarterstaff"
- **REJECTED:** Unknown spell: 'quarterstaff'

### 9. Fern (druid): "I cast produce flame at the nearest goblin"
- fern cast {'spell': 'Produce Flame', 'target': 'goblin_3', 'spell_level': 0, 'roll_total': 13, 'hit': False, 'critical': False}
  > Fern conjures a small flame, but it flickers and fails to land on Goblin 3, sputtering out with a tiny hiss. "Looks like even the flames are second-guessing you, scrawny goblin!"

### 10. Wen (monk): "I attack the nearest goblin"
- wen attack Dart vs goblin_1: 16 vs AC 15 HIT
-   dmg 7 piercing -> goblin_1
- goblin_1 death {'killed_by': 'wen'}
- goblin_3 attack Scimitar vs brannock: 5 vs AC 19 miss
- goblin_2 move {'from': {'x': 7, 'y': 2}, 'to': {'x': 6, 'y': 1}, 'dashed': False}
- goblin_2 attack Scimitar vs wen: 15 vs AC 15 HIT
-   dmg 2 slashing -> wen
- wen condition_applied {'condition': 'unconscious'}
  > Wen's dart whistled through the air with a deadly hiss, its barbed tip finding the goblin's exposed throat. With a choked gurgle, Goblin 1 slumps to the ground, lifeless. "Looks like your luck's run out, tiny tyrant!" Brannock growls, eyeing the fallen foe.
  > Brannock readies himself, muscles tensing. Across from him, Goblin 3 swings his scimitar with a yippy snarl, but the blade glances off Brannock’s sturdy armor with a clink. "Tough stuff, human," the goblin mutters, shaking his head.
  > Goblin 2 slinks forward, its eyes gleaming with mischievous intent. With a stealthy step, it inches closer to Brannock, who merely grumbles, "You little pest, I'll show you the error of your ways!"
  > Goblin 2's scimitar slices through Wen's defenses with a solid hit, though not a critical one. The blade glances off with a sharp clink, leaving only a shallow cut. With a yelp, Wen stumbles, clutching at a suddenly empty head. "Looks like your turn, Brannock!" goblin 2 taunts, grinning wickedly.

### 11. Brannock (fighter): "I use my second wind"
-   heal 0 (Second Wind) -> brannock
  > Brannock growls, clenching his fists as he prepares for the impending battle, his second wind giving him just enough energy to turn the tide.

### 12. Brannock (fighter): "I attack the nearest goblin with my longsword"
- brannock attack Longsword vs goblin_3: 25 vs AC 15 HIT
-   dmg 7 slashing -> goblin_3
- goblin_3 death {'killed_by': 'brannock'}
  > Brannock's longsword arcs through the air with a deadly grace, the tip finding its mark with a critical strike. With a battle cry, he stabs home, driving the blade into Goblin 3's chest. The goblin lets out a yelp of surprise and pain, collapsing to the ground with a thud as its life drains away. "Feel the wrath of the Longsword!" Brannock shouts, the victory clear in his voice.

### 13. Fern (druid): "I cast healing word on Brannock"
-   heal 0 (Healing Word) -> brannock
  > Brannock feels the warmth of healing magic wash over him as Fern chants the words of "Healing Word." The pain begins to recede, and his vision clears to see the faint glow of mana around her hands. "You might be down, but you're not out yet," she declares with a grin, her voice carrying a mix of relief and determination.

### 14. Fern (druid): "I hit the nearest goblin with my quarterstaff"
- wen saving_throw death_save: None vs None -> True
- goblin_2 move {'from': {'x': 6, 'y': 1}, 'to': {'x': 4, 'y': 0}, 'dashed': False}
- goblin_2 attack Scimitar vs brannock: 21 vs AC 19 HIT
-   dmg 8 slashing -> brannock
  > 
  > Brannock and Fern tense up, readying their weapons as the goblins circle warily. Wen's eyes flutter as they focus intently, a flicker of life returning with a successful death save. "Close one," they breathe, their voice a mix of relief and exhaustion.
  > Goblin 2 slinks across the battlefield, its small form barely making a ripple in the dirt as it moves to flank the party. "You can't hide from the Goblin King, worm!"
  > Brannock's shield clatters as Goblin 2's scimitar slices through the air, grazing his arm with a glancing blow. "Nice try, big guy!" Brannock snarls, though the slash still leaves him with only 4 hit points left.

### 15. Brannock (fighter): "I attack the nearest goblin"
- brannock attack Longsword vs goblin_2: 16 vs AC 15 HIT
-   dmg 7 slashing -> goblin_2
- goblin_2 death {'killed_by': 'brannock'}
  > Brannock's longsword arcs through the air with a solid hit, slicing the second goblin in half. "There goes one less pest!" he shouts, his muscles flexing with the effort. The goblin collapses, lifeless.
