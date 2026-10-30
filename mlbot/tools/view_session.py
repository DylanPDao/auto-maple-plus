"""
Summarizes a recorded session and plays it back with the held action keys drawn on top.

    python -m mlbot.tools.view_session [session_dir] [--stats]

Defaults to the newest session. Playback: space pauses, q quits.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np

from mlbot import config as config_module
from mlbot.keys import NAMES
from mlbot.recorder.storage import Session


def summarize(session: Session) -> None:
    t, seq = [], []
    for path in session.chunks:
        with np.load(path) as z:
            t.append(z['t'])
            seq.append(z['seq'])
    t, seq = np.concatenate(t), np.concatenate(seq)
    if len(t) < 2:
        print(f'{len(t)} frames')
        return
    dt = np.diff(t) * 1000
    ev = session.events()
    duration = t[-1] - t[0]
    print(f'{session.directory}\n'
          f'  {len(t)} frames over {duration:.1f}s = {len(t) / duration:.1f} fps, {len(session.chunks)} chunks\n'
          f'  frame interval ms: median {np.median(dt):.1f}, p95 {np.percentile(dt, 95):.1f}, max {dt.max():.1f}\n'
          f'  capture seq gaps: {int((np.diff(seq) - 1).clip(0).sum())} frames not saved\n'
          f'  key edges: {len(ev["t"])} ({int(ev["injected"].sum())} injected by software)')
    action_vks: dict[str, int] = session.meta['action_vks']
    for action, code in action_vks.items():
        downs = int(((ev['vk'] == code) & ev['down']).sum())
        print(f'    {action:10s} {NAMES.get(code, hex(code)):6s} pressed {downs}x')


def play(session: Session) -> None:
    action_vks: dict[str, int] = session.meta['action_vks']
    prev_t = None
    for frame in session.frames():
        tiles = [img for img in frame.images.values() if img is not None]
        height = max(img.shape[0] for img in tiles)
        canvas = np.hstack([cv2.copyMakeBorder(img, 0, height - img.shape[0], 0, 0, cv2.BORDER_CONSTANT)
                            for img in tiles])
        held = [a for a, code in action_vks.items() if frame.keys[code]]
        cv2.putText(canvas, f'#{frame.seq}  ' + ' '.join(held), (8, height - 12),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
        cv2.imshow('session', canvas)
        delay = 1 if prev_t is None else max(1, int((frame.t - prev_t) * 1000))
        prev_t = frame.t
        key = cv2.waitKey(delay) & 0xFF
        if key == ord('q'):
            break
        if key == ord(' '):
            cv2.waitKey(0)
    cv2.destroyAllWindows()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('session', nargs='?')
    parser.add_argument('--stats', action='store_true', help='print the summary only')
    args = parser.parse_args()
    if args.session:
        directory = Path(args.session)
    else:
        sessions = sorted(p for p in config_module.load().out_dir.iterdir() if p.is_dir())
        if not sessions:
            raise SystemExit('No recorded sessions yet')
        directory = sessions[-1]
    session = Session(directory)
    summarize(session)
    if not args.stats:
        play(session)


if __name__ == '__main__':
    main()
