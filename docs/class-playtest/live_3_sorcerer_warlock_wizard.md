# Live game: sorcerer, warlock, wizard (group 3)

Final status: **victory**

### 1. (monsters/companions)
- goblin_3 move {'from': {'x': 6, 'y': 4}, 'to': {'x': 2, 'y': 2}, 'dashed': False}
  > Goblin 3's scimitar slices through Elara's defenses, drawing a thin line of blood. "Taste the blade, elf," it yips, its grin wide and foolish.

### 2. (monsters/companions)
- goblin_3 attack Scimitar vs elara: 21 vs AC 13 HIT
-   dmg 3 slashing -> elara

### 3. (monsters/companions)

### 4. Vex (warlock): "I cast eldritch blast at the nearest goblin"
- vex cast {'spell': 'Eldritch Blast', 'target': 'goblin_3', 'spell_level': 0, 'roll_total': 6, 'hit': False, 'critical': False}
  > Vex lets out a mocking cackle, his eyes glinting with dark glee. "Even a feeble mage can't land a spell," he taunts as his eldritch blast fizzles harmlessly, barely grazing the edge of Goblin 3's helmet.

### 5. Elara (wizard): "I cast sleep on the goblins"
- elara cast {'spell': 'Sleep', 'hp_pool': 21}
- goblin_3 condition_applied {'condition': 'unconscious', 'source': 'sleep'}
- goblin_2 move {'from': {'x': 7, 'y': 3}, 'to': {'x': 3, 'y': 1}, 'dashed': False}
  > Elara chants a spell, the air around her shimmering with an otherworldly light. "Sleep!" she utters, and a wave of drowsiness washes over Goblin 3, who tumbles to the ground unconscious with a small, comical snore.
  > Goblin 2 sidles into a more advantageous position, his small, yippy voice barely above a chuckle as he yips, "Not so fast, elf!" Elara watches him warily, her stance tightening as she prepares for the next move.
  > Goblin 2 hesitates, unable to settle on an action, and the moment passes.

### 6. Ember (sorcerer): "I cast burning hands on the goblins"
- **REJECTED:** goblin_3 is 10ft away - out of range for Burning Hands (max 5ft)

### 7. Ember (sorcerer): "I cast fire bolt at the nearest goblin"
- ember cast {'spell': 'Fire Bolt', 'target': 'goblin_3', 'spell_level': 0, 'roll_total': 17, 'hit': True, 'critical': True}
-   dmg 7 fire -> goblin_3
- goblin_3 death {'killed_by': 'ember'}
- goblin_1 move {'from': {'x': 6, 'y': 1}, 'to': {'x': 2, 'y': 2}, 'dashed': False}
- goblin_1 attack Scimitar vs elara: 12 vs AC 13 miss
  > Ember's Fire Bolt crackled with a searing light, its fiery essence striking Goblin 3 with a critical hit. The small, yippy creature howls in agony as the flames consume it entirely, leaving it dead on the ground. "Tastes like roasted goblin," Ember taunts, her grin wicked.
  > Goblin 1 slinks forward, its small frame barely making a mark on the battlefield as it moves with a yippy agility. "You think you're safe, adventurers?" it taunts, eyes gleaming with overconfidence as it positions itself closer to the party.
  > Elara readies herself, muscles tensing. Vex growls low in his throat, eyes narrowing at the goblin. The goblin swings his scimitar, but his strike falls short, a glancing blow that grazes her arm. "Tsk, tsk, goblin," Vex snarls, "Think you can nick a blade of my kind?"

### 8. Vex (warlock): "I cast hellish rebuke on the nearest goblin"
- vex saving_throw Hellish Rebuke: 20 vs 13 -> True
- vex cast {'spell': 'Hellish Rebuke', 'target': 'goblin_1', 'save_success': True, 'damage': 8}
-   dmg 7 fire -> goblin_1
- goblin_1 death {'killed_by': 'vex'}
  > Vex channels dark energy, shouting, "Feel the heat!" and Hellish Rebuke crackles towards Goblin 1. The small, yippy goblin yelps and tumbles backward, its tiny form igniting in a burst of flame. With a final, smoky exhale, Goblin 1 is no more.

### 9. Elara (wizard): "I cast magic missile at the nearest goblin"
- elara cast {'spell': 'Magic Missile', 'target': 'goblin_1', 'damage': 5, 'auto_hit': True}
-   dmg 0 force -> goblin_1
- elara cast {'spell': 'Magic Missile', 'target': 'goblin_1', 'damage': 4, 'auto_hit': True}
-   dmg 0 force -> goblin_1
- elara cast {'spell': 'Magic Missile', 'target': 'goblin_1', 'damage': 4, 'auto_hit': True}
-   dmg 0 force -> goblin_1
- goblin_2 move {'from': {'x': 3, 'y': 1}, 'to': {'x': 2, 'y': 2}, 'dashed': False}
- goblin_2 attack Scimitar vs elara: 10 vs AC 13 miss
  > Elara's magic missiles zipped through the air with deadly precision, each one finding its mark with a solid hit. "Feel the force of my magic!" she shouted as the first goblin's life flickered out, followed swiftly by two more, their forms vanishing in a puff of smoke as they hit zero hit points.
  > Goblin 2 darts across the battlefield, its small, yippy movements almost comical as it changes position. "I'll be back, you big oaf!" it yips, eyeing Ember warily.
  > Elara readies her sword, muscles tensing. The goblin 2 swings his scimitar, the blade glancing off her shield with a clatter. "Tsk, that's no way to face a goblin!" goblin 2 snarls, though the swing was a mere glancing blow.

### 10. Ember (sorcerer): "I cast magic missile at the nearest goblin"
- ember cast {'spell': 'Magic Missile', 'target': 'goblin_1', 'damage': 4, 'auto_hit': True}
-   dmg 0 force -> goblin_1
- ember cast {'spell': 'Magic Missile', 'target': 'goblin_1', 'damage': 4, 'auto_hit': True}
-   dmg 0 force -> goblin_1
- ember cast {'spell': 'Magic Missile', 'target': 'goblin_1', 'damage': 4, 'auto_hit': True}
-   dmg 0 force -> goblin_1
  > Ember's magic missiles zip through the air, each one finding its target with deadly precision. Goblin 1's body crumples to the ground with a finality that leaves no doubt of the young druid's skill. "Looks like you underestimated the power of nature's wrath," Ember growls, her words dripping with confidence as she prepares to cast another spell.

### 11. Vex (warlock): "I cast eldritch blast at the nearest goblin"
- vex cast {'spell': 'Eldritch Blast', 'target': 'goblin_1', 'spell_level': 0, 'roll_total': 23, 'hit': True, 'critical': False}
-   dmg 0 force -> goblin_1
  > Vex lets loose a crackling blast of arcane energy, and with a small, satisfied smirk, declares, "Feel the lash of the void!" The force of the blast is enough to send Goblin 1 tumbling back, but the small creature's brief respite is cut short as it meets the ground in a heap, lifeless.

### 12. Elara (wizard): "I cast shield"
- **REJECTED:** Shield is not supported - cast_spell resolves attack-roll, save-based, heal, auto-hit, and condition spells, plus a small set of individually-implemented ones (Sleep, True Strike, Mage Armor, Shield of Faith, Spare the Dying); other no-roll/no-damage effects (most buffs/utility) aren't implemented

### 13. Elara (wizard): "I cast fire bolt at the nearest goblin"
- elara cast {'spell': 'Fire Bolt', 'target': 'goblin_1', 'spell_level': 0, 'roll_total': 13, 'hit': False, 'critical': False}
- goblin_2 attack Scimitar vs elara: 15 vs AC 13 HIT
-   dmg 4 slashing -> elara
- elara condition_applied {'condition': 'unconscious'}
  > Elara channels a streak of flame at the nearest goblin, but the spell fizzles just short of its mark. "Missed by a hair, goblin scum!" Vex snarls, charging forward with a war cry.
  > Goblin 2's scimitar whips through the air with a sharp hiss, finding its mark with a solid hit. Elara stumbles back, a look of shock crossing her face as she crumples to the ground, unconscious. "Looks like your turn is over, pretty lady," snickers Goblin 2, his voice dripping with mock admiration.

### 14. Ember (sorcerer): "I cast fire bolt at the nearest goblin"
- ember cast {'spell': 'Fire Bolt', 'target': 'goblin_1', 'spell_level': 0, 'roll_total': 18, 'hit': True, 'critical': False}
-   dmg 0 fire -> goblin_1
  > Ember lets loose a searing bolt of flame, and with a yelp, Goblin 1 meets its end in a puff of smoke and ash.

### 15. Vex (warlock): "I attack the nearest goblin"
- elara saving_throw death_save: None vs None -> True
- goblin_2 move {'from': {'x': 2, 'y': 2}, 'to': {'x': 1, 'y': 1}, 'dashed': False}
- goblin_2 attack Scimitar vs ember: 22 vs AC 12 HIT
-   dmg 4 slashing -> ember
  > 
  > Elara gasps as she clutches her side, a wave of relief washing over her as she manages to beat her death save with a natural 18. Vex, ever the quick-witted, snickers, "Looks like you're still hanging on, Elara. Maybe next time you'll have better luck."
  > Goblin 2 slinks forward, shifting from its original position to a closer vantage point. "Gotcha in my sights now, little elf," it whispers, though its words carry more bravado than confidence.
  > Goblin 2 swings its scimitar at Ember with a snarl, catching her with a solid hit. "Feel the bite of the goblin's blade!" Ember stumbles back, blood seeping from a shallow cut on her arm, her armor ringing faintly.

### 16. Ember (sorcerer): "I attack the nearest goblin"
- ember attack Dagger vs goblin_2: 18 vs AC 15 HIT
-   dmg 4 piercing -> goblin_2
  > Ember's dagger slices through Goblin 2's armor, drawing a yelp of pain from the small creature. With a swift thrust, Ember delivers a glancing blow, leaving the goblin weakened but far from down. "Feel the burn, scrawny!"

### 17. Vex (warlock): "I attack the nearest goblin"
- elara saving_throw death_save: None vs None -> False
- goblin_2 attack Scimitar vs ember: 6 vs AC 12 miss
  > 
  > Elara gasps, clenching her side where a sharp pain erupts. "Not... not like this," she grits out through clenched teeth, her grip loosening on her sword as she struggles to hold on. Vex, standing nearby, lets out a cruel laugh, his eyes gleaming with malice.
  > Vex growls, "You're all just meat, little scamps!" as Goblin 2 swings its scimitar, but Ember weaves around the clumsy slash, narrowly avoiding the glancing blow.

### 18. Ember (sorcerer): "I attack the nearest goblin"
- ember attack Dagger vs goblin_2: 18 vs AC 15 HIT
-   dmg 3 piercing -> goblin_2
- goblin_2 death {'killed_by': 'ember'}
  > Ember's dagger slices through Goblin 2's armor, a solid hit that leaves the small creature gasping for breath. With a final, desperate snarl, Goblin 2 crumples to the ground, lifeless.
