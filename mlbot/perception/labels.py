"""Detector classes and YOLO-format label files (one .txt per image: `cls cx cy w h`, normalized)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from mlbot.config import ROOT

# Every character on screen is 'player'; which one is you gets decided later by matching
# against your minimap dot ('mm_me'), since look-alike characters can't be told apart reliably
# from a single frame.
CLASSES = ['player', 'mob', 'rope', 'portal', 'mm_me', 'mm_other']

DATASETS = ROOT.parent / 'datasets'
RAW = DATASETS / 'raw'
LABELS = DATASETS / 'labels'


@dataclass
class Box:
    cls: int
    x1: float  # pixels
    y1: float
    x2: float
    y2: float

    def contains(self, x: float, y: float) -> bool:
        return self.x1 <= x <= self.x2 and self.y1 <= y <= self.y2

    @property
    def area(self) -> float:
        return (self.x2 - self.x1) * (self.y2 - self.y1)


def label_path(image: Path, raw_root: Path | None = None, labels_root: Path | None = None) -> Path:
    return ((labels_root or LABELS) / image.relative_to(raw_root or RAW)).with_suffix('.txt')


def read(path: Path, width: int, height: int) -> list[Box]:
    boxes = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        c, cx, cy, w, h = line.split()
        cx, cy, w, h = float(cx) * width, float(cy) * height, float(w) * width, float(h) * height
        boxes.append(Box(int(c), cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2))
    return boxes


def write(path: Path, boxes: list[Box], width: int, height: int) -> None:
    """Writes BOXES (an empty list is a valid 'nothing here' label)."""

    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for b in boxes:
        x1, x2 = sorted((max(0.0, b.x1), min(float(width), b.x2)))
        y1, y2 = sorted((max(0.0, b.y1), min(float(height), b.y2)))
        lines.append(f'{b.cls} {(x1 + x2) / 2 / width:.6f} {(y1 + y2) / 2 / height:.6f} '
                     f'{(x2 - x1) / width:.6f} {(y2 - y1) / height:.6f}')
    path.write_text('\n'.join(lines) + ('\n' if lines else ''))
