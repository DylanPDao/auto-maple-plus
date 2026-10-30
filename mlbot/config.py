"""Loads mlbot/config.toml into typed dataclasses."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:
    import tomli as tomllib

from mlbot.keys import vk

ROOT = Path(__file__).resolve().parent
DEFAULT_PATH = ROOT / 'config.toml'


@dataclass(frozen=True)
class Roi:
    name: str
    x: int
    y: int
    w: int
    h: int
    format: str = 'png'
    quality: int = 90
    scale: float = 1.0


@dataclass(frozen=True)
class Config:
    window_title: str
    fps: int
    rois: dict[str, Roi]
    keybinds: dict[str, str]
    out_dir: Path
    chunk_frames: int
    queue_frames: int
    toggle_key: str
    models: dict[str, Path] = field(default_factory=dict)

    @property
    def action_vks(self) -> dict[str, int]:
        return {action: vk(key) for action, key in self.keybinds.items()}


def load(path: Path | str = DEFAULT_PATH) -> Config:
    with open(path, 'rb') as f:
        raw = tomllib.load(f)

    rois = {}
    for name, r in raw['capture']['rois'].items():
        x, y, w, h = r['rect']
        fmt = r.get('format', 'png')
        if fmt not in ('png', 'jpg'):
            raise ValueError(f"ROI '{name}': format must be png or jpg")
        rois[name] = Roi(name, x, y, w, h, fmt, r.get('quality', 90), r.get('scale', 1.0))

    rec = raw['recorder']
    repo = ROOT.parent
    cfg = Config(
        window_title=raw['game']['window_title'],
        fps=raw['capture']['fps'],
        rois=rois,
        keybinds=dict(raw['keybinds']),
        out_dir=repo / rec['out_dir'],
        chunk_frames=rec['chunk_frames'],
        queue_frames=rec['queue_frames'],
        toggle_key=rec['toggle_key'],
        models={k: repo / v for k, v in raw.get('models', {}).items()},
    )
    cfg.action_vks  # validate key names early
    vk(cfg.toggle_key)
    return cfg
