"""Saves game frames to disk so detection can be tuned offline with tools/replay_combat.py."""

import os
import time
import threading
import cv2
from src.common import config


RECORDINGS_DIR = 'recordings'


class FrameRecorder:
    def __init__(self):
        self.recording = False
        self.directory = None
        self.count = 0
        self.thread = None

    def start(self, interval=0.5):
        """Starts saving the current frame every INTERVAL seconds into a new timestamped folder."""

        if self.recording:
            return
        self.directory = os.path.join(RECORDINGS_DIR, time.strftime('%Y%m%d-%H%M%S'))
        os.makedirs(self.directory, exist_ok=True)
        self.count = 0
        self.recording = True
        self.thread = threading.Thread(target=self._main, args=(interval,), daemon=True)
        self.thread.start()
        print(f"\n[~] Recording frames to '{self.directory}'")

    def stop(self):
        if self.recording:
            self.recording = False
            print(f"\n[~] Stopped recording, saved {self.count} frames to '{self.directory}'")

    def _main(self, interval):
        last = None
        while self.recording:
            frame = config.capture.frame
            if frame is not None and frame is not last:
                path = os.path.join(self.directory, f'frame_{self.count:05d}.png')
                cv2.imwrite(path, frame)
                self.count += 1
                last = frame
            time.sleep(interval)
