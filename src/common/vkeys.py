"""
Simulates keyboard input through the Interception driver. Keeps the interface of the
original vkeys module so that command books written for it continue to work.
"""

import time
import ctypes
import interception
from random import random
from src.common import config, utils


user32 = ctypes.windll.user32

# Title of the game window, the same one Capture looks for
GAME_WINDOW = 'MapleStory'

# How often (s) to check whether the game is active again while input is paused
FOCUS_CHECK_INTERVAL = 0.2


# Key names recorded by the 'keyboard' library (used by the keybinding editor)
# that Interception spells differently
KEY_ALIASES = {
    'page up': 'pageup',
    'page down': 'pagedown',
    'left shift': 'shiftleft',
    'right shift': 'shiftright',
    'left ctrl': 'ctrlleft',
    'right ctrl': 'ctrlright',
    'left alt': 'altleft',
    'right alt': 'altright',
    'alt gr': 'altright',
    'caps lock': 'capslock',
    'num lock': 'numlock',
    'scroll lock': 'scrolllock',
    'print screen': 'printscreen',
    'left windows': 'winleft',
    'right windows': 'winright'
}


def translate(key):
    """Converts KEY into the name Interception uses for it."""

    key = str(key).lower()
    return KEY_ALIASES.get(key, key)


def game_focused():
    """Returns whether the game window is the active window."""

    hwnd = user32.FindWindowW(None, GAME_WINDOW)
    return bool(hwnd) and user32.GetForegroundWindow() == hwnd


def wait_for_focus():
    """
    Interception sends keys to whichever window is active, so while the game isn't active
    (e.g. after alt-tabbing) this waits instead of typing into other programs.
    :return:    Whether the game is active, False if the bot was disabled while waiting.
    """

    if game_focused():
        return True
    print(f'\n[!] {GAME_WINDOW} is not the active window, pausing input until it is')
    while config.enabled and not game_focused():
        time.sleep(FOCUS_CHECK_INTERVAL)
    if config.enabled:
        print(f'[~] {GAME_WINDOW} is active again, resuming')
        return True
    return False


@utils.run_if_enabled
def key_down(key):
    """
    Simulates a key-down action. Can be cancelled by Bot.toggle_enabled. Waits while the game
    window isn't active so the key never reaches another program.
    :param key:     The key to press.
    :return:        None
    """

    if wait_for_focus():
        interception.key_down(translate(key), delay=0)


def key_up(key):
    """
    Simulates a key-up action. Cannot be cancelled by Bot.toggle_enabled.
    This is to ensure no keys are left in the 'down' state when the program pauses.
    :param key:     The key to press.
    :return:        None
    """

    interception.key_up(translate(key), delay=0)


@utils.run_if_enabled
def press(key, n=1, down_time=0.05, up_time=0.1):
    """
    Presses KEY N times, holding it for DOWN_TIME seconds, and releasing for UP_TIME seconds.
    :param key:         The keyboard input to press.
    :param n:           Number of times to press KEY.
    :param down_time:   Duration of down-press (in seconds).
    :param up_time:     Duration of release (in seconds).
    :return:            None
    """

    for _ in range(n):
        key_down(key)
        time.sleep(down_time * (0.8 + 0.4 * random()))
        key_up(key)
        time.sleep(up_time * (0.8 + 0.4 * random()))
