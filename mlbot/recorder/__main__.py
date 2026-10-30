"""
Records gameplay: ROIs from every captured frame plus the keys held at that moment.

    python -m mlbot.recorder [--seconds N] [--config path]

Play normally. The toggle key (config: recorder.toggle_key) pauses/resumes; Ctrl+C stops and saves.
Frames are only saved while the game window is in the foreground.
"""

from __future__ import annotations

import argparse
import threading
import time

from mlbot import config as config_module
from mlbot.capture import window
from mlbot.capture.grabber import DxcamSource, Grabber
from mlbot.capture.latest import LatestFrame
from mlbot.keys import vk
from mlbot.recorder.keylog import KeyLogger
from mlbot.recorder.recorder import Recorder
from mlbot.recorder.storage import SessionWriter, new_session_dir


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--config', default=config_module.DEFAULT_PATH)
    parser.add_argument('--seconds', type=float, default=0, help='stop after N seconds (0 = until Ctrl+C)')
    args = parser.parse_args()
    cfg = config_module.load(args.config)

    hwnd = window.find_window(cfg.window_title)
    left, top, right, bottom = window.client_rect(hwnd)
    for roi in cfg.rois.values():
        if roi.x + roi.w > right - left or roi.y + roi.h > bottom - top:
            print(f"[!] ROI '{roi.name}' extends past the {right - left}x{bottom - top} client area, "
                  f"it will be clipped. Fix it with: python -m mlbot.tools.pick_rois")

    directory = new_session_dir(cfg.out_dir)
    meta = {
        'created': time.strftime('%Y-%m-%d %H:%M:%S'),
        'window_title': cfg.window_title,
        'client_size': [right - left, bottom - top],
        'fps': cfg.fps,
        'keybinds': cfg.keybinds,
        'action_vks': cfg.action_vks,
    }
    writer = SessionWriter(directory, meta, cfg.rois, cfg.chunk_frames, cfg.queue_frames)
    slot = LatestFrame()
    grabber = Grabber(DxcamSource(cfg.window_title, cfg.fps), slot)
    keylog = KeyLogger()
    recorder = Recorder(slot, keylog, writer, cfg.rois, lambda: window.is_foreground(hwnd))
    keylog.on_press(vk(cfg.toggle_key), recorder.toggle_pause)

    keylog.start()
    grabber.start()
    thread = threading.Thread(target=recorder.run, name='recorder', daemon=True)
    thread.start()
    print(f"[recorder] saving to {directory}\n"
          f"[recorder] '{cfg.toggle_key}' pauses/resumes, Ctrl+C stops")

    start = time.perf_counter()
    try:
        while thread.is_alive() and not grabber.error:
            if args.seconds and time.perf_counter() - start >= args.seconds:
                break
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        recorder.stop_event.set()
        thread.join(timeout=2)
        grabber.stop()
        keylog.stop()
        print('[recorder] writing the last chunk...')
        writer.close()
        print(f'[recorder] saved {writer.frames} frames in {writer.chunks} chunks to {directory} '
              f'(skipped {recorder.skipped}, dropped {writer.dropped})')
    if grabber.error:
        raise grabber.error


if __name__ == '__main__':
    main()
