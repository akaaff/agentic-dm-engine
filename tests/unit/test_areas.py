"""Spell areas as geometry: which squares a cube / sphere / cone / line covers, and where the
engine puts an area so it catches the creatures the caster named and spares their friends."""

from __future__ import annotations

from src.engine.areas import AreaSpec, Occupant, area_cells, area_spec, place_area
from src.engine.position import Position


def _cells(
    spec: AreaSpec, origin: tuple[int, int], caster: tuple[int, int], size: int = 12
) -> set[tuple[int, int]]:
    return {
        (c.x, c.y)
        for c in area_cells(
            spec, Position(x=origin[0], y=origin[1]), Position(x=caster[0], y=caster[1]), size, size
        )
    }


def test_the_spec_is_read_from_the_srd_area() -> None:
    entangle = area_spec({"range": "90 feet", "area_of_effect": {"type": "cube", "size": 20}})
    assert entangle == AreaSpec(shape="cube", size_feet=20, from_caster=False)
    hands = area_spec({"range": "Self", "area_of_effect": {"type": "cone", "size": 15}})
    assert hands is not None and hands.from_caster
    assert area_spec({"range": "Touch"}) is None


def test_a_20_foot_cube_is_a_4_by_4_block_around_its_origin() -> None:
    cells = _cells(AreaSpec("cube", 20, False), origin=(5, 5), caster=(0, 0))
    assert len(cells) == 16
    assert {x for x, _ in cells} == {4, 5, 6, 7} and {y for _, y in cells} == {4, 5, 6, 7}


def test_a_cube_is_clipped_to_the_map_and_stopped_by_walls() -> None:
    corner = _cells(AreaSpec("cube", 20, False), origin=(0, 0), caster=(5, 5))
    assert all(x >= 0 and y >= 0 for x, y in corner) and len(corner) == 9  # origin-1..origin+2
    walled = {
        (c.x, c.y)
        for c in area_cells(
            AreaSpec("cube", 20, False),
            Position(x=5, y=5),
            Position(x=0, y=0),
            12,
            12,
            blocked=lambda x, y: x == 6,
        )
    }
    assert not any(x == 6 for x, _ in walled) and len(walled) == 12


def test_a_sphere_is_a_disc_of_its_radius() -> None:
    cells = _cells(AreaSpec("sphere", 20, False), origin=(6, 6), caster=(0, 0))
    assert (6, 6) in cells and (10, 6) in cells and (6, 2) in cells  # 4 squares out on an axis
    assert (11, 6) not in cells and (10, 10) not in cells  # beyond it / a diagonal corner too far


def test_a_cone_springs_from_the_caster_toward_the_origin() -> None:
    cone = AreaSpec("cone", 15, True)
    cells = _cells(cone, origin=(8, 5), caster=(5, 5))
    assert (6, 5) in cells and (7, 5) in cells and (8, 5) in cells  # straight ahead, 3 squares
    assert (5, 5) not in cells  # never the caster's own square
    assert (4, 5) not in cells and (5, 8) not in cells  # behind and to the side: not in it
    assert (9, 5) not in cells  # past its 15 feet
    wide = {(x, y) for x, y in cells if x == 8}
    assert wide == {(8, 4), (8, 5), (8, 6)}  # widens with distance


def test_a_line_runs_one_square_wide_toward_the_origin() -> None:
    cells = _cells(AreaSpec("line", 30, True), origin=(5, 9), caster=(5, 5))
    assert cells == {(5, 6), (5, 7), (5, 8), (5, 9), (5, 10), (5, 11)}


def test_a_self_cube_springs_from_the_caster_on_the_side_of_the_origin() -> None:
    cells = _cells(AreaSpec("cube", 15, True), origin=(9, 5), caster=(5, 5))
    assert {x for x, _ in cells} == {6, 7, 8} and (5, 5) not in cells


def _occupants(*rows: tuple[str, int, int, bool, bool]) -> list[Occupant]:
    return [
        Occupant(id=i, position=Position(x=x, y=y), ally=ally, named=named)
        for i, x, y, ally, named in rows
    ]


def test_placement_catches_every_named_creature_it_can() -> None:
    placement = place_area(
        AreaSpec("cube", 20, False),
        Position(x=0, y=0),
        range_feet=90,
        width=12,
        height=12,
        occupants=_occupants(("g1", 6, 6, False, True), ("g2", 8, 6, False, True)),
    )
    assert placement is not None and sorted(placement.caught) == ["g1", "g2"]


def test_placement_spares_an_ally_when_it_can() -> None:
    placement = place_area(
        AreaSpec("cube", 20, False),
        Position(x=0, y=0),
        range_feet=90,
        width=12,
        height=12,
        occupants=_occupants(
            ("g1", 6, 6, False, True), ("friend", 7, 6, True, False), ("g2", 10, 6, False, True)
        ),
    )
    assert placement is not None
    assert "friend" not in placement.caught and placement.allies_caught == []
    # one cube can hold g1 or g2 but not both, and never the friend between them
    assert placement.caught and set(placement.caught) <= {"g1", "g2"}


def test_placement_never_picks_an_area_that_misses_everyone_named() -> None:
    placement = place_area(
        AreaSpec("cone", 15, True),
        Position(x=0, y=0),
        range_feet=15,
        width=12,
        height=12,
        occupants=_occupants(("far", 10, 0, False, True), ("near", 1, 0, False, False)),
    )
    assert placement is None  # the cone can't reach the one named; catching "near" isn't the ask


def test_an_explicit_origin_is_used_as_given_allies_included() -> None:
    placement = place_area(
        AreaSpec("cube", 20, False),
        Position(x=0, y=0),
        range_feet=90,
        width=12,
        height=12,
        occupants=_occupants(("g1", 6, 6, False, True), ("friend", 7, 6, True, False)),
        explicit_origin=Position(x=6, y=6),
    )
    assert placement is not None
    assert placement.origin == Position(x=6, y=6)
    assert sorted(placement.caught) == ["friend", "g1"] and placement.allies_caught == ["friend"]


def test_placement_is_deterministic() -> None:
    args = dict(
        spec=AreaSpec("sphere", 20, False),
        caster=Position(x=0, y=0),
        range_feet=90,
        width=12,
        height=12,
        occupants=_occupants(("g1", 6, 6, False, True)),
    )
    first = place_area(**args)  # type: ignore[arg-type]
    second = place_area(**args)  # type: ignore[arg-type]
    assert first is not None and second is not None and first.origin == second.origin
