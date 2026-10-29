"""
The GUI's look. Importing ttkbootstrap makes plain tk widgets (Button, Entry, Listbox, ...)
take on the theme too, as long as the theme is applied before they are created.
"""

import tkinter.font as tkfont
import ttkbootstrap as tb
from ttkbootstrap.themes.standard import STANDARD_THEMES
from ttkbootstrap.style import ThemeDefinition


# A cartoon RPG palette: cream parchment panels, wood-brown text and borders, orange buttons
MAPLE = {
    'primary': '#F28C28',       # Buttons, selected tab
    'secondary': '#6B4423',
    'success': '#5DAA3B',
    'info': '#3A8EE6',
    'warning': '#FFC83D',
    'danger': '#E8414A',
    'light': '#FFF9E8',
    'dark': '#3B2A1A',
    'bg': '#FFF4D6',            # Parchment
    'fg': '#4A2E14',            # Dark brown text
    'selectbg': '#FFA94D',
    'selectfg': '#3B2A1A',
    'border': '#6B4423',        # Wood brown
    'inputfg': '#4A2E14',
    'inputbg': '#FFFDF5',
    'active': '#FFE4A8',
}

# 'maple', or any ttkbootstrap theme name, e.g. 'darkly', 'superhero' (dark) or 'flatly' (light)
THEME = 'maple'
FONT_FAMILY = 'Segoe UI'
FONT_SIZE = 9


def apply(root):
    """Applies the theme and font to ROOT. Call right after creating it, before any other widget."""

    style = tb.Style()
    if THEME == 'maple' and 'maple' not in style.theme_names():
        style.register_theme(ThemeDefinition('maple', MAPLE, 'light'))
    style.theme_use(THEME if THEME in style.theme_names() or THEME in STANDARD_THEMES else 'flatly')
    for name in ('TkDefaultFont', 'TkTextFont', 'TkMenuFont', 'TkHeadingFont', 'TkFixedFont'):
        tkfont.nametofont(name).configure(family=FONT_FAMILY, size=FONT_SIZE)
    root.configure(background=style.colors.bg)
    return style
