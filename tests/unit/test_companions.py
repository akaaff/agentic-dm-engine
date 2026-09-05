from src.engine.companions import build_companion, load_all_companion_specs, load_companion_spec


def test_load_all_companion_specs_returns_five() -> None:
    specs = load_all_companion_specs()
    assert len(specs) == 5
    assert all(spec.persona for spec in specs)


def test_build_companion_derives_a_full_character_sheet() -> None:
    spec = load_companion_spec("grom_ironfist")
    companion = build_companion(spec)

    assert companion.id == "companion_grom"
    assert companion.is_pc is True
    assert companion.is_companion is True
    assert companion.persona == spec.persona
    assert companion.hp > 0
    assert companion.ac > 0


def test_bards_two_proficiency_pools_are_satisfied() -> None:
    # Regression guard for the SRD gotcha documented in CLAUDE.md: Bard has
    # two proficiency_choices entries (3 skills + 3 instruments), both
    # counted together by character_creation's validation.
    spec = load_companion_spec("pip_larkspur")
    companion = build_companion(spec)
    assert companion.class_ == "Bard"
    assert companion.hp > 0


def test_every_companion_spec_builds_without_error() -> None:
    for spec in load_all_companion_specs():
        companion = build_companion(spec)
        assert companion.hp > 0
        assert companion.ac > 0
