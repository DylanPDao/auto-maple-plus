"""Finds the game window's client area in screen coordinates."""

from __future__ import annotations

import ctypes
from ctypes import wintypes

user32 = ctypes.windll.user32
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor: real pixels, no scaling
except (AttributeError, OSError):
    user32.SetProcessDPIAware()


def find_window(title: str) -> int:
    hwnd = user32.FindWindowW(None, title)
    if not hwnd:
        raise RuntimeError(f"Window '{title}' not found, is the game open?")
    return hwnd


def client_rect(hwnd: int) -> tuple[int, int, int, int]:
    """(left, top, right, bottom) of HWND's client area in screen pixels."""

    rect = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    origin = wintypes.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(origin))
    return origin.x, origin.y, origin.x + rect.right, origin.y + rect.bottom


def is_foreground(hwnd: int) -> bool:
    return user32.GetForegroundWindow() == hwnd
