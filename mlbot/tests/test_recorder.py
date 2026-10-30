"""Recorder pipeline tests with fake frames and a fake keyboard: no game or hook needed."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import numpy as np

from mlbot.capture.latest import LatestFrame
from mlbot.config import Roi
from mlbot.recorder.recorder import Recorder
from mlbot.recorder.storage import Sample, Session, SessionWriter

ROIS = {
    'minimap': Roi('minimap', 10, 20, 40, 30, 'png'),
    'play': Roi('play', 0, 0, 200, 100, 'jpg', 95, 0.5),
}


def fake_image(i: int) -> np.ndarray:
    image = np.zeros((120, 240, 3), np.uint8)
    image[20:50, 10:50] = (i * 7) % 256  # the minimap ROI area, a value per frame
    return image


class FakeKeys:
    def __init__(self) -> None:
        self.state = np.zeros(256, bool)
        self.events: list = []

    def snapshot(self) -> np.ndarray:
        return np.packbits(self.state)

    def drain_events(self) -> list:
        events, self.events = self.events, []
        return events


def test_latest_frame_wait_newer() -> None:
    slot = LatestFrame()
    assert slot.wait_newer(0, timeout=0.01) is None
    slot.publish(fake_image(1), 1.0)
    slot.publish(fake_image(2), 2.0)
    frame = slot.wait_newer(0, timeout=0.01)
    assert frame is not None and frame.seq == 2  # only the newest frame is ever handed out
    assert slot.wait_newer(2, timeout=0.01) is None


def test_writer_roundtrip(tmp_path: Path) -> None:
    writer = SessionWriter(tmp_path, {'action_vks': {}}, ROIS, chunk_frames=4, max_pending=100)
    keys = np.zeros(256, bool)
    for i in range(10):
        keys[:] = False
        keys[0x25 if i % 2 else 0x27] = True
        image = fake_image(i)
        crops = {n: image[r.y:r.y + r.h, r.x:r.x + r.w].copy() for n, r in ROIS.items()}
        assert writer.add(Sample(i + 1, float(i), np.packbits(keys), True, crops))
    writer.add_events([(0.5, 0x25, True, False), (0.7, 0x25, False, True)])
    writer.close()

    session = Session(tmp_path)
    assert len(session.chunks) == 3 and len(session) == 10
    frames = list(session.frames())
    assert [f.seq for f in frames] == list(range(1, 11))
    for i, f in enumerate(frames):
        assert f.images['minimap'].shape == (30, 40, 3)
        assert (f.images['minimap'] == (i * 7) % 256).all()  # png is lossless
        assert f.images['play'].shape == (50, 100, 3)          # scaled by 0.5
        assert f.keys[0x25] == bool(i % 2) and f.keys[0x27] == (not i % 2)
    ev = session.events()
    assert ev['vk'].tolist() == [0x25, 0x25] and ev['injected'].tolist() == [False, True]


def test_recorder_skips_paused_and_background(tmp_path: Path) -> None:
    slot = LatestFrame()
    writer = SessionWriter(tmp_path, {'action_vks': {}}, ROIS, chunk_frames=100, max_pending=100)
    foreground = {'value': True}
    recorder = Recorder(slot, FakeKeys(), writer, ROIS, lambda: foreground['value'])
    thread = threading.Thread(target=recorder.run)
    thread.start()

    def publish(i: int) -> None:
        slot.publish(fake_image(i), float(i))
        time.sleep(0.02)

    publish(1)
    publish(2)
    recorder.toggle_pause()
    publish(3)
    recorder.toggle_pause()
    foreground['value'] = False
    publish(4)
    foreground['value'] = True
    publish(5)
    recorder.stop_event.set()
    thread.join()
    writer.close()

    assert [f.seq for f in Session(tmp_path).frames()] == [1, 2, 5]
