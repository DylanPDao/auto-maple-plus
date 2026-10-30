"""
Pre-labels minimap dots by colour (yellow = you -> mm_me, red = other players -> mm_other) for every
extracted frame, into datasets/prelabels/. The labeler starts unlabeled frames from these, so you
only box what's on screen.

    python -m mlbot.tools.prelabel_minimap [--show FRAME]
"""

from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np

from mlbot.perception import labels
from mlbot.perception.labels import CLASSES, Box

PRELABELS = labels.DATASETS / 'prelabels'
MM_ME, MM_OTHER = CLASSES.index('mm_me'), CLASSES.index('mm_other')
DOT_PAD = 2        # px around a dot's coloured core, covers its white outline
MIN_DOT_AREA = 6   # px of colour; less is compression noise
SEARCH = (0.0, 0.0, 0.5, 0.5)  # x, y, w, h fractions: minimaps sit in a corner


def minimap_area(image: np.ndarray) -> tuple[int, int, int, int] | None:
    """(x, y, w, h) of the minimap's dark navy map area: the biggest navy blob in the search corner."""

    h, w = image.shape[:2]
    sx, sy = int(SEARCH[0] * w), int(SEARCH[1] * h)
    roi = image[sy:sy + int(SEARCH[3] * h), sx:sx + int(SEARCH[2] * w)]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    navy = cv2.inRange(hsv, (105, 120, 30), (130, 255, 120))
    navy = cv2.morphologyEx(navy, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))  # dots punch holes
    n, _, stats, _ = cv2.connectedComponentsWithStats(navy)
    if n < 2:
        return None
    i = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    x, y, bw, bh, area = stats[i]
    if area < 2000 or bw < 40 or bh < 30:
        return None
    return sx + x, sy + y, bw, bh


def find_dots(image: np.ndarray) -> list[Box]:
    area = minimap_area(image)
    if area is None:
        return []
    x0, y0, w, h = area
    # dots on the map's edge stick out of the navy area a little
    x0, y0 = max(0, x0 - 6), max(0, y0 - 6)
    roi = image[y0:y0 + h + 12, x0:x0 + w + 12]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    masks = {
        MM_OTHER: cv2.inRange(hsv, (0, 150, 90), (6, 255, 255)) | cv2.inRange(hsv, (170, 150, 90), (180, 255, 255)),
        MM_ME: cv2.inRange(hsv, (20, 90, 200), (35, 255, 255)),
    }
    boxes = []
    for cls, mask in masks.items():
        n, _, stats, _ = cv2.connectedComponentsWithStats(mask)
        for x, y, bw, bh, a in stats[1:]:
            if a >= MIN_DOT_AREA:
                boxes.append(Box(cls, x0 + x - DOT_PAD, y0 + y - DOT_PAD,
                                 x0 + x + bw + DOT_PAD, y0 + y + bh + DOT_PAD))
    return boxes


def prelabel_path(image: Path) -> Path:
    return labels.label_path(image, labels_root=PRELABELS)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--show', type=Path, help='draw the dots found on one frame to dots.png and stop')
    args = parser.parse_args()
    if args.show:
        image = cv2.imread(str(args.show))
        for b in find_dots(image):
            cv2.rectangle(image, (int(b.x1), int(b.y1)), (int(b.x2), int(b.y2)),
                          (0, 255, 0) if b.cls == MM_ME else (255, 255, 0), 1)
        cv2.imwrite('dots.png', image)
        return
    images = sorted(labels.RAW.glob('*/*.jpg'))
    total = no_map = 0
    for path in images:
        image = cv2.imread(str(path))
        boxes = find_dots(image)
        no_map += not boxes
        total += len(boxes)
        h, w = image.shape[:2]
        labels.write(prelabel_path(path), boxes, w, h)
    print(f'{len(images)} frames: {total} dots, {no_map} frames with none found')


if __name__ == '__main__':
    main()
