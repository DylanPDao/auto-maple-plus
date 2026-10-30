"""
Downloads gameplay videos from YouTube and extracts frames for detector labeling.

    python -m mlbot.tools.yt_frames URL [URL ...] [--every 1.0] [--height 720]
    python -m mlbot.tools.yt_frames --list mlbot/datasets/videos.txt

Frames go to datasets/raw/<video id>/, near-duplicates (standing still, menus) and black
transition frames are skipped. datasets/raw/manifest.csv records where every frame came from.
Use --crop to cut a facecam or overlay out: x,y,w,h as fractions of the frame, e.g. 0,0,1,0.85
"""

from __future__ import annotations

import argparse
import csv
from collections.abc import Iterator
from pathlib import Path

import cv2
import numpy as np

from mlbot.config import ROOT

DATASETS = ROOT.parent / 'datasets'
THUMB_SIZE = (64, 36)


def thumb(image: np.ndarray) -> np.ndarray:
    return cv2.resize(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), THUMB_SIZE,
                      interpolation=cv2.INTER_AREA).astype(np.int16)


def is_new(t: np.ndarray, last: np.ndarray | None, min_diff: float) -> bool:
    """True if thumbnail T differs from LAST by a mean absolute difference of at least MIN_DIFF."""

    return last is None or float(np.abs(t - last).mean()) >= min_diff


def is_blank(t: np.ndarray, min_std: float = 8.0) -> bool:
    """Black or single-colour frames: fades, loading screens."""

    return float(t.std()) < min_std


def crop_frac(image: np.ndarray, crop: tuple[float, float, float, float] | None) -> np.ndarray:
    if crop is None:
        return image
    h, w = image.shape[:2]
    x, y, cw, ch = crop
    return image[int(y * h):int((y + ch) * h), int(x * w):int((x + cw) * w)]


def sample_frames(path: Path, every: float) -> Iterator[tuple[float, np.ndarray]]:
    """(time in seconds, frame) roughly every EVERY seconds of video, decoded sequentially."""

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"OpenCV can't open {path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    step = max(1, round(every * fps))
    index = 0
    try:
        while cap.grab():
            if index % step == 0:
                ok, frame = cap.retrieve()
                if ok:
                    yield index / fps, frame
            index += 1
    finally:
        cap.release()


def download(url: str, height: int, directory: Path) -> tuple[Path, dict]:
    import yt_dlp

    options = {
        # video only (no audio, no ffmpeg merge needed); prefer H.264 since OpenCV decodes it everywhere
        'format': f'bv*[height<={height}][vcodec^=avc1]/bv*[height<={height}]/b[height<={height}]',
        'outtmpl': str(directory / '%(id)s.%(ext)s'),
        'quiet': True,
        'noprogress': False,
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=True)
        return Path(ydl.prepare_filename(info)), info


def extract(video: Path, info: dict, out_root: Path, every: float, min_diff: float,
            crop: tuple[float, float, float, float] | None, manifest: csv.writer) -> tuple[int, int]:
    out = out_root / info['id']
    out.mkdir(parents=True, exist_ok=True)
    kept = seen = 0
    last = None
    for t, frame in sample_frames(video, every):
        seen += 1
        frame = crop_frac(frame, crop)
        small = thumb(frame)
        if is_blank(small) or not is_new(small, last, min_diff):
            continue
        last = small
        name = f"{info['id']}_{int(t * 1000):09d}.jpg"
        cv2.imwrite(str(out / name), frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
        manifest.writerow([f"{info['id']}/{name}", info['id'], f'{t:.2f}', info.get('title', ''),
                           info.get('channel', ''), info.get('webpage_url', '')])
        kept += 1
        if kept % 200 == 0:
            print(f'  {kept} frames kept ({t / 60:.0f} min in)')
    return kept, seen


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('urls', nargs='*')
    parser.add_argument('--list', type=Path, help='text file with one URL per line (# comments ok)')
    parser.add_argument('--every', type=float, default=1.0, help='seconds between sampled frames')
    parser.add_argument('--height', type=int, default=720, help='max video height to download')
    parser.add_argument('--min-diff', type=float, default=4.0,
                        help='skip frames this similar to the last kept one (mean abs diff, 0-255)')
    parser.add_argument('--crop', help='x,y,w,h fractions of the frame to keep, e.g. 0,0,1,0.85')
    parser.add_argument('--out', type=Path, default=DATASETS / 'raw')
    parser.add_argument('--keep-video', action='store_true', help="don't delete the video afterwards")
    args = parser.parse_args()

    urls = list(args.urls)
    if args.list:
        urls += [line.split('#')[0].strip() for line in args.list.read_text().splitlines()]
    urls = [u for u in urls if u]
    if not urls:
        parser.error('give at least one URL or --list')
    crop = tuple(float(v) for v in args.crop.split(',')) if args.crop else None

    videos = DATASETS / 'videos'
    videos.mkdir(parents=True, exist_ok=True)
    args.out.mkdir(parents=True, exist_ok=True)
    manifest_path = args.out / 'manifest.csv'
    new_manifest = not manifest_path.exists()
    with open(manifest_path, 'a', newline='', encoding='utf-8') as f:
        manifest = csv.writer(f)
        if new_manifest:
            manifest.writerow(['file', 'video_id', 't_sec', 'title', 'channel', 'url'])
        for url in urls:
            print(f'[yt] downloading {url}')
            video, info = download(url, args.height, videos)
            print(f"[yt] extracting '{info.get('title')}' ({info.get('width')}x{info.get('height')}, "
                  f"{info.get('duration_string', '?')})")
            kept, seen = extract(video, info, args.out, args.every, args.min_diff, crop, manifest)
            f.flush()
            print(f'[yt] kept {kept} of {seen} sampled frames -> {args.out / info["id"]}')
            if not args.keep_video:
                video.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
