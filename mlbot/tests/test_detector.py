"""Detector post-processing on a hand-made model output: no ONNX model or GPU needed."""

from __future__ import annotations

import numpy as np

from mlbot.perception.detector import Detector
from mlbot.perception.labels import CLASSES


def bare_detector(w: int = 1280, h: int = 736) -> Detector:
    d = Detector.__new__(Detector)  # skip the onnxruntime session
    d.input_w, d.input_h, d.conf, d.iou = w, h, 0.35, 0.6
    return d


def output(*boxes: tuple[float, float, float, float, int, float]) -> np.ndarray:
    """Model output (4 + classes, anchors) from (cx, cy, w, h, cls, conf) rows."""

    out = np.zeros((4 + len(CLASSES), len(boxes)), np.float32)
    for i, (cx, cy, w, h, c, conf) in enumerate(boxes):
        out[:4, i] = cx, cy, w, h
        out[4 + c, i] = conf
    return out


def test_decode_maps_back_to_frame_pixels() -> None:
    d = bare_detector()
    # a 720p frame letterboxed into 1280x736: scale 1, 8 px of padding on top
    [det] = d._decode(output((640, 408, 40, 60, 1, 0.9)), scale=1.0, px=0, py=8)
    assert det.name == 'mob' and abs(det.conf - 0.9) < 1e-6
    assert (det.x1, det.y1, det.x2, det.y2) == (620, 370, 660, 430)
    assert det.feet == (640, 430)


def test_nms_is_per_class_and_drops_low_confidence() -> None:
    d = bare_detector()
    dets = d._decode(output(
        (100, 100, 50, 50, 0, 0.9),   # player
        (102, 101, 50, 50, 0, 0.8),   # same player again: suppressed
        (100, 100, 50, 50, 1, 0.7),   # a mob in the same spot: different class, kept
        (500, 500, 50, 50, 1, 0.2),   # below confidence: dropped
    ), scale=1.0, px=0, py=0)
    assert sorted((x.name, round(x.conf, 1)) for x in dets) == [('mob', 0.7), ('player', 0.9)]
