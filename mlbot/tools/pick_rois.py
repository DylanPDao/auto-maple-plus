"""
Grabs one frame of the game window and lets you drag a box for each ROI in config.toml,
then writes the new rects back into the file (comments are kept).

    python -m mlbot.tools.pick_rois [--only minimap]
"""

from __future__ import annotations

import argparse
import re

import cv2
import dxcam

from mlbot import config as config_module
from mlbot.capture import window


def set_rect(text: str, name: str, rect: tuple[int, int, int, int]) -> str:
    pattern = re.compile(rf'(\[capture\.rois\.{re.escape(name)}\][^\[]*?rect\s*=\s*)\[[^\]]*\]', re.S)
    new, count = pattern.subn(lambda m: f'{m.group(1)}[{", ".join(map(str, rect))}]', text)
    if count != 1:
        raise ValueError(f"Couldn't find rect for ROI '{name}' in config")
    return new


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', default=config_module.DEFAULT_PATH)
    parser.add_argument('--only', nargs='*', help='ROI names to pick (default: all)')
    args = parser.parse_args()
    cfg = config_module.load(args.config)

    hwnd = window.find_window(cfg.window_title)
    camera = dxcam.create(output_color='BGR')
    frame = None
    while frame is None:
        frame = camera.grab(region=window.client_rect(hwnd))
    del camera
    print(f'Client area: {frame.shape[1]}x{frame.shape[0]}')

    text = open(args.config, encoding='utf-8').read()
    for name in args.only or list(cfg.rois):
        title = f"Drag the '{name}' ROI, Enter to confirm, c to skip"
        x, y, w, h = cv2.selectROI(title, frame, showCrosshair=False)
        cv2.destroyWindow(title)
        if w and h:
            text = set_rect(text, name, (x, y, w, h))
            print(f'{name}: [{x}, {y}, {w}, {h}]')
    with open(args.config, 'w', encoding='utf-8') as f:
        f.write(text)
    config_module.load(args.config)
    print(f'Saved {args.config}')


if __name__ == '__main__':
    main()
