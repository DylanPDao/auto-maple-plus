"""
Runtime detector: an exported YOLO ONNX model run with onnxruntime (TensorRT > CUDA > CPU),
returning typed detections. No ultralytics needed at runtime.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from mlbot.perception.labels import CLASSES

PROVIDERS = ['TensorrtExecutionProvider', 'CUDAExecutionProvider', 'CPUExecutionProvider']


def _tensorrt_installed() -> bool:
    import importlib.util

    return importlib.util.find_spec('tensorrt') is not None


@dataclass(frozen=True)
class Detection:
    cls: int
    conf: float
    x1: float  # pixels in the input frame
    y1: float
    x2: float
    y2: float

    @property
    def name(self) -> str:
        return CLASSES[self.cls]

    @property
    def center(self) -> tuple[float, float]:
        return (self.x1 + self.x2) / 2, (self.y1 + self.y2) / 2

    @property
    def feet(self) -> tuple[float, float]:
        """Bottom-centre: where a character or mob touches its platform."""
        return (self.x1 + self.x2) / 2, self.y2


@dataclass
class Timing:
    pre_ms: float = 0.0
    infer_ms: float = 0.0
    post_ms: float = 0.0

    @property
    def total_ms(self) -> float:
        return self.pre_ms + self.infer_ms + self.post_ms


class Detector:
    def __init__(self, path: Path | str, conf: float = 0.35, iou: float = 0.6,
                 providers: list[str] | None = None) -> None:
        import onnxruntime as ort

        try:
            ort.preload_dlls()  # CUDA/cuDNN DLLs that ship with torch, so no separate CUDA install
        except Exception:
            pass
        available = ort.get_available_providers()
        chosen = [p for p in (providers or PROVIDERS) if p in available]
        if 'TensorrtExecutionProvider' in chosen and not _tensorrt_installed():
            chosen.remove('TensorrtExecutionProvider')  # listed by onnxruntime-gpu, but needs TensorRT itself
        options = ort.SessionOptions()
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.session = ort.InferenceSession(str(path), options, providers=chosen)
        self.provider = self.session.get_providers()[0]
        inp = self.session.get_inputs()[0]
        self.input_name = inp.name
        # exported by mlbot.perception.export: raw BGR uint8 (1, H, W, 3), preprocessing in-graph
        self.raw_input = inp.type == 'tensor(uint8)'
        if self.raw_input:
            self.input_h, self.input_w = inp.shape[1], inp.shape[2]
        else:
            self.input_h, self.input_w = inp.shape[2], inp.shape[3]
        self.fp16 = inp.type == 'tensor(float16)'
        self.conf = conf
        self.iou = iou
        self.timing = Timing()

    def letterbox(self, image: np.ndarray) -> tuple[np.ndarray, float, int, int]:
        h, w = image.shape[:2]
        scale = min(self.input_w / w, self.input_h / h)
        nw, nh = round(w * scale), round(h * scale)
        resized = image if (nw, nh) == (w, h) else cv2.resize(image, (nw, nh), interpolation=cv2.INTER_LINEAR)
        px, py = (self.input_w - nw) // 2, (self.input_h - nh) // 2
        canvas = cv2.copyMakeBorder(resized, py, self.input_h - nh - py, px, self.input_w - nw - px,
                                    cv2.BORDER_CONSTANT, value=(114, 114, 114))
        return canvas, scale, px, py

    def detect(self, image: np.ndarray) -> list[Detection]:
        """IMAGE is BGR uint8, any size; boxes come back in IMAGE's pixels."""

        t0 = time.perf_counter()
        canvas, scale, px, py = self.letterbox(image)
        if self.raw_input:
            blob = canvas[None]
        else:  # a plain ultralytics export: preprocess on the CPU (slow, ~25 ms)
            blob = cv2.dnn.blobFromImage(canvas, 1 / 255, swapRB=True)
            if self.fp16:
                blob = blob.astype(np.float16)
        t1 = time.perf_counter()
        out = self.session.run(None, {self.input_name: blob})[0][0]  # (4 + classes, anchors)
        t2 = time.perf_counter()
        detections = self._decode(out.astype(np.float32), scale, px, py)
        t3 = time.perf_counter()
        self.timing = Timing((t1 - t0) * 1000, (t2 - t1) * 1000, (t3 - t2) * 1000)
        return detections

    def _decode(self, out: np.ndarray, scale: float, px: int, py: int) -> list[Detection]:
        scores = out[4:]
        cls = scores.argmax(0)
        conf = scores[cls, np.arange(scores.shape[1])]
        keep = conf >= self.conf
        if not keep.any():
            return []
        cx, cy, w, h = out[:4, keep]
        cls, conf = cls[keep], conf[keep]
        x1 = (cx - w / 2 - px) / scale
        y1 = (cy - h / 2 - py) / scale
        bw, bh = w / scale, h / scale
        # per-class NMS: offset boxes by class so different classes never suppress each other
        offset = cls * 4096.0
        rects = np.stack([x1 + offset, y1, bw, bh], 1).tolist()
        idx = cv2.dnn.NMSBoxes(rects, conf.tolist(), self.conf, self.iou)
        return [Detection(int(cls[i]), float(conf[i]), float(x1[i]), float(y1[i]),
                          float(x1[i] + bw[i]), float(y1[i] + bh[i])) for i in np.array(idx).ravel()]
