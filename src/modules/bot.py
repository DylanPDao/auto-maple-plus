"""An interpreter that reads and executes user-created routines."""

import threading
import time
import git
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
        self.submodules = []
        self.command_book = None            # CommandBook instance
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

        self.update_submodules()
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
                element = config.routine[config.routine.index]
                if config.rune_cd == False:
                    if self.rune_active and isinstance(element, Point) \
                            and element.location == self.rune_closest_pos:
                        self._solve_rune()
                element.execute()
                config.routine.step()
            else:
                time.sleep(0.01)

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

    def update_submodules(self, force=False):
        """
        Syncs each submodule to the commit pinned by this repository. Upstream's
        main branch is older than the pinned commit and lacks files this fork
        needs (e.g. watcher_scan_table.py), so it is never used. Local changes
        are kept unless FORCE is True, which overwrites them.
        """

        utils.print_separator()
        print('[~] Syncing submodules:')
        self.submodules = []
        repo = git.Repo.init()
        with open('.gitmodules', 'r') as file:
            lines = file.readlines()
            i = 0
            while i < len(lines):
                if lines[i].startswith('[') and i < len(lines) - 2:
                    path = lines[i + 1].split('=')[1].strip()
                    url = lines[i + 2].split('=')[1].strip()
                    self.submodules.append(path)
                    pinned = repo.git.rev_parse(f'HEAD:{path}')
                    try:
                        repo.git.clone(url, path)       # First time loading submodule
                        print(f" -  Initialized submodule '{path}'")
                    except git.exc.GitCommandError:
                        pass
                    sub_repo = git.Repo(path)
                    try:
                        sub_repo.git.cat_file('-e', f'{pinned}^{{commit}}')
                    except git.exc.GitCommandError:
                        sub_repo.git.fetch('origin', pinned)
                    if force:
                        sub_repo.git.reset('--hard', pinned)
                        print(f" -  Rebuilt submodule '{path}'")
                    elif sub_repo.head.commit.hexsha == pinned:
                        print(f" -  Submodule '{path}' is up to date")
                    else:
                        try:        # Carries over local changes that don't conflict
                            sub_repo.git.checkout(pinned)
                            print(f" -  Updated submodule '{path}'")
                        except git.exc.GitCommandError:
                            print(f" !  Could not update submodule '{path}', "
                                  f"local changes conflict with commit {pinned[:7]}")
                    i += 3
                else:
                    i += 1
