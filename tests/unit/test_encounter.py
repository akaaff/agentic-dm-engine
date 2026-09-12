from src.engine.character_creation import create_character
from src.engine.encounter import build_encounter_state, load_encounter, monster_to_character
from src.engine.position import Position
from src.engine.srd_loader import load_srd
from src.engine.state import Character


class _FixedRandom:
    def __init__(self, values: list[int]) -> None:
        self._values = list(values)

    def randint(self, a: int, b: int) -> int:
        return self._values.pop(0)


def _make_party() -> list[Character]:
    thorin = create_character(
        character_id="thorin",
        name="Thorin",
        race_index="human",
        class_index="fighter",
        background_index="acolyte",
        base_ability_scores={"STR": 15, "DEX": 14, "CON": 13, "INT": 12, "WIS": 10, "CHA": 8},
        chosen_skills=["skill-athletics", "skill-perception"],
    )
    elrond = create_character(
        character_id="elrond",
        name="Elrond",
        race_index="elf",
        class_index="wizard",
        background_index="acolyte",
        base_ability_scores={"STR": 8, "DEX": 14, "CON": 12, "INT": 15, "WIS": 13, "CHA": 10},
        chosen_skills=["skill-arcana", "skill-history"],
    )
    grom = create_character(
        character_id="grom",
        name="Grom",
        race_index="human",
        class_index="fighter",
        background_index="acolyte",
        base_ability_scores={"STR": 15, "DEX": 8, "CON": 14, "INT": 10, "WIS": 12, "CHA": 13},
        chosen_skills=["skill-intimidation", "skill-survival"],
    )
    return [thorin, elrond, grom]


def test_load_encounter_parses_battle_map_and_monsters() -> None:
    encounter = load_encounter("goblin_ambush")

    assert encounter.id == "goblin_ambush"
    assert encounter.battle_map.width == 8
    assert encounter.battle_map.height == 6
    assert encounter.battle_map.terrain[1][3] == "difficult"
    assert encounter.battle_map.terrain[2][6] == "wall"
    assert len(encounter.monsters) == 3
    assert encounter.party_spawn_points == ["party_1", "party_2", "party_3"]


def test_monster_to_character_reads_srd_stat_block() -> None:
    srd = load_srd()
    goblin_data = srd.monsters["goblin"]

    character = monster_to_character(goblin_data, "goblin_1", Position(x=6, y=1))

    # "Goblin 1", not the bare SRD name "Goblin" - see _display_name's
    # docstring: three identically-named monsters in one encounter used to
    # be visually indistinguishable on both the sidebar and the combat grid.
    assert character.name == "Goblin 1"
    assert character.is_pc is False
    assert character.hp == 7
    assert character.max_hp == 7
    assert character.ac == 15
    assert character.stats == {"STR": 8, "DEX": 14, "CON": 10, "INT": 10, "WIS": 8, "CHA": 8}
    assert character.speed == 30
    assert character.position == Position(x=6, y=1)


def test_monster_to_character_falls_back_to_bare_name_without_a_numeric_suffix() -> None:
    # Every authored encounter names monsters "<index>_<n>", but the display
    # name shouldn't crash or garble a differently-shaped id.
    srd = load_srd()
    goblin_data = srd.monsters["goblin"]

    character = monster_to_character(goblin_data, "the_boss", Position(x=0, y=0))

    assert character.name == "Goblin"


def test_build_encounter_state_places_everyone_and_rolls_initiative() -> None:
    encounter = load_encounter("goblin_ambush")
    party = _make_party()

    # Dict-insertion order is thorin, elrond, grom, goblin_1, goblin_2, goblin_3.
    # DEX modifiers: thorin +2, elrond +3, grom -1, all goblins +2.
    # Raw d20s: thorin=10(+2=12), elrond=15(+3=18), grom=12(-1=11),
    #           goblin_1=8(+2=10), goblin_2=16(+2=18), goblin_3=5(+2=7)
    # 18/18 tie between elrond and goblin_2 broken by higher DEX mod (elrond +3 > +2).
    rng = _FixedRandom([10, 15, 12, 8, 16, 5])

    state = build_encounter_state(encounter, party, rng)  # type: ignore[arg-type]

    assert state.encounter_id == "goblin_ambush"
    assert state.round == 1
    assert state.current_turn == 0
    assert set(state.characters) == {
        "thorin",
        "elrond",
        "grom",
        "goblin_1",
        "goblin_2",
        "goblin_3",
    }
    assert state.turn_order == ["elrond", "goblin_2", "thorin", "grom", "goblin_1", "goblin_3"]

    assert state.characters["thorin"].position == Position(x=0, y=2)
    assert state.characters["elrond"].position == Position(x=0, y=3)
    assert state.characters["grom"].position == Position(x=1, y=3)
    assert state.characters["goblin_1"].position == Position(x=6, y=1)
    assert state.characters["goblin_2"].position == Position(x=7, y=3)
    assert state.characters["goblin_3"].position == Position(x=6, y=4)


def test_day22_encounters_load_and_build_state_with_known_monster_types() -> None:
    """wolf_den (short arc), kobold_ambush and bandit_hideout (full campaign) -
    just confirms each parses and every referenced monster_index exists in
    the vendored SRD, same bar as goblin_ambush above."""
    srd = load_srd()
    party = _make_party()[:2]

    for encounter_id, expected_monster_index, expected_count in [
        ("wolf_den", "wolf", 3),
        ("kobold_ambush", "kobold", 3),
        ("bandit_hideout", "bandit", 2),
    ]:
        encounter = load_encounter(encounter_id)
        assert len(encounter.monsters) == expected_count
        assert all(m.monster_index == expected_monster_index for m in encounter.monsters)

        rng = _FixedRandom([10] * (2 + expected_count))
        state = build_encounter_state(encounter, party, rng, srd=srd)  # type: ignore[arg-type]
        assert len(state.characters) == 2 + expected_count
