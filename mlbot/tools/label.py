"""
Draw detector boxes on extracted frames.

    python -m mlbot.tools.label [--model models/detector.pt] [--review] [--scale 1.5]

Shows unlabeled frames from datasets/raw in a random (fixed) order, so labeling any number of
them gives a spread over every video. With --model, boxes are pre-filled by the detector and
you only fix them. --review goes through frames that already have labels instead.

    mouse   left-drag: draw box    right-click: delete box under cursor
    1-6     pick class             space / d: save, next      a: save, previous
    u       undo last box          c: clear boxes             x: skip frame (don't save)
    r       remove this frame's labels file (back to unlabeled), next
    h       hide/show boxes        q / Esc: save and quit
"""

from __future__ import annotations

import argparse
import random
from pathlib import Path

import cv2
import numpy as np

from mlbot.perception import labels
from mlbot.perception.labels import CLASSES, Box
from mlbot.tools.prelabel_minimap import prelabel_path

WINDOW = 'label'
COLORS = [(0, 255, 255), (0, 0, 255), (255, 160, 0), (255, 0, 255), (0, 255, 0), (0, 128, 255)]
MIN_BOX = 2  # px, smaller drags count as clicks


class Labeler:
    def __init__(self, images: list[Path], scale: float, model=None, conf: float = 0.35) -> None:
        self.images = images
        self.scale = scale
        self.model = model
        self.conf = conf
        self.cls = 0
        self.boxes: list[Box] = []
        self.image: np.ndarray | None = None
        self.drag: tuple[float, float] | None = None
        self.mouse = (0.0, 0.0)
        self.hidden = False
        self.prelabeled = False
        self.saved = 0

    # --- frame io -------------------------------------------------------------------------
    def load(self, path: Path) -> None:
        self.image = cv2.imread(str(path))
        h, w = self.image.shape[:2]
        lp = labels.label_path(path)
        self.prelabeled = False
        if lp.exists():
            self.boxes = labels.read(lp, w, h)
            return
        self.boxes = []
        pre = prelabel_path(path)
        if pre.exists():
            self.boxes = labels.read(pre, w, h)
            self.prelabeled = bool(self.boxes)
        if self.model is not None:
            # colour-found minimap dots beat the model's, keep the model for everything else
            dot_classes = {b.cls for b in self.boxes}
            self.boxes += [b for b in self.predict(self.image) if b.cls not in dot_classes]
            self.prelabeled = True

    def save(self, path: Path) -> None:
        h, w = self.image.shape[:2]
        labels.write(labels.label_path(path), self.boxes, w, h)
        self.saved += 1

    def predict(self, image: np.ndarray) -> list[Box]:
        result = self.model.predict(image, imgsz=1280, conf=self.conf, verbose=False)[0]
        return [Box(int(c), *map(float, xyxy))
                for c, xyxy in zip(result.boxes.cls.tolist(), result.boxes.xyxy.tolist())]

    # --- ui -------------------------------------------------------------------------------
    def on_mouse(self, event: int, x: int, y: int, flags: int, param) -> None:
        ix, iy = x / self.scale, y / self.scale
        self.mouse = (ix, iy)
        if event == cv2.EVENT_LBUTTONDOWN:
            self.drag = (ix, iy)
        elif event == cv2.EVENT_LBUTTONUP and self.drag:
            (x1, x2), (y1, y2) = sorted((self.drag[0], ix)), sorted((self.drag[1], iy))
            if x2 - x1 >= MIN_BOX and y2 - y1 >= MIN_BOX:
                self.boxes.append(Box(self.cls, x1, y1, x2, y2))
            self.drag = None
        elif event == cv2.EVENT_RBUTTONDOWN:
            hits = [b for b in self.boxes if b.contains(ix, iy)]
            if hits:
                self.boxes.remove(min(hits, key=lambda b: b.area))

    def render(self, index: int) -> np.ndarray:
        s = self.scale
        canvas = cv2.resize(self.image, None, fx=s, fy=s, interpolation=cv2.INTER_NEAREST)
        if not self.hidden:
            for b in self.boxes:
                p1, p2 = (int(b.x1 * s), int(b.y1 * s)), (int(b.x2 * s), int(b.y2 * s))
                cv2.rectangle(canvas, p1, p2, COLORS[b.cls], 1 if self.prelabeled else 2)
                cv2.putText(canvas, CLASSES[b.cls], (p1[0], p1[1] - 3), cv2.FONT_HERSHEY_SIMPLEX,
                            0.45, COLORS[b.cls], 1)
        mx, my = int(self.mouse[0] * s), int(self.mouse[1] * s)
        if self.drag:
            cv2.rectangle(canvas, (int(self.drag[0] * s), int(self.drag[1] * s)), (mx, my),
                          COLORS[self.cls], 1)
        cv2.line(canvas, (mx, 0), (mx, canvas.shape[0]), (200, 200, 200), 1)
        cv2.line(canvas, (0, my), (canvas.shape[1], my), (200, 200, 200), 1)
        cv2.putText(canvas, CLASSES[self.cls], (mx + 8, my - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 4)
        cv2.putText(canvas, CLASSES[self.cls], (mx + 8, my - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                    COLORS[self.cls], 2)

        status = (f'[{index + 1}/{len(self.images)}] {self.images[index].name}   '
                  f'class {self.cls + 1}: {CLASSES[self.cls]}   boxes {len(self.boxes)}'
                  f"{'  (model guess)' if self.prelabeled else ''}   saved {self.saved}")
        legend = '   '.join(f'{i + 1} {c}' for i, c in enumerate(CLASSES))
        for text, y in ((status, 22), (legend, canvas.shape[0] - 10)):
            cv2.putText(canvas, text, (8, y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 4)
            cv2.putText(canvas, text, (8, y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
        return canvas

    def run(self) -> None:
        cv2.namedWindow(WINDOW, cv2.WINDOW_AUTOSIZE)
        cv2.setMouseCallback(WINDOW, self.on_mouse)
        i = 0
        while 0 <= i < len(self.images):
            path = self.images[i]
            self.load(path)
            step = self.edit(i)
            if step is None:
                self.save(path)
                break
            if step == 'remove':
                labels.label_path(path).unlink(missing_ok=True)
                print(f'Removed labels of {path.name}, it is unlabeled again')
            elif step != 'skip':
                self.save(path)
            i = max(0, i - 1) if step == 'prev' else i + 1
        cv2.destroyAllWindows()
        print(f'Saved {self.saved} frames, {count_labeled()} labeled in total')

    def edit(self, index: int) -> str | None:
        """Handles input on the current frame until the user moves on; None = quit."""

        while True:
            cv2.imshow(WINDOW, self.render(index))
            key = cv2.waitKey(15) & 0xFF
            if cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
                return None
            if ord('1') <= key < ord('1') + len(CLASSES):
                self.cls = key - ord('1')
            elif key in (ord(' '), ord('d')):
                return 'next'
            elif key == ord('a'):
                return 'prev'
            elif key == ord('x'):
                return 'skip'
            elif key == ord('r'):
                return 'remove'
            elif key == ord('u') and self.boxes:
                self.boxes.pop()
            elif key == ord('c'):
                self.boxes = []
            elif key == ord('h'):
                self.hidden = not self.hidden
            elif key in (ord('q'), 27):
                return None


def all_images() -> list[Path]:
    return sorted(labels.RAW.glob('*/*.jpg'))


def count_labeled() -> int:
    return sum(1 for p in all_images() if labels.label_path(p).exists())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--model', type=Path, help='detector weights (.pt) to pre-fill boxes')
    parser.add_argument('--conf', type=float, default=0.35, help='min confidence for pre-filled boxes')
    parser.add_argument('--review', action='store_true', help='go through already-labeled frames')
    parser.add_argument('--scale', type=float, default=1.5, help='window zoom (small minimap dots)')
    parser.add_argument('--seed', type=int, default=0)
    args = parser.parse_args()

    images = [p for p in all_images() if labels.label_path(p).exists() == args.review]
    random.Random(args.seed).shuffle(images)
    if not images:
        raise SystemExit('Nothing to label: run mlbot.tools.yt_frames first' if not args.review
                         else 'No labeled frames yet')
    model = None
    if args.model:
        from ultralytics import YOLO
        model = YOLO(str(args.model))
    print(f'{len(images)} frames to go, {count_labeled()} labeled so far')
    Labeler(images, args.scale, model, args.conf).run()


if __name__ == '__main__':
    main()
