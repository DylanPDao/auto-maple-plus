"""Settings panels for reactive combat and anti-ban behaviour."""

import time
import threading
import cv2
import tkinter as tk
from tkinter import ttk
from PIL import ImageTk, Image
from src.gui.interfaces import LabelFrame, Frame
from src.common import config
from src.modules.combat import draw_detection, attack_commands, SKILL_SLOTS
from src.modules.recorder import FrameRecorder


NO_COMMAND = '(none - use attack key)'


class SettingsPanel(LabelFrame):
    """A LabelFrame that edits a Configurable through checkboxes and text fields."""

    def __init__(self, parent, name, target, **kwargs):
        super().__init__(parent, name, **kwargs)
        self.target = target
        self.vars = {}

    def add_check(self, key, text):
        var = tk.BooleanVar(value=bool(self.target.get(key)))
        self.vars[key] = var
        row = Frame(self)
        row.pack(side=tk.TOP, fill='x', expand=True, pady=(5, 0), padx=5)
        tk.Checkbutton(row, variable=var, text=text, command=self._on_change).pack(side=tk.LEFT)

    def add_entry(self, key, text, width=8):
        var = tk.StringVar(value=str(self.target.get(key)))
        self.vars[key] = var
        row = Frame(self)
        row.pack(side=tk.TOP, fill='x', expand=True, pady=(5, 0), padx=5)
        tk.Label(row, text=text).pack(side=tk.LEFT)
        tk.Entry(row, textvariable=var, width=width).pack(side=tk.RIGHT)
        var.trace_add('write', lambda *_: self._on_change())

    def add_choice(self, key, text, options):
        """Adds a dropdown for KEY. OPTIONS is a list of (saved value, label shown) pairs."""

        labels = dict(options)
        values = {label: value for value, label in options}
        var = tk.StringVar(value=labels.get(self.target.get(key), options[0][1]))
        row = Frame(self)
        row.pack(side=tk.TOP, fill='x', expand=True, pady=(5, 0), padx=5)
        tk.Label(row, text=text).pack(side=tk.LEFT)
        box = ttk.Combobox(row, textvariable=var, values=[label for _, label in options],
                           state='readonly', width=24)
        box.pack(side=tk.RIGHT)

        def on_select(_=None):
            self.target.set(key, values[var.get()])
            self.target.save_config()
        box.bind('<<ComboboxSelected>>', on_select)

    def add_note(self, text):
        tk.Label(self, text=text, justify=tk.LEFT, fg='gray25').pack(side=tk.TOP, anchor=tk.W, padx=5, pady=(2, 0))

    def _on_change(self):
        for key, var in self.vars.items():
            self.target.set(key, var.get())
        self.target.save_config()


class CombatSettings(SettingsPanel):
    def __init__(self, parent, **kwargs):
        super().__init__(parent, 'Combat', config.bot.combat, **kwargs)

        self.add_check('enabled', 'Fight monsters between routine points')
        self.add_check('loot', 'Pick up drops after each kill')

        row = Frame(self)
        row.pack(side=tk.TOP, fill='x', expand=True, pady=(5, 0), padx=5)
        tk.Label(row, text='Attack command:').pack(side=tk.LEFT)
        self.command_var = tk.StringVar()
        self.command_box = ttk.Combobox(row, textvariable=self.command_var, state='readonly', width=18)
        self.command_box.pack(side=tk.RIGHT)
        self.command_box.bind('<<ComboboxSelected>>', self._on_command_selected)
        self.refresh_commands()

        self.add_entry('attack_key', 'Attack key (if no command):', width=14)
        self.add_entry('engage_range', 'Engage range (px):')
        self.add_entry('attack_range', 'Attack range (px):')
        self.add_entry('layer_height', 'Platform height tolerance (px):')
        self.add_entry('player_offset_y', 'Nametag to body offset (px):')
        self.add_entry('chase_timeout', 'Chase timeout (s):')
        self.add_entry('engage_timeout', 'Max fight time per point (s):')
        self.add_entry('give_up_cooldown', 'Cooldown after giving up (s):')
        self.add_entry('threshold', 'Match confidence (0-1):')
        self.add_entry('track_threshold', 'Keep-tracking confidence (0-1):')

        row = Frame(self)
        row.pack(side=tk.TOP, fill='x', expand=True, pady=5, padx=5)
        self.template_status = tk.StringVar()
        tk.Button(row, text='Reload templates', command=self._reload).pack(side=tk.LEFT)
        tk.Label(row, textvariable=self.template_status).pack(side=tk.LEFT, padx=(10, 0))
        self._reload()

        row = Frame(self)
        row.pack(side=tk.TOP, fill='x', expand=True, pady=(0, 5), padx=5)
        self.debug_window = None
        tk.Button(row, text='Debug view', command=self._open_debug).pack(side=tk.LEFT)
        self.recorder = FrameRecorder()
        self.record_text = tk.StringVar(value='Record frames')
        tk.Button(row, textvariable=self.record_text, command=self._toggle_recording).pack(side=tk.LEFT, padx=(10, 0))

    def refresh_commands(self):
        """Fills the attack command dropdown from the loaded command book."""

        commands = attack_commands(config.bot.command_book)
        self.command_box['values'] = [NO_COMMAND] + commands
        current = str(config.bot.combat.get('attack_command')).strip().lower()
        if current in commands:
            self.command_var.set(current)
        else:
            self.command_var.set(NO_COMMAND)
            if current and config.bot.command_book is not None:
                # Not a command this book has (e.g. a key typed in by mistake), so clear it
                config.bot.combat.set('attack_command', '')
                config.bot.combat.save_config()

    def _on_command_selected(self, _=None):
        value = self.command_var.get()
        config.bot.combat.set('attack_command', '' if value == NO_COMMAND else value)
        config.bot.combat.save_config()

    def _reload(self):
        has_player, num_mobs = config.bot.combat.load_templates()
        player = 'nametag ok' if has_player else 'no nametag'
        drops = len(config.bot.combat.drop_templates)
        self.template_status.set(f'{player}, {num_mobs} monsters, {drops} drops')

    def _open_debug(self):
        if self.debug_window is not None and self.debug_window.open:
            self.debug_window.lift()
        else:
            self.debug_window = DebugWindow(self)

    def _toggle_recording(self):
        if self.recorder.recording:
            self.recorder.stop()
            self.record_text.set('Record frames')
        else:
            self.recorder.start()
            self.record_text.set('Stop recording')


class DebugWindow(tk.Toplevel):
    """Live view of what combat detection sees on the game screen."""

    WIDTH = 800
    FRAME_RATE = 5

    def __init__(self, parent):
        super().__init__(parent)
        self.title('Combat debug view')
        self.open = True
        self.protocol('WM_DELETE_WINDOW', self._close)
        self.canvas = tk.Canvas(self, bg='black', width=self.WIDTH, height=self.WIDTH * 9 // 16,
                                borderwidth=0, highlightthickness=0)
        self.canvas.pack(expand=True, fill='both')
        self.status = tk.StringVar(value='Waiting for frames...')
        tk.Label(self, textvariable=self.status, anchor=tk.W).pack(fill='x', padx=5, pady=2)
        tk.Label(self, anchor=tk.W, text='cyan player · green monster in range · red ignored · '
                                         'magenta target · yellow platform band · orange attack range · '
                                         'white monster HP bar'
                 ).pack(fill='x', padx=5, pady=(0, 4))
        self.container = None
        self.latest = None              # (image, status) produced by the detection thread
        threading.Thread(target=self._main, daemon=True).start()
        self._poll()

    def _close(self):
        self.open = False
        self.destroy()

    def _main(self):
        """Runs detection off the GUI thread so slow frames don't freeze the window."""

        combat = config.bot.combat
        while self.open:
            frame = config.capture.frame
            if frame is not None:
                start = time.perf_counter()
                result = combat.detect(frame, whole_frame=True)
                elapsed = (time.perf_counter() - start) * 1000
                img = draw_detection(frame, result, combat)
                scale = self.WIDTH / img.shape[1]
                img = cv2.resize(img, (self.WIDTH, int(img.shape[0] * scale)), interpolation=cv2.INTER_AREA)
                self.latest = (cv2.cvtColor(img, cv2.COLOR_BGR2RGB), self._describe(result, elapsed))
            time.sleep(1 / self.FRAME_RATE)

    def _poll(self):
        """Shows the newest detection result. Runs on the GUI thread, as Tk requires."""

        if not self.open:
            return
        if self.latest is not None:
            img, status = self.latest
            self.latest = None
            self._show(img)
            self.status.set(status)
        self.after(1000 // self.FRAME_RATE, self._poll)

    def _show(self, img):
        img = ImageTk.PhotoImage(Image.fromarray(img))
        if self.container is None:
            self.canvas.config(height=img.height())
            self.container = self.canvas.create_image(0, 0, image=img, anchor=tk.NW)
        else:
            self.canvas.itemconfig(self.container, image=img)
        self._img = img                 # Prevent garbage collection

    @staticmethod
    def _describe(result, elapsed):
        if result is None:
            return 'Missing templates, see assets/combat/README.md'
        if result['player'] is None:
            return f"Player nametag not found · {len(result['mobs'])} monster(s) · {elapsed:.0f} ms"
        in_range = sum(1 for m in result['mobs'] if m[3])
        target = 'target locked' if result['target'] is not None else 'no target'
        return (f"Player {result['player_score']:.2f} · {len(result['mobs'])} monster(s), "
                f"{in_range} in range · {target} · {elapsed:.0f} ms")


class AntiBanSettings(SettingsPanel):
    def __init__(self, parent, **kwargs):
        super().__init__(parent, 'Anti-ban', config.bot.antiban, **kwargs)

        self.add_check('take_breaks', 'Take random breaks')
        self.add_entry('play_time_min', 'Play time min (minutes):')
        self.add_entry('play_time_max', 'Play time max (minutes):')
        self.add_entry('break_time_min', 'Break length min (minutes):')
        self.add_entry('break_time_max', 'Break length max (minutes):')
        self.add_check('break_in_cash_shop', 'Wait in the cash shop during breaks')
        self.add_check('idle_pauses', 'Random short idle pauses')
        self.add_check('random_jumps', 'Random jumps between points')
        self.add_check('fidgets', 'Random fidgets (turn around, crouch, hesitate)')


class SkillRotationSettings(SettingsPanel):
    """Up to SKILL_SLOTS skills cast in priority order whenever they're off cooldown."""

    def __init__(self, parent, **kwargs):
        super().__init__(parent, 'Skill rotation', config.bot.combat, **kwargs)

        self.add_note('Used top first whenever off cooldown, before the normal\n'
                      'attack. Leave a key empty to skip that slot.')
        for i in range(1, SKILL_SLOTS + 1):
            row = Frame(self)
            row.pack(side=tk.TOP, fill='x', expand=True, pady=(5, 0), padx=5)
            tk.Label(row, text=f'Skill {i} key:').pack(side=tk.LEFT)
            for key, label, width in ((f'skill{i}_key', None, 8), (f'skill{i}_cooldown', 'cooldown (s):', 6)):
                if label:
                    tk.Label(row, text=label).pack(side=tk.LEFT, padx=(10, 0))
                var = tk.StringVar(value=str(self.target.get(key)))
                self.vars[key] = var
                tk.Entry(row, textvariable=var, width=width).pack(side=tk.LEFT, padx=(5, 0))
                var.trace_add('write', lambda *_: self._on_change())
        self.add_entry('skill_cast_time', 'Skill cast time (s):')


class MovementSettings(SettingsPanel):
    def __init__(self, parent, **kwargs):
        super().__init__(parent, 'Movement', config.bot.combat, **kwargs)

        self.add_choice('movement', 'Route:', [('loop', 'Loop (in order)'),
                                               ('hunt', 'Hunt (where monsters are)')])
        self.add_note('Hunt treats the routine\'s points as places to hunt and picks\n'
                      'the next one by where monsters were found recently and how\n'
                      'long since it went there. Works best with 4+ points spread\n'
                      'over the map. Labels and jumps in the routine are ignored.\n'
                      'To reach other levels it takes recorded paths (Edit tab).')


class PotSaveSettings(SettingsPanel):
    """Rest instead of drinking HP potions, and the regular attack instead of skills at low MP."""

    def __init__(self, parent, **kwargs):
        super().__init__(parent, 'Pot save', config.bot.combat, **kwargs)

        self.add_check('pot_save', 'Pot save mode')
        self.add_note('Rests to heal instead of drinking HP potions (they are only\n'
                      'used below 15% HP), and uses the regular attack instead of\n'
                      'skills when MP is low, so no MP potions are needed.')
        self.add_entry('rest_below_hp', 'Rest when HP is below (%):')
        self.add_entry('rest_until_hp', 'Rest until HP is back to (%):')
        self.add_entry('max_rest_time', 'Longest rest (s):')
        self.add_entry('chair_key', 'Chair key (empty = stand still):', width=10)

        row = Frame(self)
        row.pack(side=tk.TOP, fill='x', expand=True, pady=(5, 0), padx=5)
        tk.Label(row, text='Rest spot:').pack(side=tk.LEFT)
        self.spot_text = tk.StringVar()
        tk.Label(row, textvariable=self.spot_text).pack(side=tk.LEFT, padx=5)
        tk.Button(row, text='Clear', command=self._clear_spot).pack(side=tk.RIGHT)
        tk.Button(row, text="Set to where I'm standing", command=self._set_spot).pack(side=tk.RIGHT, padx=(0, 5))
        self._show_spot()

        self.add_entry('skills_below_mp', 'Regular attack when MP is below (%):')
        self.add_entry('low_mp_command', 'Regular attack command:', width=10)

    def _set_spot(self):
        x, y = config.player_pos
        self.target.set('rest_spot', f'{x:.3f},{y:.3f}')
        self.target.save_config()
        self._show_spot()

    def _clear_spot(self):
        self.target.set('rest_spot', '')
        self.target.save_config()
        self._show_spot()

    def _show_spot(self):
        spot = self.target.rest_spot()
        self.spot_text.set(f'({spot[0]:.3f}, {spot[1]:.3f})' if spot else 'none, rests in place')
