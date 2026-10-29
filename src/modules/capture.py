"""A module for tracking useful in-game information."""

import time
import cv2
import threading
import ctypes
import mss
import mss.windows
import numpy as np
from src.common import config, utils
from ctypes import wintypes
user32 = ctypes.windll.user32
user32.SetProcessDPIAware()


# Offset in pixels to adjust for windowed mode
WINDOWED_OFFSET_TOP = 36
WINDOWED_OFFSET_LEFT = 10


class MinimapStyle:
    """
    A minimap frame look: templates of its top-left and bottom-right corners, the offset from
    the top-left template's match to the minimap's top-left corner, and the offset from the
    bottom-right template's match to the minimap's bottom-right corner.
    """

    def __init__(self, name, directory, tl_offset, br_offset, method):
        self.name = name
        self.tl_template = cv2.imread(f'{directory}/minimap_tl_template.png', 0)
        self.br_template = cv2.imread(f'{directory}/minimap_br_template.png', 0)
        self.tl_offset = tl_offset
        self.br_offset = br_offset
        self.method = method


# Calibration uses whichever style's top-left corner matches the screen best
MINIMAP_STYLES = [
    MinimapStyle('modern', 'assets', (9, 5), (-9, -9), cv2.TM_CCOEFF),
    MinimapStyle('old-style', 'assets/classic', (9, 11), (-9, -9), cv2.TM_CCOEFF_NORMED),
    MinimapStyle('old-style compact', 'assets/classic_compact', (7, 11), (-6, -9), cv2.TM_CCOEFF_NORMED)
]

# Minimum normalized match score of both corners, and minimum minimap width and height (px),
# for calibration to accept a minimap
MINIMAP_MIN_SCORE = 0.9
MINIMAP_MIN_SIZE = 20

MMT_HEIGHT = max(t.shape[0] for s in MINIMAP_STYLES for t in (s.tl_template, s.br_template))
MMT_WIDTH = max(t.shape[1] for s in MINIMAP_STYLES for t in (s.tl_template, s.br_template))

# The player's symbol on the minimap
PLAYER_TEMPLATE = cv2.imread('assets/player_template.png', 0)
PT_HEIGHT, PT_WIDTH = PLAYER_TEMPLATE.shape


class Capture:
    """
    A class that tracks player position and various in-game events. It constantly updates
    the config module with information regarding these events. It also annotates and
    displays the minimap in a pop-up window.
    """

    def __init__(self):
        """Initializes this Capture object's main thread."""

        config.capture = self

        self.frame = None
        self.minimap = {}
        self.minimap_ratio = 1
        self.minimap_sample = None
        self.minimap_style = None
        self.warned_no_minimap = False
        self.sct = None
        self.window = {
            'left': 0,
            'top': 0,
            'width': 1366,
            'height': 768
        }

        self.ready = False
        self.calibrated = False
        self.thread = threading.Thread(target=self._main)
        self.thread.daemon = True

    def start(self):
        """Starts this Capture's thread."""

        print('\n[~] Started video capture')
        self.thread.start()

    def _main(self):
        """Constantly monitors the player's position and in-game events."""

        mss.windows.CAPTUREBLT = 0
        while True:
            # Calibrate screen capture
            handle = user32.FindWindowW(None, 'MapleStory')
            rect = wintypes.RECT()
            user32.GetWindowRect(handle, ctypes.pointer(rect))
            rect = (rect.left, rect.top, rect.right, rect.bottom)
            rect = tuple(max(0, x) for x in rect)

            self.window['left'] = rect[0]
            self.window['top'] = rect[1]
            self.window['width'] = max(rect[2] - rect[0], MMT_WIDTH)
            self.window['height'] = max(rect[3] - rect[1], MMT_HEIGHT)

            # Calibrate by finding the top-left and bottom-right corners of the minimap
            with mss.mss() as self.sct:
                self.frame = self.screenshot()
            if self.frame is None:
                continue
            found = find_minimap(self.frame)
            if found is None:       # Wait for a real minimap, e.g. while another window covers the game
                if not self.warned_no_minimap:
                    print('\n[!] Minimap not found, make sure the game window is visible '
                          'and not covered by other windows')
                    self.warned_no_minimap = True
                time.sleep(1)
                continue
            self.warned_no_minimap = False
            style, mm_tl, mm_br = found
            if style is not self.minimap_style:
                print(f'\n[~] Detected {style.name} minimap')
                self.minimap_style = style
            self.minimap_ratio = (mm_br[0] - mm_tl[0]) / (mm_br[1] - mm_tl[1])
            self.minimap_sample = self.frame[mm_tl[1]:mm_br[1], mm_tl[0]:mm_br[0]]
            self.calibrated = True

            with mss.mss() as self.sct:
                while True:
                    if not self.calibrated:
                        break

                    #force recalibrate if moved or window size changed
                    handle2 = user32.FindWindowW(None, 'MapleStory')
                    rect2 = wintypes.RECT()
                    user32.GetWindowRect(handle2, ctypes.pointer(rect2))
                    rect2 = (rect2.left, rect2.top, rect2.right, rect2.bottom)
                    if rect2 != rect:
                        time.sleep(1)
                        break

                    # Take screenshot
                    self.frame = self.screenshot()
                    if self.frame is None:
                        continue

                    # Crop the frame to only show the minimap
                    minimap = self.frame[mm_tl[1]:mm_br[1], mm_tl[0]:mm_br[0]]

                    # Determine the player's position
                    player = utils.multi_match(minimap, PLAYER_TEMPLATE, threshold=0.8)
                    if player:
                        config.player_pos = utils.convert_to_relative(player[0], minimap)

                    # Package display information to be polled by GUI
                    self.minimap = {
                        'minimap': minimap,
                        'rune_active': config.bot.rune_active,
                        'rune_pos': config.bot.rune_pos,
                        'path': config.path,
                        'player_pos': config.player_pos
                    }

                    if not self.ready:
                        self.ready = True
                    time.sleep(0.001)

    def screenshot(self, delay=1):
        try:
            return np.array(self.sct.grab(self.window))
        except mss.exception.ScreenShotError:
            print(f'\n[!] Error while taking screenshot, retrying in {delay} second'
                  + ('s' if delay != 1 else ''))
            time.sleep(delay)


def find_minimap(frame):
    """
    Finds the minimap in FRAME using whichever minimap style's top-left corner matches best.
    :param frame:   A screenshot of the game window.
    :return:        A tuple of (style, top-left, bottom-right) bounds of the minimap's contents,
                    or None if no minimap is confidently found.
    """

    style = max(MINIMAP_STYLES, key=lambda s: utils.match_score(frame, s.tl_template))
    if min(utils.match_score(frame, style.tl_template),
           utils.match_score(frame, style.br_template)) < MINIMAP_MIN_SCORE:
        return None
    tl, _ = utils.single_match(frame, style.tl_template, style.method)
    _, br = utils.single_match(frame, style.br_template, style.method)
    mm_tl = (tl[0] + style.tl_offset[0], tl[1] + style.tl_offset[1])
    mm_br = (br[0] + style.br_offset[0], br[1] + style.br_offset[1])
    if mm_br[0] < mm_tl[0] + MINIMAP_MIN_SIZE or mm_br[1] < mm_tl[1] + MINIMAP_MIN_SIZE:
        return None
    return style, mm_tl, mm_br
