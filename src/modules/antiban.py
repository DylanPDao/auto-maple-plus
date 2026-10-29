"""
Human-like pacing for the main bot loop: randomly timed play sessions and breaks, plus
optional small idle pauses and jumps between routine steps.
"""

import time
import random
from src.common import config, utils
from src.common.interfaces import Configurable
from src.common.vkeys import press, key_down, key_up
from src.runesolvercore.runesolver import INSIDE_CS_TEMPLATE


# Gaps longer than this between ticks mean the bot was paused, so they don't count as play time
MAX_TICK_GAP = 5

# Per-step chance of each small behaviour
IDLE_CHANCE = 0.02
JUMP_CHANCE = 0.01

# Seconds between fidgets (turning around, crouching, hesitating), picked at random each time
FIDGET_GAP = (15, 45)


class AntiBan(Configurable):
    DEFAULT_CONFIG = {
        'take_breaks': False,
        'play_time_min': 90,        # Minutes of play before a break
        'play_time_max': 240,
        'break_time_min': 20,       # Minutes per break
        'break_time_max': 90,
        'break_in_cash_shop': True,
        'idle_pauses': False,
        'random_jumps': False,
        'fidgets': False
    }

    def __init__(self):
        super().__init__('antiban')
        self.play_time = 0
        self.play_limit = None
        self.last_tick = None
        self.next_fidget = time.time() + random.uniform(*FIDGET_GAP)

    def number(self, key):
        """Returns setting KEY as a float, falling back to its default if it is invalid."""

        try:
            return float(self.config[key])
        except (TypeError, ValueError):
            return float(self.DEFAULT_CONFIG[key])

    def time_until_break(self):
        """Returns the seconds of play left before the next break, or None if breaks are off."""

        if not self.config['take_breaks'] or self.play_limit is None:
            return None
        return max(0, self.play_limit - self.play_time)

    def tick(self):
        """
        Called once per routine step while the bot is enabled.
        :return:    True if a break was taken, meaning the caller should restart its loop.
        """

        now = time.time()
        if self.last_tick is not None:
            self.play_time += min(now - self.last_tick, MAX_TICK_GAP)
        self.last_tick = now
        if self.play_limit is None:
            self._schedule_break()
        self._show_next_break()

        if self.config['take_breaks'] and self.play_time >= self.play_limit:
            self._take_break()
            self._schedule_break()
            self.last_tick = time.time()
            return True

        if self.config['idle_pauses'] and utils.bernoulli(IDLE_CHANCE):
            time.sleep(utils.rand_float(0.5, 2.5))
        if self.config['random_jumps'] and utils.bernoulli(JUMP_CHANCE):
            press(self._jump_key(), 1)
        if self.config['fidgets'] and now >= self.next_fidget:
            self._fidget()
            self.next_fidget = time.time() + random.uniform(*FIDGET_GAP)
        return False

    def _show_next_break(self):
        """Shows the play time left before the next break in the View tab."""

        left = self.time_until_break()
        if left is None:
            text = 'Breaks off'
        else:
            hours, minutes = divmod(int(left // 60), 60)
            text = f'in {hours}h {minutes:02d}m of play' if hours else f'in {minutes}m of play'
        try:
            config.gui.view.monitoringconsole.set_breakstat(text)
        except AttributeError:      # No GUI yet
            pass

    @staticmethod
    def _fidget():
        """
        Does one small harmless thing a player might: turn around, crouch for a moment or
        hesitate. Keys that could be bound to something (items, menus) are never pressed.
        """

        kind = random.choice(('turn', 'crouch', 'hesitate'))
        if kind == 'turn':
            press(random.choice(('left', 'right')), 1, down_time=0.05)
        elif kind == 'crouch':
            key_down('down')
            time.sleep(random.uniform(0.2, 0.6))
            key_up('down')
        else:
            time.sleep(random.uniform(0.3, 1.2))

    def _schedule_break(self):
        low, high = sorted((self.number('play_time_min'), self.number('play_time_max')))
        self.play_time = 0
        self.play_limit = utils.rand_float(low, high + 1e-6) * 60

    def _take_break(self):
        low, high = sorted((self.number('break_time_min'), self.number('break_time_max')))
        duration = utils.rand_float(low, high + 1e-6) * 60
        resume_at = time.time() + duration
        in_cash_shop = self.config['break_in_cash_shop'] and self._enter_cash_shop()
        print(f"\n[~] Anti-ban: taking a {duration / 60:.0f} minute break"
              f"{' in the cash shop' if in_cash_shop else ''}, resuming at "
              f"{time.strftime('%I:%M %p', time.localtime(resume_at))}")

        config.on_break = True
        config.enabled = False
        config.gui.view.monitoringconsole.set_enabledstat(False)
        config.gui.view.monitoringconsole.set_breakstat(
            f"On break until {time.strftime('%I:%M %p', time.localtime(resume_at))}")
        try:
            while time.time() < resume_at and not config.enabled:
                time.sleep(1)
            resumed_by_user = config.enabled
        finally:
            config.on_break = False

        if not resumed_by_user:
            config.enabled = True
            config.gui.view.monitoringconsole.set_enabledstat(True)
        if in_cash_shop:
            self._exit_cash_shop()
        config.listener.recalibrate_minimap()
        print('\n[~] Anti-ban: break over, resuming routine')

    def _enter_cash_shop(self, attempts=3):
        """Opens the cash shop. Returns whether the player ended up inside it."""

        key = config.bot.config['Cash Shop']
        for _ in range(attempts):
            if not config.enabled:
                return False
            press(key, 1)
            time.sleep(3)
            if self._in_cash_shop():
                return True
            press('esc', 1)
            time.sleep(0.5)
        print('[!] Anti-ban: could not enter the cash shop, taking the break in place')
        return False

    def _exit_cash_shop(self, attempts=10):
        for _ in range(attempts):
            if not config.enabled or not self._in_cash_shop():
                return
            press('esc', 1)
            time.sleep(0.5)
            press('esc', 1)
            time.sleep(0.5)
            press('enter', 1)
            time.sleep(2.5)

    @staticmethod
    def _in_cash_shop():
        frame = config.capture.frame
        return frame is not None and utils.multi_match(frame, INSIDE_CS_TEMPLATE, threshold=0.9) != []

    @staticmethod
    def _jump_key():
        command_book = config.bot.command_book
        if command_book is not None and hasattr(command_book.module.Key, 'JUMP'):
            return command_book.module.Key.JUMP
        return 'space'
