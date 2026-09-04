from src.engine.srd_loader import load_srd


def test_load_srd_indexes_by_index_key() -> None:
    srd = load_srd()

    assert srd.monsters["goblin"]["name"] == "Goblin"
    assert srd.classes["fighter"]["hit_die"] == 10
    assert srd.races["human"]["speed"] == 30
    assert srd.backgrounds["acolyte"]["name"] == "Acolyte"
    assert srd.equipment["chain-mail"]["armor_category"] == "Heavy"


def test_load_srd_is_cached() -> None:
    assert load_srd() is load_srd()
