"""
Routes between two spots on a learned map (resources/maps/<map>/map.json, from mlbot.maps.learn):
which platforms to walk along, and where to climb, jump or drop between them.

    nav = NavMap.load('resources/maps/henesys_hunting_ground_i')
    for step in nav.plan(world.position, target):
        ...   # Step('walk', ...), Step('climb', ...), ...

Positions are the minimap's 0..1 (x right, y down), as in map.json. Distances are worked out in
minimap pixels, so a map's aspect ratio doesn't skew them.

Moves come from two places. Learned ones are the map's vertical links: 'climb' (rope or ladder,
both ways), 'jump' (up only) and 'drop' (down only); YouTube-learned links have no kind and count
as climbs. Footage sampled seconds apart misses most moves, so a few are also assumed from the
platform layout: a down-jump onto the platform right below, and a jump onto a platform a little
higher or across a small gap.
"""

from __future__ import annotations

import heapq
import json
from dataclasses import dataclass
from pathlib import Path

# minimap pixels; a minimap pixel is very roughly 10-20 px of game
X_TOL = 2.0          # how far past a platform's seen ends it still counts as that platform
Y_TOL = 2.5          # height difference that still counts as standing on a platform
LINK_TOL = 3.0       # a link's end and a platform this close in height are connected
JUMP_UP = 5.0        # highest platform reachable with a plain jump
JUMP_GAP = 8.0       # widest gap a running jump crosses
MAX_DOWNJUMP = 40.0  # deepest a down-jump is trusted to land on the platform below

# cost per minimap pixel, plus a fixed cost per move for the time it takes to set up
WALK, CLIMB, FALL = 1.0, 2.0, 0.5
SETUP = {'climb': 4.0, 'jump': 3.0, 'drop': 2.0, 'downjump': 3.0}


@dataclass(frozen=True)
class Platform:
    y: float     # px
    x1: float
    x2: float

    def holds(self, x: float, tol: float = X_TOL) -> bool:
        return self.x1 - tol <= x <= self.x2 + tol

    def clamp(self, x: float) -> float:
        return min(max(x, self.x1), self.x2)


@dataclass(frozen=True)
class Step:
    kind: str                     # walk, climb, jump, drop, downjump
    start: tuple[float, float]    # 0..1 minimap coordinates
    end: tuple[float, float]


@dataclass(frozen=True)
class _Edge:
    kind: str
    a: int                        # platform indices
    b: int
    ax: float                     # where it leaves a and lands on b, px
    bx: float
    cost: float
    span: tuple[float, float] | None = None   # straight up/down moves: take off anywhere in here

    def takeoff(self, x: float) -> tuple[float, float]:
        """Where to leave a and land on b, coming from X on a."""

        if self.span is None:
            return self.ax, self.bx
        x = min(max(x, self.span[0]), self.span[1])
        return x, x


class NavMap:
    def __init__(self, size: tuple[int, int], platforms: list[dict], links: list[dict]) -> None:
        self.w, self.h = size
        self.platforms = [Platform(p['y'] * self.h, p['x1'] * self.w, p['x2'] * self.w) for p in platforms]
        self.edges: list[_Edge] = []
        self._learned(links)
        self._assumed()

    @classmethod
    def load(cls, folder: Path | str) -> NavMap:
        data = json.loads((Path(folder) / 'map.json').read_text(encoding='utf-8'))
        return cls(tuple(data['picture_size']), data['platforms'], data['vertical_links'])

    # --- graph ----------------------------------------------------------------------------------
    def _at(self, x: float, y: float, tol: float) -> int | None:
        """The platform at height Y (within TOL) that reaches X."""

        found = [(abs(p.y - y), i) for i, p in enumerate(self.platforms) if p.holds(x) and abs(p.y - y) <= tol]
        return min(found)[1] if found else None

    def below(self, x: float, y: float) -> int | None:
        """The first platform under (X, Y), or at its height."""

        found = [(p.y, i) for i, p in enumerate(self.platforms) if p.holds(x) and p.y >= y - Y_TOL]
        return min(found)[1] if found else None

    def _learned(self, links: list[dict]) -> None:
        for link in links:
            x, top_y, bottom_y = link['x'] * self.w, link['y1'] * self.h, link['y2'] * self.h
            top, bottom = self._at(x, top_y, LINK_TOL), self._at(x, bottom_y, LINK_TOL)
            if top is None or bottom is None or top == bottom:
                continue   # one end is mid-air or on an unseen platform: no use for routing
            kind = link.get('kind', 'climb')
            dy = self.platforms[bottom].y - self.platforms[top].y
            if kind in ('climb', 'jump'):
                self._edge(kind, bottom, top, x, x, CLIMB * dy)
            if kind == 'climb':
                self._edge('climb', top, bottom, x, x, CLIMB * dy)
            if kind == 'drop':
                self._edge('drop', top, bottom, x, x, FALL * dy)

    def _assumed(self) -> None:
        ps = self.platforms
        for i, a in enumerate(ps):
            for j, b in enumerate(ps):
                if i == j:
                    continue
                lo, hi = max(a.x1, b.x1), min(a.x2, b.x2)
                dy = b.y - a.y   # > 0: b is lower
                if lo <= hi and Y_TOL < dy <= MAX_DOWNJUMP and self._first_below(a, (lo + hi) / 2) == j:
                    self._edge('downjump', i, j, lo, lo, FALL * dy, (lo, hi))   # anywhere they overlap
                elif lo <= hi and -JUMP_UP <= dy < -Y_TOL:
                    self._edge('jump', i, j, lo, lo, CLIMB * -dy, (lo, hi))     # up through b from underneath
                elif hi < lo <= hi + JUMP_GAP and -JUMP_UP <= dy <= MAX_DOWNJUMP:
                    # across a gap from a's end nearest b: a jump, or walking off onto a lower one
                    ax, bx = (a.x2, b.x1) if a.x2 < b.x1 else (a.x1, b.x2)
                    kind = 'drop' if dy > Y_TOL else 'jump'
                    self._edge(kind, i, j, ax, bx, WALK * abs(bx - ax) + (FALL * dy if dy > 0 else CLIMB * -dy))

    def _first_below(self, a: Platform, x: float) -> int | None:
        found = [(p.y, k) for k, p in enumerate(self.platforms) if p.holds(x, 0) and p.y > a.y + Y_TOL]
        return min(found)[1] if found else None

    def _edge(self, kind: str, a: int, b: int, ax: float, bx: float, cost: float,
              span: tuple[float, float] | None = None) -> None:
        pa, pb = self.platforms[a], self.platforms[b]
        self.edges.append(_Edge(kind, a, b, pa.clamp(ax), pb.clamp(bx), cost + SETUP[kind], span))

    # --- routing --------------------------------------------------------------------------------
    def plan(self, start: tuple[float, float], goal: tuple[float, float]) -> list[Step] | None:
        """Steps from START to GOAL (0..1 minimap coordinates), or None if there's no known way."""

        sx, sy, gx, gy = start[0] * self.w, start[1] * self.h, goal[0] * self.w, goal[1] * self.h
        a, b = self.below(sx, sy), self.below(gx, gy)
        if a is None or b is None:
            return None
        # A* over (platform, x) spots: the start, the goal and every move's take-off point
        out: dict[int, list[_Edge]] = {}
        for e in self.edges:
            out.setdefault(e.a, []).append(e)
        start_node = (a, self.platforms[a].clamp(sx))
        goal_node = (b, self.platforms[b].clamp(gx))

        def h(node: tuple[int, float]) -> float:   # never more than the real cost: walking is cheapest
            return WALK * abs(node[1] - goal_node[1])

        best = {start_node: 0.0}
        came: dict[tuple[int, float], tuple[tuple[int, float], _Edge | None, float]] = {}
        queue = [(h(start_node), 0.0, start_node)]
        while queue:
            _, cost, node = heapq.heappop(queue)
            if node == goal_node:
                return self._steps(came, node)
            if cost > best.get(node, float('inf')):
                continue
            p, x = node
            moves: list[tuple[tuple[int, float], float, _Edge | None, float]] = []
            if p == goal_node[0]:
                moves.append((goal_node, WALK * abs(goal_node[1] - x), None, x))
            for e in out.get(p, []):
                ax, bx = e.takeoff(x)
                moves.append(((e.b, bx), WALK * abs(ax - x) + e.cost, e, ax))
            for nxt, step_cost, edge, ax in moves:
                c = cost + step_cost
                if c < best.get(nxt, float('inf')):
                    best[nxt] = c
                    came[nxt] = (node, edge, ax)
                    heapq.heappush(queue, (c + h(nxt), c, nxt))
        return None

    def _steps(self, came: dict, node: tuple[int, float]) -> list[Step]:
        steps: list[Step] = []
        while node in came:
            prev, edge, ax = came[node]
            (pa, xa), (pb, xb) = prev, node
            if edge is None:
                steps.append(self._step('walk', xa, self.platforms[pa].y, xb, self.platforms[pb].y))
            else:
                steps.append(self._step(edge.kind, ax, self.platforms[pa].y, xb, self.platforms[pb].y))
                steps.append(self._step('walk', xa, self.platforms[pa].y, ax, self.platforms[pa].y))
            node = prev
        return [s for s in reversed(steps) if s.kind != 'walk' or s.start != s.end]

    def _step(self, kind: str, x1: float, y1: float, x2: float, y2: float) -> Step:
        return Step(kind, (x1 / self.w, y1 / self.h), (x2 / self.w, y2 / self.h))


EDGE_COLORS = {'climb': (0, 165, 255), 'jump': (255, 255, 0), 'drop': (255, 0, 255), 'downjump': (180, 0, 255)}


def draw(folder: Path, nav: NavMap, steps: list[Step] | None, zoom: int = 5):
    """The map's minimap with its platforms, every move between them, and STEPS on top."""

    import cv2
    import numpy as np

    picture = cv2.imread(str(folder / 'minimap.png'))
    view = cv2.resize(picture, (nav.w * zoom, nav.h * zoom), interpolation=cv2.INTER_NEAREST)
    view = cv2.addWeighted(view, 0.4, np.zeros_like(view), 0.6, 0)
    px = lambda x, y: (round(x * zoom), round(y * zoom))
    for e in nav.edges:
        a, b = nav.platforms[e.a], nav.platforms[e.b]
        cv2.line(view, px(e.ax, a.y), px(e.bx, b.y), EDGE_COLORS[e.kind], 1)
    for p in nav.platforms:
        cv2.line(view, px(p.x1, p.y), px(p.x2, p.y), (80, 255, 80), 2)
    for s in steps or []:
        cv2.arrowedLine(view, px(s.start[0] * nav.w, s.start[1] * nav.h), px(s.end[0] * nav.w, s.end[1] * nav.h),
                        (0, 0, 255), 3, tipLength=0.15)
    return view


def main() -> None:
    import argparse

    import cv2

    parser = argparse.ArgumentParser(description='Draws a learned map\'s moves and a route: '
                                                 'python -m mlbot.nav.planner resources/maps/<map> --from 0.1,0.9 --to 0.6,0.3')
    parser.add_argument('map', type=Path, help='folder with map.json and minimap.png')
    parser.add_argument('--from', dest='start', help='x,y in 0..1 of the minimap')
    parser.add_argument('--to', dest='goal')
    parser.add_argument('--out', type=Path, default=Path('route.png'))
    args = parser.parse_args()

    nav = NavMap.load(args.map)
    steps = None
    if args.start and args.goal:
        point = lambda s: tuple(float(v) for v in s.split(','))
        steps = nav.plan(point(args.start), point(args.goal))
        for s in steps or []:
            print(f'{s.kind:9s} ({s.start[0]:.3f}, {s.start[1]:.3f}) -> ({s.end[0]:.3f}, {s.end[1]:.3f})')
        if steps is None:
            print('no known way')
    cv2.imwrite(str(args.out), draw(args.map, nav, steps))
    print(f'wrote {args.out}')


if __name__ == '__main__':
    main()
