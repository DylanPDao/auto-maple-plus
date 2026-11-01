"""Routing on hand-made maps (100 x 100 minimap pixels, so 0.01 = 1 px)."""

from __future__ import annotations

from mlbot.nav.planner import NavMap


def plat(y: float, x1: float, x2: float) -> dict:
    return {'y': y, 'x1': x1, 'x2': x2, 'samples': 10}


def kinds(steps) -> list[str]:
    return [s.kind for s in steps]


def test_walk_along_one_platform() -> None:
    nav = NavMap((100, 100), [plat(0.9, 0.1, 0.9)], [])
    [step] = nav.plan((0.2, 0.9), (0.7, 0.88))
    assert step.kind == 'walk' and step.start == (0.2, 0.9) and step.end == (0.7, 0.9)


def test_climbs_rope_up_and_down() -> None:
    nav = NavMap((100, 100), [plat(0.9, 0.0, 1.0), plat(0.6, 0.3, 0.6)],
                 [{'x': 0.5, 'y1': 0.6, 'y2': 0.9, 'moves': 3}])   # no kind: a climb
    up = nav.plan((0.1, 0.9), (0.35, 0.6))
    assert kinds(up) == ['walk', 'climb', 'walk']
    assert up[1].start == (0.5, 0.9) and up[1].end == (0.5, 0.6)
    # down: a down-jump onto the floor beats walking to the rope
    assert kinds(nav.plan((0.35, 0.6), (0.9, 0.9))) == ['downjump', 'walk']
    # too high to down-jump safely: back down the rope
    high = NavMap((100, 100), [plat(0.9, 0.0, 1.0), plat(0.4, 0.3, 0.6)],
                  [{'x': 0.5, 'y1': 0.4, 'y2': 0.9, 'moves': 3}])
    assert kinds(high.plan((0.35, 0.4), (0.9, 0.9))) == ['walk', 'climb', 'walk']


def test_one_way_links() -> None:
    plats = [plat(0.9, 0.0, 1.0), plat(0.2, 0.0, 0.2)]   # too high to jump or down-jump
    drop_only = NavMap((100, 100), plats, [{'x': 0.1, 'y1': 0.2, 'y2': 0.9, 'moves': 1, 'kind': 'drop'}])
    assert drop_only.plan((0.5, 0.9), (0.1, 0.2)) is None
    assert drop_only.plan((0.1, 0.2), (0.5, 0.9)) is not None
    jump_only = NavMap((100, 100), plats, [{'x': 0.1, 'y1': 0.2, 'y2': 0.9, 'moves': 1, 'kind': 'jump'}])
    assert kinds(jump_only.plan((0.5, 0.9), (0.1, 0.2))) == ['walk', 'jump']


def test_assumed_jumps_across_gaps_and_up_steps() -> None:
    nav = NavMap((100, 100), [plat(0.5, 0.0, 0.3), plat(0.5, 0.36, 0.7), plat(0.47, 0.5, 0.6)], [])
    across = nav.plan((0.1, 0.5), (0.4, 0.5))
    assert kinds(across) == ['walk', 'jump', 'walk'] and across[1].start == (0.3, 0.5)
    assert 'jump' in kinds(nav.plan((0.4, 0.5), (0.55, 0.47)))   # 3 px up, from underneath
    # too wide a gap: no way
    assert NavMap((100, 100), [plat(0.5, 0.0, 0.3), plat(0.5, 0.5, 0.7)], []).plan((0.1, 0.5), (0.6, 0.5)) is None


def test_off_map_start() -> None:
    nav = NavMap((100, 100), [plat(0.5, 0.4, 0.6)], [])
    assert nav.plan((0.1, 0.5), (0.5, 0.5)) is None
