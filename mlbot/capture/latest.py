"""A single-slot frame buffer: consumers always see the newest frame, never a stale queue."""

from __future__ import annotations

import threading
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Frame:
    seq: int          # increases by 1 per published frame; gaps on the consumer side = skipped frames
    t: float          # time.perf_counter() when the frame was grabbed
    image: np.ndarray  # BGR, full game client area


class LatestFrame:
    """
    Writers replace the slot; readers either peek (get, lock-free: one reference read) or block
    until a frame newer than the one they last saw arrives (wait_newer).
    """

    def __init__(self) -> None:
        self._frame: Frame | None = None
        self._seq = 0
        self._cond = threading.Condition()

    def publish(self, image: np.ndarray, t: float) -> Frame:
        with self._cond:
            self._seq += 1
            frame = Frame(self._seq, t, image)
            self._frame = frame
            self._cond.notify_all()
        return frame

    def get(self) -> Frame | None:
        return self._frame

    def wait_newer(self, seq: int, timeout: float | None = None) -> Frame | None:
        with self._cond:
            ok = self._cond.wait_for(
                lambda: self._frame is not None and self._frame.seq > seq, timeout)
            return self._frame if ok else None
