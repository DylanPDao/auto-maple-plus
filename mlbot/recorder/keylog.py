"""
Keyboard state via a low-level keyboard hook (WH_KEYBOARD_LL): every down/up edge is timestamped
when it happens, so short taps between two frames are never missed the way polling would miss them.
"""

from __future__ import annotations

import ctypes
import threading
import time
from collections.abc import Callable
from ctypes import wintypes

import numpy as np

from mlbot.keys import SIDED_TO_GENERIC

# Own DLL handles so the argtypes set here don't leak into other code using ctypes.windll
_user32 = ctypes.WinDLL('user32', use_last_error=True)
_kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)

WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x100, 0x101, 0x104, 0x105
WM_QUIT = 0x12
LLKHF_INJECTED = 0x10

LRESULT = ctypes.c_ssize_t
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [('vkCode', wintypes.DWORD), ('scanCode', wintypes.DWORD),
                ('flags', wintypes.DWORD), ('time', wintypes.DWORD),
                ('dwExtraInfo', ctypes.c_size_t)]


_user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
_user32.SetWindowsHookExW.restype = ctypes.c_void_p
_user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
_user32.CallNextHookEx.restype = LRESULT
_user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
_user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
_user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
_kernel32.GetModuleHandleW.restype = wintypes.HMODULE

# One key event: (perf_counter time, vk, is_down, was_injected_by_software)
KeyEvent = tuple[float, int, bool, bool]


class KeyLogger:
    def __init__(self) -> None:
        self.state = np.zeros(256, dtype=bool)  # generic + sided codes, True = held
        self._events: list[KeyEvent] = []
        self._lock = threading.Lock()
        self._on_press: dict[int, Callable[[], None]] = {}
        self._thread_id = 0
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._main, name='keylog', daemon=True)
        self._proc = HOOKPROC(self._hook)  # keep a reference or the callback gets collected

    def on_press(self, vk: int, callback: Callable[[], None]) -> None:
        """Calls CALLBACK (on the hook thread, keep it quick) when VK goes down."""

        self._on_press[vk] = callback

    def start(self) -> None:
        self._thread.start()
        if not self._ready.wait(2):
            raise RuntimeError('Keyboard hook did not start')

    def stop(self) -> None:
        if self._thread_id:
            _user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            self._thread.join(timeout=2)

    def snapshot(self) -> np.ndarray:
        """The held keys right now, as 256 bits packed into 32 bytes (index = VK code)."""

        return np.packbits(self.state)

    def drain_events(self) -> list[KeyEvent]:
        with self._lock:
            events, self._events = self._events, []
        return events

    def _hook(self, n_code: int, w_param: int, l_param: int) -> int:
        if n_code == 0:
            info = KBDLLHOOKSTRUCT.from_address(l_param)
            code = info.vkCode & 0xFF
            down = w_param in (WM_KEYDOWN, WM_SYSKEYDOWN)
            if w_param in (WM_KEYDOWN, WM_SYSKEYDOWN, WM_KEYUP, WM_SYSKEYUP) \
                    and self.state[code] != down:  # skip auto-repeat
                self._set(code, down, bool(info.flags & LLKHF_INJECTED))
                if down and code in self._on_press:
                    self._on_press[code]()
        return _user32.CallNextHookEx(None, n_code, w_param, l_param)

    def _set(self, code: int, down: bool, injected: bool) -> None:
        t = time.perf_counter()
        self.state[code] = down
        generic = SIDED_TO_GENERIC.get(code)
        if generic is not None:
            sides = [c for c, g in SIDED_TO_GENERIC.items() if g == generic]
            self.state[generic] = self.state[sides].any()
        with self._lock:
            self._events.append((t, code, down, injected))

    def _main(self) -> None:
        self._thread_id = _kernel32.GetCurrentThreadId()
        hook = _user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc,
                                         _kernel32.GetModuleHandleW(None), 0)
        if not hook:
            raise ctypes.WinError(ctypes.get_last_error())
        self._ready.set()
        msg = wintypes.MSG()
        try:
            while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                pass
        finally:
            _user32.UnhookWindowsHookEx(hook)
