"""
Which of the 'player' boxes is you, by your name tag.

Busy maps show several characters, and the detector calls every one of them 'player'. Your name
is read once off the status bar (or set in config), then found again in the name tags under the
characters with OCR. Once a tag reads as your name, it's kept as a picture, and later frames just
look for that picture under each character: a template match per box, a fraction of a millisecond,
where OCR costs 10-50 ms per tag.

When your tag is covered (another character, a skill effect), you're the box nearest to where you
were, if one is close enough.
"""

from __future__ import annotations

import difflib
import re
import time
from dataclasses import dataclass

import cv2
import numpy as np

from mlbot.perception.detector import Detection

TAG_MATCH = 0.75      # template score that counts as "your tag is here"
NAME_MATCH = 0.75     # fuzzy ratio between an OCR reading and your name
OCR_EVERY = 1.0       # s between OCR attempts while you're not found by tag
NAME_EVERY = 5.0      # s between attempts to read your name off the status bar
MAX_STEP = 1.5        # box heights you can move between two frames and still be tracked
TRACK_FRAMES = 30     # frames to keep tracking by position without seeing your tag

_ocr = None


def _engine():
    global _ocr
    if _ocr is None:
        from rapidocr_onnxruntime import RapidOCR
        _ocr = RapidOCR()
    return _ocr


def normalize(text: str) -> str:
    return re.sub(r'[^a-z0-9]', '', text.lower())


def name_score(name: str, text: str) -> float:
    """How well TEXT contains NAME: the best fuzzy ratio over windows of TEXT as long as NAME."""

    name, text = normalize(name), normalize(text)
    if not name or not text:
        return 0.0
    if len(text) <= len(name):
        return difflib.SequenceMatcher(None, name, text).ratio()
    return max(difflib.SequenceMatcher(None, name, text[i:i + len(name)]).ratio()
               for i in range(len(text) - len(name) + 1))


def read_status_name(frame: np.ndarray) -> str | None:
    """
    Your character's name from the status bar at the bottom: the lower of the two lines (job, then
    name) just right of "LV. nn". Takes a second or two on the CPU, so it's done once.
    """

    h = frame.shape[0]
    y0 = int(h * 0.93)
    big = cv2.resize(frame[y0:], None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    result, _ = _engine()(big)
    items = []
    for box, text, _score in result or []:
        xs, ys = [p[0] / 2 for p in box], [p[1] / 2 for p in box]
        items.append((text.strip(), min(xs), max(xs), (min(ys) + max(ys)) / 2))
    level = next((i for i in items if normalize(i[0]) in ('lv', 'iv', 'lv1')), None)
    if level is None:
        return None
    reach = 80 * h / 720   # the job and name lines start this close after the level number
    beside = [i for i in items if level[2] < i[1] < level[2] + reach
              and sum(c.isalpha() for c in i[0]) >= 2]
    if not beside:
        return None
    return max(beside, key=lambda i: i[3])[0]


def tag_region(frame: np.ndarray, box: Detection) -> tuple[np.ndarray, int, int]:
    """Where a character's name tag is: just under its feet. Returns the crop and its corner."""

    h, w = frame.shape[:2]
    bh = box.y2 - box.y1
    cx = (box.x1 + box.x2) / 2
    x0, x1 = max(0, int(cx - 1.4 * bh)), min(w, int(cx + 1.4 * bh))
    y0, y1 = max(0, int(box.y2 - 0.1 * bh)), min(h, int(box.y2 + 0.45 * bh))
    return frame[y0:y1, x0:x1], x0, y0


def ocr_tag(frame: np.ndarray, box: Detection) -> tuple[str, tuple[int, int, int, int]] | None:
    """The text line of a character's name tag and its rectangle (x, y, w, h) in frame pixels."""

    crop, x0, y0 = tag_region(frame, box)
    if crop.size == 0:
        return None
    big = cv2.resize(crop, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    result, _ = _engine()(big)
    if not result:
        return None
    cx = (box.x1 + box.x2) / 2 - x0
    # the name is the top line, centred under the character; a guild name may sit below it
    lines = []
    for quad, text, _score in result:
        xs, ys = [p[0] / 2 for p in quad], [p[1] / 2 for p in quad]
        if min(xs) - 4 <= cx <= max(xs) + 4:
            lines.append((min(ys), text, (int(min(xs)), int(min(ys)), int(max(xs) - min(xs)), int(max(ys) - min(ys)))))
    if not lines:
        return None
    _, text, (x, y, w, h) = min(lines)
    return text, (x0 + x, y0 + y, w, h)


def _gray(image: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


@dataclass
class Me:
    box: Detection
    how: str        # 'tag': your tag's picture matched, 'ocr': your name was read, 'track': by position


class Identity:
    def __init__(self, name: str | None = None) -> None:
        self.name = name
        self.template: np.ndarray | None = None   # grey picture of your name tag
        self.last: Detection | None = None
        self.unseen = 0                            # frames since your tag was last seen
        self.next_ocr = 0.0
        self.next_name = 0.0

    def learn_name(self, frame: np.ndarray, now: float | None = None) -> str | None:
        """Reads your name off the status bar, at most every NAME_EVERY s (it's slow) until it works."""

        now = time.monotonic() if now is None else now
        if self.name is None and now >= self.next_name:
            self.next_name = now + NAME_EVERY
            self.name = read_status_name(frame)
        return self.name

    def update(self, frame: np.ndarray, players: list[Detection], now: float | None = None) -> Me | None:
        now = time.monotonic() if now is None else now
        if not players:
            self.unseen += 1
            return None
        me = self._by_tag(frame, players)
        if me is None and self.name and now >= self.next_ocr:
            self.next_ocr = now + OCR_EVERY
            me = self._by_ocr(frame, players)
        if me is not None:
            self.unseen = 0
        else:
            self.unseen += 1
            me = self._by_track(players)
        if me is not None:
            self.last = me.box
        return me

    def _by_tag(self, frame: np.ndarray, players: list[Detection]) -> Me | None:
        if self.template is None:
            return None
        th, tw = self.template.shape
        best, best_score = None, TAG_MATCH
        for box in players:
            crop, _, _ = tag_region(frame, box)
            if crop.shape[0] < th or crop.shape[1] < tw:
                continue
            score = float(cv2.minMaxLoc(cv2.matchTemplate(_gray(crop), self.template, cv2.TM_CCOEFF_NORMED))[1])
            if score > best_score:
                best, best_score = box, score
        return Me(best, 'tag') if best is not None else None

    def _by_ocr(self, frame: np.ndarray, players: list[Detection]) -> Me | None:
        # nearest to where you were first: usually the first one read is you
        if self.last is not None:
            lx, ly = self.last.center
            players = sorted(players, key=lambda b: np.hypot(b.center[0] - lx, b.center[1] - ly))
        for box in players:
            found = ocr_tag(frame, box)
            if found is None or name_score(self.name, found[0]) < NAME_MATCH:
                continue
            x, y, w, h = found[1]
            if w >= 8 and h >= 6:
                self.template = _gray(frame[y:y + h, x:x + w]).copy()
            return Me(box, 'ocr')
        return None

    def _by_track(self, players: list[Detection]) -> Me | None:
        if self.last is None or self.unseen > TRACK_FRAMES:
            return None
        lx, ly = self.last.center
        step = MAX_STEP * (self.last.y2 - self.last.y1)
        dist = sorted((np.hypot(b.center[0] - lx, b.center[1] - ly), i) for i, b in enumerate(players))
        if dist[0][0] > step:
            return None
        # two characters about as close: can't tell which one is you
        if len(dist) > 1 and dist[1][0] < 1.5 * dist[0][0] + 0.2 * step:
            return None
        return Me(players[dist[0][1]], 'track')
