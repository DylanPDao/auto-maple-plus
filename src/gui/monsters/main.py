"""
The Combat tab: captures nametag, monster and drop templates from game screenshots, manages
monster sets, and holds the combat, skill rotation, movement and anti-ban settings.
"""

import os
import cv2
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog, filedialog
from PIL import ImageTk, Image
from src.common import config
from src.gui.interfaces import Tab, Frame, LabelFrame
import numpy as np
from src.modules.combat import draw_detection, clean_name, cut_out, trim_nametag, text_template, \
    PLAYER_TEMPLATE_PATH
from src.modules.recorder import RECORDINGS_DIR
from src.gui.settings.combat import CombatSettings, SkillRotationSettings, PotSaveSettings, MovementSettings, \
    AntiBanSettings
from src.modules.combat import TOWN_MARKER_PATH


UNGROUPED = '(ungrouped)'
THUMB_SIZE = 64


class Monsters(Tab):
    CANVAS_WIDTH = 720
    CANVAS_HEIGHT = 405

    def __init__(self, parent, **kwargs):
        super().__init__(parent, 'Combat', **kwargs)

        self.image = None           # Full resolution BGR screenshot
        self.scale = 1
        self.selection = None       # Crop box (x1, y1, x2, y2) in screenshot pixels
        self.cutout = None          # BGRA monster with its background removed, or None
        self.drag_start = None
        self.thumbs = []            # Keeps thumbnail PhotoImages from being garbage collected

        self.columnconfigure(0, weight=1)
        self.columnconfigure(4, weight=1)
        self._build_screenshot_panel()
        self._build_set_panel()
        self._build_settings_column()
        self.refresh()

    #################################
    #            Layout             #
    #################################
    def _build_screenshot_panel(self):
        panel = LabelFrame(self, 'Screenshot')
        panel.grid(row=0, column=1, sticky=tk.N, padx=10, pady=10)

        toolbar = Frame(panel)
        toolbar.pack(side=tk.TOP, fill='x', padx=5, pady=5)
        tk.Button(toolbar, text='Capture', command=lambda: self._capture(0)).pack(side=tk.LEFT)
        tk.Button(toolbar, text='Capture in 3s', command=lambda: self._capture(3000)).pack(side=tk.LEFT, padx=(5, 0))
        tk.Button(toolbar, text='Open image...', command=self._open_image).pack(side=tk.LEFT, padx=(5, 0))
        self.show_matches = tk.BooleanVar(value=False)
        tk.Checkbutton(toolbar, text='Show matches', variable=self.show_matches,
                       command=self._render).pack(side=tk.LEFT, padx=(15, 0))
        self.status = tk.StringVar(value='Capture the game screen, then drag a box around a monster or your nametag.')
        tk.Label(panel, textvariable=self.status, anchor=tk.W).pack(side=tk.TOP, fill='x', padx=5)

        self.canvas = tk.Canvas(panel, bg='black', width=self.CANVAS_WIDTH, height=self.CANVAS_HEIGHT,
                                borderwidth=0, highlightthickness=0, cursor='crosshair')
        self.canvas.pack(side=tk.TOP, padx=5, pady=5)
        self.canvas_image = None
        self.canvas_rect = None
        self.canvas.bind('<ButtonPress-1>', self._on_press)
        self.canvas.bind('<B1-Motion>', self._on_drag)
        self.canvas.bind('<ButtonRelease-1>', self._on_release)

        bottom = Frame(panel)
        bottom.pack(side=tk.TOP, fill='x', padx=5, pady=(0, 5))
        self.preview = tk.Label(bottom, text='No selection', width=28, height=6, relief=tk.SUNKEN)
        self.preview.pack(side=tk.LEFT)
        form = Frame(bottom)
        form.pack(side=tk.LEFT, padx=10, anchor=tk.N)
        tk.Label(form, text='Monster name:').grid(row=0, column=0, sticky=tk.W)
        self.name_var = tk.StringVar()
        name_entry = tk.Entry(form, textvariable=self.name_var, width=24)
        name_entry.grid(row=0, column=1, padx=5)
        name_entry.bind('<Return>', lambda _: self._save_mob())
        tk.Button(form, text='Save as monster', command=self._save_mob).grid(row=1, column=1, sticky=tk.EW, padx=5, pady=(5, 0))
        tk.Button(form, text='Save as drop', command=self._save_drop).grid(row=2, column=1, sticky=tk.EW, padx=5, pady=(5, 0))
        tk.Button(form, text='Save as my nametag', command=self._save_player).grid(row=3, column=1, sticky=tk.EW, padx=5, pady=(5, 0))
        tk.Button(form, text='Save as town marker', command=self._save_town_marker).grid(row=4, column=1, sticky=tk.EW, padx=5, pady=(5, 0))
        tk.Label(form, justify=tk.LEFT, fg='gray25',
                 text='Box the whole monster or drop with a little room around it,\n'
                      'the background is cut out (the preview shows what is kept).\n'
                      'Saving a name again adds another pose. For your nametag,\n'
                      'box the name, only its white letters are used. For the town\n'
                      'marker, box something only seen in town (e.g. an NPC).'
                 ).grid(row=5, column=0, columnspan=2, sticky=tk.W, pady=(8, 0))

    def _build_set_panel(self):
        panel = LabelFrame(self, 'Monster set')
        panel.grid(row=0, column=2, sticky=tk.NS, padx=10, pady=10)

        row = Frame(panel)
        row.pack(side=tk.TOP, fill='x', padx=5, pady=5)
        self.set_var = tk.StringVar()
        self.set_box = ttk.Combobox(row, textvariable=self.set_var, state='readonly', width=24)
        self.set_box.pack(side=tk.LEFT)
        self.set_box.bind('<<ComboboxSelected>>', self._on_set_selected)
        tk.Button(row, text='New set', command=self._new_set).pack(side=tk.LEFT, padx=(5, 0))
        self.delete_set_button = tk.Button(row, text='Delete set', command=self._delete_set)
        self.delete_set_button.pack(side=tk.LEFT, padx=(5, 0))
        tk.Label(panel, justify=tk.LEFT, fg='gray25',
                 text="A set named like a routine file is picked\nautomatically when that routine loads."
                 ).pack(side=tk.TOP, anchor=tk.W, padx=5)

        nametag = Frame(panel)
        nametag.pack(side=tk.TOP, fill='x', padx=5, pady=(10, 5))
        tk.Label(nametag, text='Your nametag:').pack(side=tk.LEFT)
        self.nametag_label = tk.Label(nametag, text='not set')
        self.nametag_label.pack(side=tk.LEFT, padx=5)

        tk.Label(panel, text='Monsters in this set:').pack(side=tk.TOP, anchor=tk.W, padx=5)
        self.list_canvas, self.list_frame = scrolling_frame(panel, 260, 330)
        tk.Label(panel, text='Drops (shared by all sets):').pack(side=tk.TOP, anchor=tk.W, padx=5)
        self.drops_canvas, self.drops_frame = scrolling_frame(panel, 260, 170)

    def _build_settings_column(self):
        self.settings_canvas, column = scrolling_frame(self, 380, 740, grid=(0, 3), fit_width=True)
        self.combat_settings = CombatSettings(column)
        self.combat_settings.pack(side=tk.TOP, fill='x', expand=True)
        for panel in (SkillRotationSettings, PotSaveSettings, MovementSettings, AntiBanSettings):
            panel(column).pack(side=tk.TOP, fill='x', expand=True, pady=(10, 0))

    #################################
    #        Monster Sets           #
    #################################
    def refresh(self):
        """Reloads the set list, nametag and monster thumbnails from disk."""

        combat = config.bot.combat
        sets = combat.list_sets()
        current = combat.config['mob_set']
        if current and current not in sets:
            current = ''
        self.set_box['values'] = [UNGROUPED] + sets
        self.set_var.set(current or UNGROUPED)
        self.delete_set_button.config(state=tk.NORMAL if current else tk.DISABLED)

        self.thumbs = []
        if os.path.isfile(PLAYER_TEMPLATE_PATH):
            thumb = self._thumbnail(cv2.imread(PLAYER_TEMPLATE_PATH))
            self.nametag_label.config(image=thumb, text='')
        else:
            self.nametag_label.config(image='', text='not set')

        self._fill_list(self.list_frame, combat.list_mobs(current), 'No monsters yet', 'monster')
        self._fill_list(self.drops_frame, combat.list_drops(), 'No drops yet, the bot sweeps\n'
                                                               'over kills blindly until some are saved', 'drop')
        if hasattr(self, 'combat_settings'):
            self.combat_settings._reload()
        self._render()

    def _fill_list(self, frame, items, empty_text, kind):
        """Shows ITEMS ((name, path) pairs) as thumbnail rows with delete buttons in FRAME."""

        for child in frame.winfo_children():
            child.destroy()
        if not items:
            tk.Label(frame, text=empty_text, fg='gray40', justify=tk.LEFT).pack(anchor=tk.W)
        for name, path in items:
            row = Frame(frame)
            row.pack(side=tk.TOP, fill='x', pady=2)
            tk.Label(row, image=self._thumbnail(cv2.imread(path, cv2.IMREAD_UNCHANGED)),
                     width=THUMB_SIZE).pack(side=tk.LEFT)
            tk.Label(row, text=name, anchor=tk.W, width=16).pack(side=tk.LEFT, padx=5)
            tk.Button(row, text='Delete',
                      command=lambda p=path, n=name: self._delete_mob(p, n, kind)).pack(side=tk.RIGHT)

    def _on_set_selected(self, _=None):
        value = self.set_var.get()
        config.bot.combat.use_set('' if value == UNGROUPED else value)
        self.refresh()

    def _new_set(self):
        default = ''
        if config.routine is not None and config.routine.path:
            default = os.path.splitext(os.path.basename(config.routine.path))[0]
        name = simpledialog.askstring('New monster set', 'Set name (use the routine file name to\n'
                                      'have it picked automatically):', initialvalue=default, parent=self)
        name = config.bot.combat.create_set(name or '')
        if name:
            config.bot.combat.use_set(name)
            self.refresh()

    def _delete_set(self):
        name = self.set_var.get()
        if name == UNGROUPED:
            return
        count = len(config.bot.combat.list_mobs(name))
        if messagebox.askyesno('Delete monster set',
                               f"Delete the set '{name}' and its {count} monster image(s)?\n"
                               f"This can't be undone.", icon=messagebox.WARNING, parent=self):
            config.bot.combat.delete_set(name)
            self.status.set(f"Deleted set '{name}'.")
            self.refresh()

    def _delete_mob(self, path, name, kind='monster'):
        where = 'from this set' if kind == 'monster' else 'from the drops'
        if messagebox.askyesno(f'Delete {kind}', f"Delete '{name}' {where}?", parent=self):
            config.bot.combat.delete_mob(path)
            self.refresh()

    #################################
    #          Screenshot           #
    #################################
    def _capture(self, delay_ms):
        """Hides Auto Maple so it doesn't cover the game, then grabs the latest game frame."""

        root = self.winfo_toplevel()
        root.iconify()
        self.after(delay_ms + 400, lambda: self._finish_capture(root))

    def _finish_capture(self, root):
        frame = config.capture.frame
        root.deiconify()
        if frame is None:
            messagebox.showerror('Capture failed', 'No game frame yet, is MapleStory open?', parent=self)
            return
        self._set_image(cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR) if frame.shape[2] == 4 else frame.copy())

    def _open_image(self):
        path = filedialog.askopenfilename(parent=self, title='Open screenshot',
                                          initialdir=RECORDINGS_DIR if os.path.isdir(RECORDINGS_DIR) else '.',
                                          filetypes=[('Images', '*.png *.jpg *.jpeg *.bmp')])
        if path:
            image = cv2.imread(path, cv2.IMREAD_COLOR)
            if image is None:
                messagebox.showerror('Open failed', f"Could not read '{path}'", parent=self)
            else:
                self._set_image(image)

    def _set_image(self, image):
        self.image = image
        self.selection = None
        self.preview.config(image='', text='No selection')
        self._render()

    def _render(self):
        """Draws the screenshot (with detections if 'Show matches' is on) scaled to the canvas."""

        if self.image is None:
            return
        img = self.image
        if self.show_matches.get():
            combat = config.bot.combat
            combat.last_player = None
            img = draw_detection(img, combat.detect(img, whole_frame=True), combat)
        h, w = img.shape[:2]
        self.scale = min(self.CANVAS_WIDTH / w, self.CANVAS_HEIGHT / h)
        img = cv2.resize(img, (int(w * self.scale), int(h * self.scale)), interpolation=cv2.INTER_AREA)
        photo = ImageTk.PhotoImage(Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB)))
        if self.canvas_image is None:
            self.canvas_image = self.canvas.create_image(0, 0, image=photo, anchor=tk.NW)
        else:
            self.canvas.itemconfig(self.canvas_image, image=photo)
        self._photo = photo
        self._draw_selection()

    def _on_press(self, event):
        if self.image is not None:
            self.drag_start = (event.x, event.y)

    def _on_drag(self, event):
        if self.drag_start is None:
            return
        self.selection = self._to_image_box(self.drag_start, (event.x, event.y))
        self._draw_selection()

    def _on_release(self, event):
        if self.drag_start is None:
            return
        self.selection = self._to_image_box(self.drag_start, (event.x, event.y))
        self.drag_start = None
        x1, y1, x2, y2 = self.selection
        self.cutout = None
        if x2 - x1 < 4 or y2 - y1 < 4:
            self.selection = None
            self.preview.config(image='', text='Selection too small')
        else:
            self.cutout = cut_out(self.image, self.selection)
            self._show_preview()
            if self.cutout is None:
                self.status.set(f"Selected {x2 - x1}x{y2 - y1} px. Couldn't separate a monster from the "
                                f"background, saving keeps the whole box.")
            else:
                self.status.set(f'Selected {x2 - x1}x{y2 - y1} px, background removed (preview). '
                                f'Name it and save, or drag again.')
        self._draw_selection()

    def _to_image_box(self, a, b):
        h, w = self.image.shape[:2]
        xs = sorted(min(max(int(v / self.scale), 0), w) for v in (a[0], b[0]))
        ys = sorted(min(max(int(v / self.scale), 0), h) for v in (a[1], b[1]))
        return xs[0], ys[0], xs[1], ys[1]

    def _draw_selection(self):
        if self.canvas_rect is not None:
            self.canvas.delete(self.canvas_rect)
            self.canvas_rect = None
        if self.selection is not None:
            x1, y1, x2, y2 = (v * self.scale for v in self.selection)
            self.canvas_rect = self.canvas.create_rectangle(x1, y1, x2, y2, outline='#ff00ff', width=2)

    def _show_preview(self):
        crop = to_display(self.cutout if self.cutout is not None else self._crop())
        h, w = crop.shape[:2]
        zoom = max(1, min(200 // w, 100 // h, 4))
        crop = cv2.resize(crop, (w * zoom, h * zoom), interpolation=cv2.INTER_NEAREST)
        photo = ImageTk.PhotoImage(Image.fromarray(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)))
        self.preview.config(image=photo, text='', width=0, height=0)
        self._preview_photo = photo

    def _crop(self):
        x1, y1, x2, y2 = self.selection
        return self.image[y1:y2, x1:x2].copy()

    def _save_mob(self):
        self._save_template('monster')

    def _save_drop(self):
        self._save_template('drop')

    def _save_template(self, kind):
        """Saves the selection as a 'monster' (in the active set) or a 'drop' (shared)."""

        if self.selection is None:
            messagebox.showinfo('No selection', f'Drag a box around a {kind} first.', parent=self)
            return
        combat = config.bot.combat
        listing = combat.list_mobs(combat.config['mob_set']) if kind == 'monster' else combat.list_drops()
        base = clean_name(self.name_var.get()) or kind
        existing = {n for n, _ in listing}
        name = base if base != kind and base not in existing else None
        number = 2 if base != kind else 1
        while name is None:
            candidate = f'{base}_{number}'
            if candidate not in existing:
                name = candidate
            number += 1
        save = combat.save_mob if kind == 'monster' else combat.save_drop
        save(name, self.cutout if self.cutout is not None else self._crop())
        pose = f", another pose of '{base}'" if base in existing else ''
        self.status.set(f"Saved {kind} '{name}'{pose}. Drag around the next one.")
        self.selection = None
        self.cutout = None
        self.preview.config(image='', text='No selection')
        self.refresh()

    def _save_town_marker(self):
        """Saves the selection as what the watcher looks for to tell the player is in town."""

        if self.selection is None:
            messagebox.showinfo('No selection', 'Drag a box around something only seen in town first.',
                                parent=self)
            return
        if os.path.isfile(TOWN_MARKER_PATH) and \
                not messagebox.askyesno('Replace town marker', 'Replace the saved town marker?', parent=self):
            return
        cv2.imwrite(TOWN_MARKER_PATH, self._crop())
        self.status.set("Saved the town marker. Turn on 'Auto pause in town' in Automation to use it.")

    def _save_player(self):
        if self.selection is None:
            messagebox.showinfo('No selection', 'Drag a box around your nametag first.', parent=self)
            return
        crop = self._crop()
        if text_template(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)) is None and \
                not messagebox.askyesno('No name text found',
                                        'No white name text was found in the box, so the whole box '
                                        'will be matched, which is much less reliable.\nSave anyway?',
                                        parent=self):
            return
        if os.path.isfile(PLAYER_TEMPLATE_PATH) and \
                not messagebox.askyesno('Replace nametag', 'Replace your saved nametag?', parent=self):
            return
        config.bot.combat.save_player(trim_nametag(crop))
        self.status.set('Saved your nametag.')
        self.refresh()

    def _thumbnail(self, image):
        if image is None:
            return ''
        image = to_display(image)
        h, w = image.shape[:2]
        scale = min(THUMB_SIZE / w, THUMB_SIZE / h, 2)
        image = cv2.resize(image, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=cv2.INTER_AREA)
        photo = ImageTk.PhotoImage(Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB)))
        self.thumbs.append(photo)
        return photo


def scrolling_frame(parent, width, height, grid=None, fit_width=False):
    """
    Creates a fixed-height, vertically scrolling area in PARENT, packed or placed at GRID (row,
    column). Scrolls with the mouse wheel while the pointer is over it.
    :param fit_width:   Grow the area to fit its contents' width instead of clipping them.
    :return:            The canvas and the frame to put widgets in.
    """

    holder = Frame(parent)
    if grid is None:
        holder.pack(side=tk.TOP, fill='both', expand=True, padx=5, pady=5)
    else:
        holder.grid(row=grid[0], column=grid[1], sticky=tk.N, padx=10, pady=10)
    canvas = tk.Canvas(holder, width=width, height=height, borderwidth=0, highlightthickness=0)
    scrollbar = tk.Scrollbar(holder, orient=tk.VERTICAL, command=canvas.yview)
    inner = Frame(canvas)

    def resize(_):
        canvas.configure(scrollregion=canvas.bbox('all'))
        if fit_width:
            canvas.configure(width=inner.winfo_reqwidth())
    inner.bind('<Configure>', resize)
    canvas.create_window((0, 0), window=inner, anchor=tk.NW)
    canvas.configure(yscrollcommand=scrollbar.set)
    canvas.pack(side=tk.LEFT, fill='both', expand=True)
    scrollbar.pack(side=tk.RIGHT, fill='y')

    def scroll(event):
        canvas.yview_scroll(int(-event.delta / 120), 'units')
    holder.bind('<Enter>', lambda _: canvas.bind_all('<MouseWheel>', scroll))
    holder.bind('<Leave>', lambda _: canvas.unbind_all('<MouseWheel>'))
    return canvas, inner


def to_display(image):
    """Returns a BGR copy of IMAGE, drawing transparent areas as a checkerboard."""

    if image.ndim == 2:
        return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    if image.shape[2] == 3:
        return image
    h, w = image.shape[:2]
    ys, xs = np.mgrid[0:h, 0:w]
    checker = np.where(((ys // 6) + (xs // 6)) % 2 == 0, 200, 150).astype(np.uint8)
    checker = cv2.cvtColor(checker, cv2.COLOR_GRAY2BGR)
    alpha = image[:, :, 3:4] / 255.0
    return (image[:, :, :3] * alpha + checker * (1 - alpha)).astype(np.uint8)
