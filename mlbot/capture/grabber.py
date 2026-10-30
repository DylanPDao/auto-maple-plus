"""Grabs the game's client area with dxcam on a thread and publishes it to a LatestFrame slot."""

from __future__ import annotations

import threading
import time
from typing import Protocol

import numpy as np

from mlbot.capture.latest import LatestFrame
from mlbot.config import Roi

# How often (s) to check whether the game window moved or resized
WINDOW_CHECK_INTERVAL = 1.0


class FrameSource(Protocol):
    def start(self) -> None: ...
    def next(self) -> np.ndarray | None: ...
    def stop(self) -> None: ...


class DxcamSource:
    """Desktop Duplication capture of one window's client area, following it if it moves."""

    def __init__(self, window_title: str, fps: int) -> None:
        self.window_title = window_title
        self.fps = fps
        self.camera = None
        self.region: tuple[int, int, int, int] | None = None
        self._next_check = 0.0

    def start(self) -> None:
        import dxcam
        from mlbot.capture import window

        self._window = window
        self.hwnd = window.find_window(self.window_title)
        self.camera = dxcam.create(output_color='BGR')
        self._restart(window.client_rect(self.hwnd))

    def _restart(self, region: tuple[int, int, int, int]) -> None:
        if self.camera.is_capturing:
            self.camera.stop()
        self.region = region
        # video_mode repeats the last frame when the screen doesn't change, giving a steady rate
        self.camera.start(region=region, target_fps=self.fps, video_mode=True)

    def next(self) -> np.ndarray | None:
        now = time.perf_counter()
        if now >= self._next_check:
            self._next_check = now + WINDOW_CHECK_INTERVAL
            region = self._window.client_rect(self.hwnd)
            if region != self.region:
                print(f'[capture] window moved to {region}, restarting capture')
                self._restart(region)
        return self.camera.get_latest_frame()

    def stop(self) -> None:
        if self.camera is not None and self.camera.is_capturing:
            self.camera.stop()


class Grabber:
    def __init__(self, source: FrameSource, slot: LatestFrame) -> None:
        self.source = source
        self.slot = slot
        self.error: BaseException | None = None
        self._running = False
        self._thread = threading.Thread(target=self._main, name='grabber', daemon=True)

    def start(self) -> None:
        self.source.start()
        self._running = True
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        self._thread.join(timeout=2)
        self.source.stop()

    def _main(self) -> None:
        try:
            while self._running:
                image = self.source.next()
                if image is not None:
                    self.slot.publish(image, time.perf_counter())
        except BaseException as e:
            self.error = e
            self._running = False
            raise


def crop(image: np.ndarray, roi: Roi) -> np.ndarray:
    """A view of ROI inside a full client-area frame (clipped to the frame)."""

    return image[roi.y:roi.y + roi.h, roi.x:roi.x + roi.w]
