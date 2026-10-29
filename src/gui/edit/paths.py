"""Records movement paths taught by the player and adds them to routines as Path commands."""

import tkinter as tk
from tkinter import messagebox
from src.common import config
from src.gui.interfaces import LabelFrame, Frame
from src.modules.paths import list_paths, load_path, delete_path


class Paths(LabelFrame):
    def __init__(self, parent, **kwargs):
        super().__init__(parent, 'Recorded Paths', **kwargs)

        tk.Label(self, justify=tk.LEFT, fg='gray25',
                 text='Teach a route by doing it: stop the bot, press Record\n'
                      '(or the Record path key, F8 by default), move in the\n'
                      'game, then press it again. Pause briefly between moves.\n'
                      'Click a path to add it to the selected point. Move also\n'
                      'uses paths by itself to reach other levels.'
                 ).pack(side=tk.TOP, anchor=tk.W, padx=5, pady=(2, 0))

        row = Frame(self)
        row.pack(side=tk.TOP, fill='x', padx=5, pady=5)
        tk.Label(row, text='Name:').pack(side=tk.LEFT)
        self.path_name = tk.StringVar()
        tk.Entry(row, textvariable=self.path_name, width=16).pack(side=tk.LEFT, padx=5)
        self.record_text = tk.StringVar(value='Record')
        tk.Button(row, textvariable=self.record_text, command=self._toggle_recording).pack(side=tk.LEFT)

        row = Frame(self)
        row.pack(side=tk.TOP, fill='both', expand=True, padx=5, pady=(0, 5))
        scroll = tk.Scrollbar(row)
        scroll.pack(side=tk.RIGHT, fill='y')
        self.names = []
        self.listbox = tk.Listbox(row, width=25, height=5, exportselection=False,
                                  activestyle='none', yscrollcommand=scroll.set)
        self.listbox.bind('<<ListboxSelect>>', self.on_select)
        self.listbox.pack(side=tk.LEFT, expand=True, fill='both')
        scroll.config(command=self.listbox.yview)
        tk.Button(self, text='Delete selected path', command=self._delete).pack(side=tk.TOP, pady=(0, 5))
        self.refresh()

    def refresh(self):
        """Lists the saved paths and shows whether one is being recorded."""

        self.record_text.set('Stop recording' if config.bot.paths.recording else 'Record')
        self.names = list_paths()
        self.listbox.delete(0, 'end')
        for name in self.names:
            self.listbox.insert('end', name)

    def on_select(self, e):
        """Shows where the path starts and opens a pre-filled Path command to add to the routine."""

        selects = e.widget.curselection()
        command_book = config.bot.command_book
        if not selects or command_book is None or 'path' not in command_book:
            return
        name = self.names[int(selects[0])]
        path = load_path(name)
        if path is not None:
            self.parent.minimap.draw_point(tuple(path['start']))
        self.parent.record.clear_selection()
        self.parent.editor.create_add_ui(command_book['path'], kwargs={'name': name})

    def _toggle_recording(self):
        if config.enabled:
            messagebox.showinfo('Bot running', 'Stop the bot before recording a path.', parent=self)
            return
        config.bot.paths.toggle(self.path_name.get())
        self.refresh()

    def _delete(self):
        selects = self.listbox.curselection()
        if not selects:
            return
        name = self.names[int(selects[0])]
        if messagebox.askyesno('Delete path', f"Delete the path '{name}'?", parent=self):
            delete_path(name)
            self.refresh()

    def clear_selection(self):
        self.listbox.selection_clear(0, 'end')
