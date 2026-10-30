"""Key names <-> Windows virtual-key codes."""

from __future__ import annotations

import string

VK: dict[str, int] = {
    'backspace': 0x08, 'tab': 0x09, 'enter': 0x0D, 'shift': 0x10, 'ctrl': 0x11, 'alt': 0x12,
    'pause': 0x13, 'capslock': 0x14, 'esc': 0x1B, 'space': 0x20, 'pageup': 0x21,
    'pagedown': 0x22, 'end': 0x23, 'home': 0x24, 'left': 0x25, 'up': 0x26, 'right': 0x27,
    'down': 0x28, 'insert': 0x2D, 'delete': 0x2E,
    'lshift': 0xA0, 'rshift': 0xA1, 'lctrl': 0xA2, 'rctrl': 0xA3, 'lalt': 0xA4, 'ralt': 0xA5,
    ';': 0xBA, '=': 0xBB, ',': 0xBC, '-': 0xBD, '.': 0xBE, '/': 0xBF, '`': 0xC0,
    '[': 0xDB, '\\': 0xDC, ']': 0xDD, "'": 0xDE,
}
VK.update({c: ord(c.upper()) for c in string.ascii_lowercase})
VK.update({c: ord(c) for c in string.digits})
VK.update({f'f{i}': 0x6F + i for i in range(1, 25)})

# Low-level hooks report sided modifiers; fold them into the generic code too.
SIDED_TO_GENERIC: dict[int, int] = {
    0xA0: 0x10, 0xA1: 0x10, 0xA2: 0x11, 0xA3: 0x11, 0xA4: 0x12, 0xA5: 0x12,
}

NAMES: dict[int, str] = {code: name for name, code in VK.items()}


def vk(name: str) -> int:
    try:
        return VK[name.lower()]
    except KeyError:
        raise ValueError(f"Unknown key name '{name}'") from None
