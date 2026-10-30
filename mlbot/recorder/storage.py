"""
Session format on disk:

    <session>/meta.json           capture settings, ROIs, keybinds
    <session>/chunk_00000.npz     one chunk of consecutive frames:
        t        float64[N]   perf_counter time each frame was grabbed
        seq      int64[N]     capture sequence number (gaps = frames the recorder skipped)
        keys     uint8[N,32]  held keys at grab time, 256 bits packed (index = VK code)
        fg       bool[N]      game window was in the foreground
        <roi>    uint8[...]   every frame's encoded ROI image (png/jpg) concatenated...
        <roi>_off int64[N+1]  ...and the byte offsets splitting it per frame
        ev_t, ev_vk, ev_down, ev_injected   raw key edges that happened during the chunk
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path

import cv2
import numpy as np

from mlbot.config import Roi
from mlbot.recorder.keylog import KeyEvent


@dataclass
class Sample:
    seq: int
    t: float
    keys: np.ndarray            # uint8[32]
    fg: bool
    crops: dict[str, np.ndarray]  # ROI name -> BGR image (owned copy)


def encode(image: np.ndarray, roi: Roi) -> np.ndarray:
    if roi.scale != 1.0:
        image = cv2.resize(image, None, fx=roi.scale, fy=roi.scale, interpolation=cv2.INTER_AREA)
    if roi.format == 'jpg':
        ok, buf = cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, roi.quality])
    else:
        ok, buf = cv2.imencode('.png', image, [cv2.IMWRITE_PNG_COMPRESSION, 1])
    if not ok:
        raise RuntimeError(f"Couldn't encode ROI '{roi.name}'")
    return buf.ravel()


class SessionWriter:
    """
    Encodes ROIs on a small thread pool and writes a chunk file every CHUNK_FRAMES frames on a
    background thread, so add() never blocks the capture loop. When MAX_PENDING frames are still
    waiting to be encoded, add() drops the frame and returns False.
    """

    def __init__(self, directory: Path, meta: dict, rois: dict[str, Roi], chunk_frames: int,
                 max_pending: int, workers: int = 3) -> None:
        self.directory = directory
        self.rois = rois
        self.chunk_frames = chunk_frames
        self.frames = 0
        self.dropped = 0
        self.chunks = 0
        directory.mkdir(parents=True, exist_ok=True)
        meta = {**meta, 'rois': {n: asdict(r) for n, r in rois.items()}, 'chunk_frames': chunk_frames}
        (directory / 'meta.json').write_text(json.dumps(meta, indent=2))

        self._encoders = ThreadPoolExecutor(workers, thread_name_prefix='encode')
        self._flusher = ThreadPoolExecutor(1, thread_name_prefix='flush')
        self._slots = threading.Semaphore(max_pending)
        self._samples: list[tuple[Sample, Future]] = []
        self._events: list[KeyEvent] = []

    def add(self, sample: Sample) -> bool:
        if not self._slots.acquire(blocking=False):
            self.dropped += 1
            return False
        future = self._encoders.submit(self._encode, sample)
        future.add_done_callback(lambda _: self._slots.release())
        self._samples.append((sample, future))
        self.frames += 1
        if len(self._samples) >= self.chunk_frames:
            self._flush()
        return True

    def add_events(self, events: list[KeyEvent]) -> None:
        self._events.extend(events)

    def close(self) -> None:
        if self._samples or self._events:
            self._flush()
        self._encoders.shutdown(wait=True)
        self._flusher.shutdown(wait=True)

    def _encode(self, sample: Sample) -> dict[str, np.ndarray]:
        encoded = {name: encode(sample.crops[name], roi) for name, roi in self.rois.items()}
        sample.crops = {}  # free the raw pixels as soon as they're encoded
        return encoded

    def _flush(self) -> None:
        samples, self._samples = self._samples, []
        events, self._events = self._events, []
        path = self.directory / f'chunk_{self.chunks:05d}.npz'
        self.chunks += 1
        self._flusher.submit(self._write, path, samples, events)

    def _write(self, path: Path, samples: list[tuple[Sample, Future]], events: list[KeyEvent]) -> None:
        encoded = [f.result() for _, f in samples]
        arrays: dict[str, np.ndarray] = {
            't': np.array([s.t for s, _ in samples], dtype=np.float64),
            'seq': np.array([s.seq for s, _ in samples], dtype=np.int64),
            'keys': np.stack([s.keys for s, _ in samples]) if samples
                    else np.zeros((0, 32), np.uint8),
            'fg': np.array([s.fg for s, _ in samples], dtype=bool),
            'ev_t': np.array([e[0] for e in events], dtype=np.float64),
            'ev_vk': np.array([e[1] for e in events], dtype=np.uint8),
            'ev_down': np.array([e[2] for e in events], dtype=bool),
            'ev_injected': np.array([e[3] for e in events], dtype=bool),
        }
        for name in self.rois:
            bufs = [e[name] for e in encoded]
            arrays[f'{name}_off'] = np.concatenate([[0], np.cumsum([len(b) for b in bufs])]).astype(np.int64)
            arrays[name] = np.concatenate(bufs) if bufs else np.zeros(0, np.uint8)
        tmp = path.with_suffix('.tmp.npz')
        np.savez(tmp, **arrays)
        tmp.replace(path)


@dataclass
class RecordedFrame:
    seq: int
    t: float
    keys: np.ndarray            # bool[256], index = VK code
    fg: bool
    images: dict[str, np.ndarray]  # ROI name -> decoded BGR image


class Session:
    """Reads a recorded session back, chunk by chunk."""

    def __init__(self, directory: Path | str) -> None:
        self.directory = Path(directory)
        self.meta = json.loads((self.directory / 'meta.json').read_text())
        self.chunks = sorted(self.directory.glob('chunk_[0-9][0-9][0-9][0-9][0-9].npz'))
        self.rois = {n: Roi(**r) for n, r in self.meta['rois'].items()}

    def __len__(self) -> int:
        total = 0
        for path in self.chunks:
            with np.load(path) as z:
                total += len(z['t'])
        return total

    def frames(self, rois: list[str] | None = None) -> Iterator[RecordedFrame]:
        names = list(self.rois) if rois is None else rois
        for path in self.chunks:
            with np.load(path) as z:
                t, seq, keys, fg = z['t'], z['seq'], np.unpackbits(z['keys'], axis=1).astype(bool), z['fg']
                data = {n: (z[n], z[f'{n}_off']) for n in names}
            for i in range(len(t)):
                images = {n: cv2.imdecode(buf[off[i]:off[i + 1]], cv2.IMREAD_COLOR)
                          for n, (buf, off) in data.items()}
                yield RecordedFrame(int(seq[i]), float(t[i]), keys[i], bool(fg[i]), images)

    def events(self) -> dict[str, np.ndarray]:
        parts: dict[str, list[np.ndarray]] = {k: [] for k in ('ev_t', 'ev_vk', 'ev_down', 'ev_injected')}
        for path in self.chunks:
            with np.load(path) as z:
                for k in parts:
                    parts[k].append(z[k])
        return {k[3:]: np.concatenate(v) if v else np.zeros(0) for k, v in parts.items()}


def new_session_dir(root: Path) -> Path:
    return root / time.strftime('%Y%m%d-%H%M%S')
