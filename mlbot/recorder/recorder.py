"""Pairs every captured frame with the keys held at that moment and hands both to a SessionWriter."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

from mlbot.capture.grabber import crop
from mlbot.capture.latest import LatestFrame
from mlbot.config import Roi
from mlbot.recorder.keylog import KeyLogger
from mlbot.recorder.storage import Sample, SessionWriter

STATS_INTERVAL = 5.0


class Recorder:
    def __init__(self, slot: LatestFrame, keylog: KeyLogger, writer: SessionWriter,
                 rois: dict[str, Roi], is_foreground: Callable[[], bool] = lambda: True,
                 foreground_only: bool = True) -> None:
        self.slot = slot
        self.keylog = keylog
        self.writer = writer
        self.rois = rois
        self.is_foreground = is_foreground
        self.foreground_only = foreground_only
        self.paused = False
        self.skipped = 0  # frames grabbed but never seen by the recorder (it fell behind)
        self.stop_event = threading.Event()

    def toggle_pause(self) -> None:
        self.paused = not self.paused
        print(f"[recorder] {'paused' if self.paused else 'recording'}")

    def run(self) -> None:
        last_seq = 0
        stats_frames, stats_start = 0, time.perf_counter()
        while not self.stop_event.is_set():
            frame = self.slot.wait_newer(last_seq, timeout=0.5)
            if frame is None:
                continue
            if last_seq:
                self.skipped += frame.seq - last_seq - 1
            last_seq = frame.seq
            self.writer.add_events(self.keylog.drain_events())

            fg = self.is_foreground()
            if self.paused or (self.foreground_only and not fg):
                continue
            crops = {name: crop(frame.image, roi).copy() for name, roi in self.rois.items()}
            if self.writer.add(Sample(frame.seq, frame.t, self.keylog.snapshot(), fg, crops)):
                stats_frames += 1

            now = time.perf_counter()
            if now - stats_start >= STATS_INTERVAL:
                print(f'[recorder] {stats_frames / (now - stats_start):5.1f} fps saved | '
                      f'{self.writer.frames} frames, {self.writer.chunks} chunks | '
                      f'skipped {self.skipped}, dropped {self.writer.dropped}')
                stats_frames, stats_start = 0, now
        self.writer.add_events(self.keylog.drain_events())
