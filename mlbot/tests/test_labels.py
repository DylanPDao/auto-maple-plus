from __future__ import annotations

from pathlib import Path

from mlbot.perception import labels
from mlbot.perception.labels import Box
from mlbot.perception.train import is_val


def test_roundtrip_and_clipping(tmp_path: Path) -> None:
    path = tmp_path / 'a.txt'
    boxes = [Box(1, 100, 50, 300, 250), Box(4, -10, 700, 20, 730)]  # second one spills off-frame
    labels.write(path, boxes, 1280, 720)
    back = labels.read(path, 1280, 720)
    assert back[0].cls == 1 and round(back[0].x1) == 100 and round(back[0].y2) == 250
    assert back[1].x1 > -0.01 and back[1].y2 < 720.01  # clipped, give or take 6-digit rounding


def test_empty_label(tmp_path: Path) -> None:
    path = tmp_path / 'empty.txt'
    labels.write(path, [], 1280, 720)
    assert path.exists() and labels.read(path, 1280, 720) == []


def test_split_keeps_minute_blocks_together() -> None:
    a = is_val(Path('vid_000060000.jpg'))
    assert all(is_val(Path(f'vid_{ms:09d}.jpg')) == a for ms in range(60000, 120000, 1000))
    share = sum(is_val(Path(f'v{i}_000000000.jpg')) for i in range(2000)) / 2000
    assert 0.1 < share < 0.2
