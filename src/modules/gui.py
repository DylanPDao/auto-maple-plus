"""User friendly GUI to interact with Auto Maple."""

import time
import threading
import tkinter as tk
from tkinter import ttk
from src.common import config, settings
from src.gui import Menu, View, Edit, Settings, Notifer_Settings, Runtime_Flags, Automation_Settings
from src.gui.menu.file import Import_Settings
from src.gui import theme

class GUI:
    DISPLAY_FRAME_RATE = 30
    RESOLUTIONS = {
        'DEFAULT': '800x800',
        'Edit': '1300x750',
        'Settings': '750x850',
        'Notifier': '600x775',
        'Monitoring': '400x575',
        'Automation': '400x525'
    }

    def __init__(self):
        config.gui = self

        self.root = tk.Tk()
        self.style = theme.apply(self.root)     # Before building any widgets, so they're themed
        self.root.title('Auto Maple')
        icon = tk.PhotoImage(file='assets/icon.png')
        self.root.iconphoto(False, icon)
        self.root.geometry(GUI.RESOLUTIONS['DEFAULT'])
        self.root.resizable(False, False)

        # Initialize GUI variables
        self.routine_var = tk.StringVar()

        # Build the GUI
        self.menu = Menu(self.root)
        self.root.config(menu=self.menu)

        self.navigation = ttk.Notebook(self.root)

        self.view = View(self.navigation)
        self.edit = Edit(self.navigation)
        self.settings = Settings(self.navigation)
        self.watcher_settings = Notifer_Settings(self.navigation)
        self.runtime_console = Runtime_Flags(self.navigation)
        self.automation_settings = Automation_Settings(self.navigation)

        self.navigation.pack(expand=True, fill='both')
        self.navigation.bind('<<NotebookTabChanged>>', self._resize_window)
        self.root.focus()

    def set_routine(self, arr):
        self.routine_var.set(arr)

    def clear_routine_info(self):
        """
        Clears information in various GUI elements regarding the current routine.
        Does not clear Listboxes containing routine Components, as that is handled by Routine.
        """

        self.view.details.clear_info()
        self.view.status.set_routine('')

        self.edit.minimap.redraw()
        self.edit.routine.commands.clear_contents()
        self.edit.routine.commands.update_display()
        self.edit.editor.reset()

    def _resize_window(self, e):
        """Callback to resize entire Tkinter window every time a new Page is selected."""

        nav = e.widget
        curr_id = nav.select()
        nav.nametowidget(curr_id).focus()      # Focus the current Tab
        page = nav.tab(curr_id, 'text')
        if self.root.state() != 'zoomed':
            if page in GUI.RESOLUTIONS:
                self.root.geometry(GUI.RESOLUTIONS[page])
            else:
                self.root.geometry(GUI.RESOLUTIONS['DEFAULT'])

    def start(self):
        """Starts the GUI as well as any scheduled functions."""

        display_thread = threading.Thread(target=self._display_minimap)
        display_thread.daemon = True
        display_thread.start()

        layout_thread = threading.Thread(target=self._save_layout)
        layout_thread.daemon = True
        layout_thread.start()

        # Load the built-in classic commands and the last used routine
        print()
        config.bot.load_classic_commands()
        last_routine = Import_Settings("CBR").get("last_routine")
        if last_routine is not None and config.bot.command_book is not None:
            try:
                config.routine.load(last_routine)
            except Exception:
                print(f"[!] Could not load last used routine '{last_routine}'")

        self.root.mainloop()

    def _display_minimap(self):
        delay = 1 / GUI.DISPLAY_FRAME_RATE
        while True:
            self.view.minimap.display_minimap()
            time.sleep(delay)

    def _save_layout(self):
        """Periodically saves the current Layout object."""

        while True:
            if config.layout is not None and settings.record_layout:
                config.layout.save()
            time.sleep(5)


if __name__ == '__main__':
    gui = GUI()
    gui.start()
