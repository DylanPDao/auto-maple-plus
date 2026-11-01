"""
Plays footage through the perception stack and draws what it sees: every detection, which
character is you (and how that was decided), your minimap position and speed, and per-step times.

    python -m mlbot.tools.overlay VIDEO_ID [--name CHARACTER] [--out overlay.mp4] [--start N]

VIDEO_ID is a folder of extracted frames under datasets/raw (python -m mlbot.tools.yt_frames).
Without --out it opens a window: space pauses, d steps while paused, q quits.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np

from mlbot.config import ROOT
from mlbot.perception import labels
from mlbot.perception.detector import Detector
from mlbot.perception.world import WorldBuilder, WorldState

WINDOW = 'overlay'
COLORS = {'player': (200, 200, 200), 'mob': (0, 0, 255), 'rope': (255, 160, 0), 'portal': (255, 0, 255),
          'loot': (255, 255, 255)}
ME_COLORS = {'tag': (0, 255, 0), 'ocr': (0, 255, 255), 'track': (0, 165, 255)}


def text(image: np.ndarray, s: str, org: tuple[int, int], color=(255, 255, 255), scale: float = 0.5) -> None:
    cv2.putText(image, s, org, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), 3)
    cv2.putText(image, s, org, cv2.FONT_HERSHEY_SIMPLEX, scale, color, 1)


def draw(frame: np.ndarray, world: WorldState, title: str, name: str | None) -> np.ndarray:
    view = frame.copy()
    for kind, dets in world.objects.items():
        for d in dets:
            if world.me is not None and d is world.me.box:
                continue
            cv2.rectangle(view, (int(d.x1), int(d.y1)), (int(d.x2), int(d.y2)), COLORS[kind], 1)
    if world.me is not None:
        b = world.me.box
        color = ME_COLORS[world.me.how]
        cv2.rectangle(view, (int(b.x1), int(b.y1)), (int(b.x2), int(b.y2)), color, 3)
        text(view, f'me ({world.me.how})', (int(b.x1), int(b.y1) - 6), color)
        target = world.nearest('mob')
        if target is not None:
            cv2.line(view, tuple(map(int, b.feet)), tuple(map(int, target.feet)), (0, 0, 255), 1)
    mm = world.minimap
    if mm is not None:
        cv2.rectangle(view, mm.tl, mm.br, (255, 0, 255), 1)
        if world.position is not None:
            pw, ph = mm.br[0] - mm.tl[0], mm.br[1] - mm.tl[1]
            p = (int(mm.tl[0] + world.position[0] * pw), int(mm.tl[1] + world.position[1] * ph))
            cv2.circle(view, p, 5, (0, 255, 0), 1)
            if world.velocity is not None:   # where you'll be in half a second
                q = (int(p[0] + world.velocity[0] * pw / 2), int(p[1] + world.velocity[1] * ph / 2))
                cv2.arrowedLine(view, p, q, (0, 255, 0), 1, tipLength=0.3)

    lines = [title, f'you: {world.me.how if world.me else "-"}   name: {name or "unknown"}',
             'pos: ' + ('-' if world.position is None else '%.3f, %.3f' % world.position)
             + '   vel: ' + ('-' if world.velocity is None else '%+.3f, %+.3f /s' % world.velocity),
             '  '.join(f'{k} {len(v)}' for k, v in world.objects.items()),
             '  '.join(f'{k} {v:.1f}ms' for k, v in world.timing.items())]
    for j, line in enumerate(lines):
        text(view, line, (10, view.shape[0] - 12 - 20 * (len(lines) - 1 - j)), scale=0.55)
    return view


def frames(video: str, start: int):
    paths = sorted((labels.RAW / video).glob('*.jpg'))[start:]
    if not paths:
        raise SystemExit(f'No frames in {labels.RAW / video}')
    for path in paths:
        t = int(path.stem.rsplit('_', 1)[1]) / 1000   # frame names end in their time in ms
        yield path, t, cv2.imread(str(path))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('video', help='frame folder under datasets/raw')
    parser.add_argument('--name', help="your character's name (default: read off the status bar)")
    parser.add_argument('--model', type=Path, default=ROOT.parent / 'models' / 'detector.onnx')
    parser.add_argument('--out', type=Path, help='write an mp4 instead of opening a window')
    parser.add_argument('--fps', type=float, default=4, help='playback rate of --out')
    parser.add_argument('--start', type=int, default=0, help='frame index to start at')
    parser.add_argument('--count', type=int, default=0, help='frames to play (0 = all)')
    args = parser.parse_args()

    builder = WorldBuilder(Detector(args.model), args.name)
    writer = None
    paused = False
    for i, (path, t, frame) in enumerate(frames(args.video, args.start)):
        if args.count and i >= args.count:
            break
        world = builder.update(frame, t)
        view = draw(frame, world, path.name, builder.identity.name)
        if args.out:
            if writer is None:
                writer = cv2.VideoWriter(str(args.out), cv2.VideoWriter_fourcc(*'mp4v'), args.fps,
                                         (view.shape[1], view.shape[0]))
            writer.write(view)
            continue
        cv2.imshow(WINDOW, view)
        while True:
            key = cv2.waitKey(0 if paused else 1) & 0xFF
            if key == ord(' '):
                paused = not paused
            if key in (ord('q'), 27):
                cv2.destroyAllWindows()
                return
            if not paused or key in (ord('d'), ord(' ')):
                break
    if writer is not None:
        writer.release()
        print(f'[overlay] wrote {args.out}')
    cv2.destroyAllWindows()


if __name__ == '__main__':
    main()
