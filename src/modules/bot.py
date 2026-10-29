"""An interpreter that reads and executes user-created routines."""

import threading
import time
import cv2
import inspect
import importlib
import traceback
from interception import press
from src.common.vkeys import key_up
from os.path import splitext, basename
from src.common import config, utils
from src.routine import components
from src.routine.routine import Routine
from src.command_book.command_book import CommandBook, CB_KEYBINDING_DIR, CLASSIC_FILE, \
    CLASSIC_MODULE, CLASSIC_KEYBINDING_DIR
from src.routine.components import Point
from src.common.interfaces import Configurable
from src.modules.combat import Combat
from src.modules.antiban import AntiBan
from src.modules.hunt import HuntPlanner
from src.modules.paths import PathRecorder
from src.runesolvercore.runesolver import solve_rune_raw



# The rune's buff icon
RUNE_BUFF_TEMPLATE = cv2.imread('assets/rune_buff_template.jpg', 0)


class Bot(Configurable):
    """A class that interprets and executes user-defined routines."""

    DEFAULT_CONFIG = {
        'NPC/Gather': 'y',
        'Feed pet': '9',
        'Cash Shop': '`'
    }

    def __init__(self):
        """Loads a user-defined routine on start up and initializes this Bot's main thread."""

        super().__init__('keybindings')
        config.bot = self

        self.rune_active = False
        self.rune_pos = (0, 0)
        self.rune_closest_pos = (0, 0)      # Location of the Point closest to rune
        self.command_book = None            # CommandBook instance
        self.combat = Combat()              # Fights monsters between routine Points
        self.antiban = AntiBan()            # Breaks and human-like pauses
        self.hunt = HuntPlanner()           # Picks the next Point in hunt movement
        self.paths = PathRecorder()         # Records movement paths taught by the player
        # self.module_name = None
        # self.buff = components.Buff()

        # self.command_book = {}
        # for c in (components.Wait, components.Walk, components.Fall,
        #           components.Move, components.Adjust, components.Buff):
        #     self.command_book[c.__name__.lower()] = c

        config.routine = Routine()

        self.hwnd = None

        self.ready = False
        self.thread = threading.Thread(target=self._run)
        self.thread.daemon = True

    def start(self):
        """
        Starts this Bot object's thread.
        :return:    None
        """

        print('\n[~] Started main bot loop')
        self.thread.start()

    def _run(self):
        """
        Runs the main loop, and if it crashes, prints the error and disables the bot instead of
        leaving it shown as enabled with nothing running. The loop then restarts.
        """

        while True:
            try:
                self._main()
            except Exception:
                traceback.print_exc()
                print('\n[!] The bot hit an error and was disabled, see above')
                config.enabled = False
                for key in ('left', 'right', 'up', 'down'):     # Don't leave a held key stuck down
                    key_up(key)
                try:
                    config.gui.view.monitoringconsole.set_enabledstat(False)
                except Exception:
                    pass
                time.sleep(1)

    def _main(self):
        """
        The main body of Bot that executes the user's routine.
        :return:    None
        """
        self.ready = True
        config.listener.enabled = True
        last_fed = time.time()

        while True:
            if config.enabled and len(config.routine) > 0:
                if self.antiban.tick():
                    continue
                if self.combat.rest_if_needed():        # Pot save mode: rest at low HP
                    continue
                if self._pause_in_town():
                    continue

                # Buff and feed pets
                self.command_book.buff.main()
                pet_settings = config.gui.settings.pets
                auto_feed = pet_settings.auto_feed.get()
                num_pets = pet_settings.num_pets.get()

                #feed pets
                now = time.time()
                if auto_feed and now - last_fed > 1200 / num_pets:
                    press(self.config['Feed pet'], 1)
                    last_fed = now

                # Highlight the current Point
                config.gui.view.routine.select(config.routine.index)
                config.gui.view.details.display_info(config.routine.index)

                # Execute next Point in the routine
                index = config.routine.index
                element = config.routine[index]
                kills = self.combat.kills
                if isinstance(element, Point):
                    self.combat.engage()
                if config.rune_cd == False:
                    if self.rune_active and isinstance(element, Point) \
                            and element.location == self.rune_closest_pos:
                        self._solve_rune()
                element.execute()
                if self.combat.config['movement'] == 'hunt' and isinstance(element, Point):
                    # Hunt: go next wherever monsters have been found and it's been a while
                    self.hunt.record(index, self.combat.kills - kills)
                    if config.enabled:
                        config.routine.index = self.hunt.next_index(config.routine, index)
                else:
                    config.routine.step()
            else:
                time.sleep(0.01)

    @staticmethod
    def _pause_in_town():
        """
        Stops the bot when the town marker is on screen and 'Auto pause in town' is on, whether
        or not Discord notifications are set up.
        :return:    Whether the bot was stopped.
        """

        try:
            wanted = config.gui.automation_settings.automationSettings.auto_pause_in_town_toggle.get()
        except AttributeError:
            return False
        if not (config.char_in_town and wanted):
            return False
        print('\n[!] In town, pausing the bot')
        config.enabled = False
        config.gui.view.monitoringconsole.set_enabledstat(False)
        return True

    @utils.run_if_enabled
    def _solve_rune(self):
        """
        Moves to the position of the rune and solves the arrow-key puzzle.
        :param model:   The TensorFlow model to classify with.
        :param sct:     The mss instance object with which to take screenshots.
        :return:        None
        """
        
        move = self.command_book['move']
        move(*self.rune_pos).execute()
        adjust = self.command_book['adjust']
        adjust(*self.rune_pos).execute()
        time.sleep(0.5)
        print('\nSolving rune:')
        solve_rune_raw(self)
        self.rune_active = False

    def load_commands(self, file, module=None, keybinding_dir=CB_KEYBINDING_DIR):
        try:
            self.command_book = CommandBook(file, module, keybinding_dir)
            config.gui.settings.update_class_bindings()
        except ValueError:
            pass    # TODO: UI warning popup, say check cmd for errors

    def load_classic_commands(self):
        """Loads the built-in classic command book."""

        self.load_commands(CLASSIC_FILE, CLASSIC_MODULE, CLASSIC_KEYBINDING_DIR)
        #
        # utils.print_separator()
        # print(f"[~] Loading command book '{basename(file)}':")
        #
        # ext = splitext(file)[1]
        # if ext != '.py':
        #     print(f" !  '{ext}' is not a supported file extension.")
        #     return False
        #
        # new_step = components.step
        # new_cb = {}
        # for c in (components.Wait, components.Walk, components.Fall):
        #     new_cb[c.__name__.lower()] = c
        #
        # # Import the desired command book file
        # module_name = splitext(basename(file))[0]
        # target = '.'.join(['resources', 'command_books', module_name])
        # try:
        #     module = importlib.import_module(target)
        #     module = importlib.reload(module)
        # except ImportError:     # Display errors in the target Command Book
        #     print(' !  Errors during compilation:\n')
        #     for line in traceback.format_exc().split('\n'):
        #         line = line.rstrip()
        #         if line:
        #             print(' ' * 4 + line)
        #     print(f"\n !  Command book '{module_name}' was not loaded")
        #     return
        #
        # # Check if the 'step' function has been implemented
        # step_found = False
        # for name, func in inspect.getmembers(module, inspect.isfunction):
        #     if name.lower() == 'step':
        #         step_found = True
        #         new_step = func
        #
        # # Populate the new command book
        # for name, command in inspect.getmembers(module, inspect.isclass):
        #     new_cb[name.lower()] = command
        #
        # # Check if required commands have been implemented and overridden
        # required_found = True
        # for command in [components.Buff]:
        #     name = command.__name__.lower()
        #     if name not in new_cb:
        #         required_found = False
        #         new_cb[name] = command
        #         print(f" !  Error: Must implement required command '{name}'.")
        #
        # # Look for overridden movement commands
        # movement_found = True
        # for command in (components.Move, components.Adjust):
        #     name = command.__name__.lower()
        #     if name not in new_cb:
        #         movement_found = False
        #         new_cb[name] = command
        #
        # if not step_found and not movement_found:
        #     print(f" !  Error: Must either implement both 'Move' and 'Adjust' commands, "
        #           f"or the function 'step'")
        # if required_found and (step_found or movement_found):
        #     self.module_name = module_name
        #     self.command_book = new_cb
        #     self.buff = new_cb['buff']()
        #     components.step = new_step
        #     config.gui.menu.file.enable_routine_state()
        #     config.gui.view.status.set_cb(basename(file))
        #     config.routine.clear()
        #     print(f" ~  Successfully loaded command book '{module_name}'")
        # else:
        #     print(f" !  Command book '{module_name}' was not loaded")
