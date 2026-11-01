"""
One snapshot of the game per frame: you (on screen and on the minimap), how fast you're moving,
and the mobs, ropes, portals, loot and other characters around you.

    builder = WorldBuilder(Detector('models/detector.onnx'), name='MyCharacter')
    world = builder.update(frame)          # every captured frame
    world.nearest('mob')                   # what the policy and the planner read

Screen boxes are in frame pixels. Your map position is 0..1 of the minimap picture, the same
units as the platforms in resources/maps/<map>/map.json.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

import numpy as np

from mlbot.maps.minimap import MeTracker, Minimap, MinimapLocator
from mlbot.perception.detector import Detection, Detector
from mlbot.perception.identity import Identity, Me

VELOCITY_WINDOW = 0.25   # s of minimap positions a velocity is measured over
SCREEN_KINDS = ('player', 'mob', 'rope', 'portal', 'loot')   # the minimap dots come from the minimap


@dataclass
class WorldState:
    t: float
    size: tuple[int, int]                          # frame w, h
    me: Me | None = None                           # your box on screen, and how it was found
    position: tuple[float, float] | None = None    # your minimap dot, 0..1 of the picture
    velocity: tuple[float, float] | None = None    # minimap picture widths/heights per second
    minimap: Minimap | None = None
    objects: dict[str, list[Detection]] = field(default_factory=dict)
    others: list[Detection] = field(default_factory=list)   # 'player' boxes that aren't you
    timing: dict[str, float] = field(default_factory=dict)  # ms per step

    def of(self, kind: str) -> list[Detection]:
        return self.objects.get(kind, [])

    def offset(self, d: Detection) -> tuple[float, float] | None:
        """D's feet relative to yours, in frame pixels (x right, y down)."""

        if self.me is None:
            return None
        (mx, my), (x, y) = self.me.box.feet, d.feet
        return x - mx, y - my

    def nearest(self, kind: str) -> Detection | None:
        found = [d for d in self.of(kind) if self.offset(d) is not None]
        return min(found, key=lambda d: np.hypot(*self.offset(d)), default=None)

    def within(self, kind: str, dx: float, dy: float) -> list[Detection]:
        """KIND within DX px to either side and DY px above or below your feet."""

        return [d for d in self.of(kind)
                if (o := self.offset(d)) is not None and abs(o[0]) <= dx and abs(o[1]) <= dy]


class Velocity:
    """Your minimap speed: the straight-line fit of the last VELOCITY_WINDOW of positions."""

    def __init__(self, window: float = VELOCITY_WINDOW) -> None:
        self.window = window
        self.samples: deque[tuple[float, float, float]] = deque()

    def update(self, t: float, pos: tuple[float, float] | None) -> tuple[float, float] | None:
        if pos is not None:
            self.samples.append((t, *pos))
        while self.samples and self.samples[0][0] < t - self.window:
            self.samples.popleft()
        if len(self.samples) < 3 or self.samples[-1][0] - self.samples[0][0] < self.window / 2:
            return None
        ts, xs, ys = np.array(self.samples).T
        ts = ts - ts.mean()
        denom = float(ts @ ts)
        return float(ts @ (xs - xs.mean()) / denom), float(ts @ (ys - ys.mean()) / denom)

    def reset(self) -> None:
        self.samples.clear()


class WorldBuilder:
    def __init__(self, detector: Detector, name: str | None = None) -> None:
        self.detector = detector
        self.locator = MinimapLocator()
        self.tracker = MeTracker()
        self.identity = Identity(name)
        self.velocity = Velocity()

    def update(self, frame: np.ndarray, t: float | None = None) -> WorldState:
        t = time.monotonic() if t is None else t
        h, w = frame.shape[:2]
        world = WorldState(t, (w, h))

        t0 = time.perf_counter()
        detections = self.detector.detect(frame)
        world.objects = {k: [d for d in detections if d.name == k] for k in SCREEN_KINDS}
        t1 = time.perf_counter()

        world.minimap = self.locator.locate(frame)
        if world.minimap is not None:
            world.position = self.tracker.update(world.minimap.picture)
        else:
            self.velocity.reset()   # minimap gone (map change, loading screen): old positions are stale
        world.velocity = self.velocity.update(t, world.position)
        t2 = time.perf_counter()

        if self.identity.name is None:
            self.identity.learn_name(frame, t)
        players = world.of('player')
        world.me = self.identity.update(frame, players, t)
        world.others = [p for p in players if world.me is None or p is not world.me.box]
        t3 = time.perf_counter()

        world.timing = {'detect': (t1 - t0) * 1000, 'minimap': (t2 - t1) * 1000,
                        'identify': (t3 - t2) * 1000}
        return world
