"""Identity and world-state logic on hand-made boxes: no model, OCR or footage needed."""

from __future__ import annotations

import cv2
import numpy as np

from mlbot.perception.detector import Detection
from mlbot.perception.identity import Identity, Me, name_score
from mlbot.perception.world import Velocity, WorldState


def box(cls: int, x: float, y: float, w: float = 30, h: float = 50) -> Detection:
    """A box with its feet at (x, y)."""

    return Detection(cls, 0.9, x - w / 2, y - h, x + w / 2, y)


def test_name_score_finds_name_inside_noisy_reading() -> None:
    assert name_score('MrBrillz', 'nkMrBrllz NIc') > 0.85     # a neighbour's tag ran into it
    assert name_score('DiXiMaN', 'DIXiMaN') == 1.0             # case and spacing don't matter
    assert name_score('DiXiMaN', 'Summit') < 0.5


def test_tag_template_picks_your_box() -> None:
    frame = np.full((300, 400, 3), 40, np.uint8)
    players = [box(0, 100, 150), box(0, 300, 150)]
    for (x, name) in ((100, 'ME'), (300, 'XO')):   # a light name tag under each character
        cv2.putText(frame, name, (x - 14, 165), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    ident = Identity('me')
    ident.template = cv2.cvtColor(frame[153:168, 84:118], cv2.COLOR_BGR2GRAY).copy()
    me = ident.update(frame, players, now=0.0)
    assert me.how == 'tag' and me.box is players[0]


def test_tracks_by_position_until_ambiguous() -> None:
    frame = np.zeros((300, 400, 3), np.uint8)
    ident = Identity(None)                      # no name: tags can't help
    ident.last = box(0, 100, 150)
    me = ident.update(frame, [box(0, 110, 150), box(0, 300, 150)], now=0.0)
    assert me.how == 'track' and me.box.feet == (110, 150)
    # another character right next to you: no guess
    assert ident.update(frame, [box(0, 115, 150), box(0, 105, 150)], now=0.1) is None


def test_velocity_is_slope_of_recent_positions() -> None:
    v = Velocity(window=0.25)
    out = None
    for i in range(10):
        t = i / 30
        out = v.update(t, (0.2 + 0.3 * t, 0.5))
    assert np.allclose(out, (0.3, 0.0), atol=1e-6)


def test_offsets_and_nearest() -> None:
    me = box(0, 200, 300)
    mobs = [box(1, 260, 300), box(1, 150, 260), box(1, 600, 300)]
    world = WorldState(0.0, (800, 600), me=Me(me, 'tag'), objects={'mob': mobs})
    assert world.offset(mobs[0]) == (60, 0)
    assert world.nearest('mob') is mobs[0]   # 60 px away, the one above is hypot(50, 40) = 64
    assert world.within('mob', dx=100, dy=10) == [mobs[0]]
    assert WorldState(0.0, (800, 600), objects={'mob': mobs}).nearest('mob') is None   # you unknown
