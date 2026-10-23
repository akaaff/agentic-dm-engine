"""Spell areas as real geometry on the battle grid, and picking where to put one.

A spell's `area_of_effect` (`{type: cube|sphere|cone|line|cylinder, size: feet}`) used to be
ignored: the engine affected whichever creatures the caster *named*, so there was no "who is
actually caught", no friendly fire by position, and nowhere to put the difficult terrain
Entangle creates. This module is the pure, LLM-free half of the fix: given a shape, a size and an
origin it returns the squares covered, and given the creatures on the map it picks the origin
that catches the right ones.

Conventions (one grid square = 5 ft, x to the right, y down, like the rest of the engine):
- cube: an n x n block (n = size / 5) with the origin near its middle. A *Self*-range cube
  (Thunderwave) instead springs from the caster, on the side the origin square lies in.
- sphere / cylinder: every square whose centre is within size / 5 squares of the origin.
- cone: from the caster toward the origin square, size / 5 squares long, about 53 degrees wide.
- line: from the caster toward the origin square, size / 5 squares long and one square wide.
The caster's own square is never part of a cone, line or Self cube.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from src.engine.position import FEET_PER_SQUARE, Position, chebyshev_distance

_CONE_COS_HALF_ANGLE = 0.86
"""cos of the cone's half-angle. A 5e cone is as wide as it is long (half-angle ~26.6 degrees,
cos 0.894); a little looser so the squares at the edge of the cone count."""


@dataclass(frozen=True)
class AreaSpec:
    shape: str  # "cube" | "sphere" | "cylinder" | "cone" | "line"
    size_feet: int
    from_caster: bool
    """True when the area springs from the caster (Burning Hands, Thunderwave): the origin
    square only gives its direction, and no range limit applies to it."""


def area_spec(spell: dict[str, object]) -> AreaSpec | None:
    """The shape of an SRD spell's area, or None if it has none."""
    area = spell.get("area_of_effect")
    if not isinstance(area, dict):
        return None
    shape = str(area.get("type", ""))
    size = area.get("size")
    if shape not in ("cube", "sphere", "cylinder", "cone", "line") or not isinstance(size, int):
        return None
    return AreaSpec(shape=shape, size_feet=size, from_caster=str(spell.get("range")) == "Self")


def _squares(size_feet: int) -> int:
    return max(1, size_feet // FEET_PER_SQUARE)


def area_cells(
    spec: AreaSpec,
    origin: Position,
    caster: Position,
    width: int,
    height: int,
    blocked: Callable[[int, int], bool] = lambda x, y: False,
) -> list[Position]:
    """The squares the area covers, inside the map and not blocked (walls stop an area)."""
    cells: set[tuple[int, int]] = set()
    n = _squares(spec.size_feet)
    cx, cy = caster.x, caster.y

    if spec.shape == "cube":
        if spec.from_caster:
            dx = (origin.x > cx) - (origin.x < cx)
            dy = (origin.y > cy) - (origin.y < cy)
            x0 = cx + 1 if dx > 0 else cx - n if dx < 0 else cx - (n - 1) // 2
            y0 = cy + 1 if dy > 0 else cy - n if dy < 0 else cy - (n - 1) // 2
        else:
            x0 = origin.x - (n - 1) // 2
            y0 = origin.y - (n - 1) // 2
        cells = {(x, y) for x in range(x0, x0 + n) for y in range(y0, y0 + n)}
    elif spec.shape in ("sphere", "cylinder"):
        r = n
        cells = {
            (x, y)
            for x in range(origin.x - r, origin.x + r + 1)
            for y in range(origin.y - r, origin.y + r + 1)
            if (x - origin.x) ** 2 + (y - origin.y) ** 2 <= r * r
        }
    elif spec.shape in ("cone", "line"):
        ax, ay = origin.x - cx, origin.y - cy
        length = math.hypot(ax, ay)
        if length == 0:
            return []
        if spec.shape == "cone":
            for x in range(cx - n, cx + n + 1):
                for y in range(cy - n, cy + n + 1):
                    vx, vy = x - cx, y - cy
                    distance = math.hypot(vx, vy)
                    if distance == 0 or distance > n + 0.5:
                        continue
                    if (vx * ax + vy * ay) / (distance * length) >= _CONE_COS_HALF_ANGLE:
                        cells.add((x, y))
        else:
            ux, uy = ax / length, ay / length
            for step in range(1, n + 1):
                cells.add((round(cx + ux * step), round(cy + uy * step)))

    # The caster's own square is never part of an area that springs from them; a cube, sphere
    # or cylinder placed elsewhere covers it if it happens to reach it.
    skip_caster = spec.shape in ("cone", "line") or spec.from_caster
    covered: list[Position] = []
    for x, y in sorted(cells, key=lambda c: (c[1], c[0])):
        if not (0 <= x < width and 0 <= y < height) or blocked(x, y):
            continue
        if skip_caster and (x, y) == (cx, cy):
            continue
        covered.append(Position(x=x, y=y))
    return covered


@dataclass(frozen=True)
class Occupant:
    id: str
    position: Position
    ally: bool
    """On the caster's side."""
    named: bool
    """The caster named this creature as a target."""


@dataclass(frozen=True)
class Placement:
    origin: Position
    cells: list[Position]
    caught: list[str]
    allies_caught: list[str]


_NAMED_SCORE = 100
_OTHER_ENEMY_SCORE = 10
_ALLY_PENALTY = 150
"""An unnamed ally in the area costs more than a named enemy is worth: the caster would rather
miss a target than burn a friend, and the player can still place it by hand."""


def caught_by(cells: Iterable[Position], occupants: Iterable[Occupant]) -> list[Occupant]:
    covered = {(c.x, c.y) for c in cells}
    return [o for o in occupants if (o.position.x, o.position.y) in covered]


def _score(caught: list[Occupant]) -> int:
    score = 0
    for o in caught:
        if o.named:
            score += _NAMED_SCORE
        elif o.ally:
            score -= _ALLY_PENALTY
        else:
            score += _OTHER_ENEMY_SCORE
    return score


def place_area(
    spec: AreaSpec,
    caster: Position,
    range_feet: int,
    width: int,
    height: int,
    occupants: list[Occupant],
    blocked: Callable[[int, int], bool] = lambda x, y: False,
    explicit_origin: Position | None = None,
) -> Placement | None:
    """Where to put the area. With `explicit_origin` (the player chose a square) that square is
    used as given - the caller has already checked it is in range. Otherwise every square within
    reach is tried and the best-scoring one wins: named creatures caught are worth the most,
    other enemies a little, an unnamed ally is a heavy penalty. Ties go to the origin closest to
    the named creatures, then the one closest to the caster, then reading order, so the result
    is deterministic. Returns None if no origin catches anyone at all."""
    if explicit_origin is not None:
        cells = area_cells(spec, explicit_origin, caster, width, height, blocked)
        caught = caught_by(cells, occupants)
        return Placement(
            origin=explicit_origin,
            cells=cells,
            caught=[o.id for o in caught],
            allies_caught=[o.id for o in caught if o.ally],
        )

    named = [o for o in occupants if o.named]
    best: tuple[tuple[int, int, int, int, int], Placement] | None = None
    seen_shapes: set[frozenset[tuple[int, int]]] = set()
    for y in range(height):
        for x in range(width):
            origin = Position(x=x, y=y)
            if not spec.from_caster and chebyshev_distance(caster, origin) * 5 > range_feet:
                continue
            if blocked(x, y) and not spec.from_caster:
                continue
            cells = area_cells(spec, origin, caster, width, height, blocked)
            key = frozenset((c.x, c.y) for c in cells)
            if not key or key in seen_shapes:
                continue
            seen_shapes.add(key)
            caught = caught_by(cells, occupants)
            # An area that misses everyone the caster named is not what they asked for, even
            # if it would catch someone else; with nobody named, any enemy will do.
            if named:
                if not any(o.named for o in caught):
                    continue
            elif not any(not o.ally for o in caught):
                continue
            score = _score(caught)
            to_named = sum(chebyshev_distance(origin, o.position) for o in named)
            rank = (-score, to_named, chebyshev_distance(caster, origin), y, x)
            if best is None or rank < best[0]:
                best = (
                    rank,
                    Placement(
                        origin=origin,
                        cells=cells,
                        caught=[o.id for o in caught],
                        allies_caught=[o.id for o in caught if o.ally],
                    ),
                )
    return best[1] if best else None
