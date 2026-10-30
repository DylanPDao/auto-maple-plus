"""
Builds a YOLO dataset from the labeled frames and trains the detector.

    python -m mlbot.perception.train [--epochs 100] [--imgsz 1280] [--batch 8] [--model yolo11n.pt]

Frames are split into train/val by one-minute blocks of each video, so near-identical neighbouring
frames never land on both sides (that would make val scores look better than they are).
The best weights are copied to models/detector.pt.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
from pathlib import Path

from mlbot.config import ROOT
from mlbot.perception import labels
from mlbot.perception.labels import CLASSES

YOLO_DIR = labels.DATASETS / 'yolo'
MODELS = ROOT.parent / 'models'
VAL_FRACTION = 0.15
BLOCK_SECONDS = 60


def is_val(image: Path) -> bool:
    """Frame names are <video id>_<ms>.jpg; hash (video, minute) for a stable split."""

    video, ms = image.stem.rsplit('_', 1)
    block = int(ms) // 1000 // BLOCK_SECONDS
    digest = hashlib.md5(f'{video}/{block}'.encode()).digest()
    return digest[0] / 256 < VAL_FRACTION


def link_or_copy(src: Path, dst: Path) -> None:
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def build_dataset() -> Path:
    if YOLO_DIR.exists():
        shutil.rmtree(YOLO_DIR)
    counts = {'train': 0, 'val': 0}
    for image in sorted(labels.RAW.glob('*/*.jpg')):
        label = labels.label_path(image)
        if not label.exists():
            continue
        split = 'val' if is_val(image) else 'train'
        for kind, src in (('images', image), ('labels', label)):
            dst = YOLO_DIR / kind / split / src.name
            dst.parent.mkdir(parents=True, exist_ok=True)
            link_or_copy(src, dst)
        counts[split] += 1
    if not counts['train']:
        raise SystemExit(f'Need labeled training frames, have {counts}. Label more frames.')
    if not counts['val']:
        # bootstrap runs on a handful of frames: validate on the training frames, scores will flatter
        print('[train] WARNING: no validation frames yet, validating on training frames')
        shutil.copytree(YOLO_DIR / 'images' / 'train', YOLO_DIR / 'images' / 'val')
        shutil.copytree(YOLO_DIR / 'labels' / 'train', YOLO_DIR / 'labels' / 'val')
    data = YOLO_DIR / 'data.yaml'
    names = '\n'.join(f'  {i}: {c}' for i, c in enumerate(CLASSES))
    data.write_text(f'path: {YOLO_DIR.as_posix()}\ntrain: images/train\nval: images/val\nnames:\n{names}\n')
    print(f"[train] dataset: {counts['train']} train / {counts['val']} val frames")
    return data


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--model', default='yolo11n.pt', help='starting weights')
    parser.add_argument('--epochs', type=int, default=100)
    parser.add_argument('--imgsz', type=int, default=1280, help='big enough to keep minimap dots')
    parser.add_argument('--batch', type=int, default=8)
    parser.add_argument('--patience', type=int, default=30,
                        help='stop after this many epochs without improvement, 0 = never. Use 0 on tiny '
                             'datasets: the first 100 batches are a slow warmup, many epochs at 8 frames')
    parser.add_argument('--name', default='detector')
    args = parser.parse_args()

    from ultralytics import YOLO

    data = build_dataset()
    model = YOLO(args.model)
    model.train(
        data=str(data), epochs=args.epochs, imgsz=args.imgsz, batch=args.batch, device=0,
        project=str(MODELS / 'runs'), name=args.name, exist_ok=True, patience=args.patience or args.epochs,
        fliplr=0.5, degrees=0, scale=0.3,  # the game camera never rotates; zoom only a little
        workers=2,
    )
    best = MODELS / 'runs' / args.name / 'weights' / 'best.pt'
    shutil.copy2(best, MODELS / 'detector.pt')
    print(f"[train] saved {MODELS / 'detector.pt'}")


if __name__ == '__main__':
    main()
