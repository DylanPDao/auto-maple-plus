"""
The built-in commands for classic (old-style UI) MapleStory, loaded automatically at startup.
Their keys are edited under Settings > Classic Keybindings.
"""

from src.common import config, settings
import time
from src.routine.components import Command
from src.common.vkeys import press, key_down, key_up


# Default key mappings, overridden by the saved Classic Keybindings
class Key:
    # Movement
    JUMP = 'alt'

    # Attacks
    ATTACK = 'ctrl'
    SKILL = 'shift'

    # Utility
    PICK_UP = 'z'
    HP_POTION = 'd'
    MP_POTION = 's'


# Potions are used when a bar falls below these fractions
HP_POTION_BELOW = 0.5
MP_POTION_BELOW = 0.3
POTION_COOLDOWN = 1.0       # Seconds between potions of the same kind

# Where the HP and MP bars are in a 1366x768 game window: the x range of each bar's inside and
# the row through them, counted from the bottom of the window
HP_BAR_X = (511, 616)
MP_BAR_X = (619, 724)
BAR_ROW_FROM_BOTTOM = 21


#########################
#        Helpers        #
#########################
def bar_fill(x_range, color):
    """
    Reads how full the HP ('red') or MP ('blue') bar is from the latest screenshot.
    :return:    The filled fraction from 0 to 1, or None if the bar isn't where it should be.
    """

    frame = config.capture.frame
    if frame is None or frame.shape[1] < x_range[1] or frame.shape[0] < BAR_ROW_FROM_BOTTOM:
        return None
    row = frame[frame.shape[0] - BAR_ROW_FROM_BOTTOM, x_range[0]:x_range[1], :3].astype(int)
    b, g, r = row[:, 0], row[:, 1], row[:, 2]
    if color == 'red':
        filled = (r > 150) & (g < 80) & (b < 80)
    else:
        filled = (b > 150) & (r < 80)
    empty = (abs(r - g) < 12) & (abs(g - b) < 12) & (r > 150) & (r < 230)     # Grey
    if (filled | empty).mean() < 0.9:       # Not a bar, e.g. covered or a different resolution
        return None
    return filled.mean()


last_potion = {'hp': 0, 'mp': 0}


def use_potions():
    """Drinks an HP or MP potion if that bar is low."""

    now = time.time()
    for kind, x_range, color, below, key in (('hp', HP_BAR_X, 'red', HP_POTION_BELOW, Key.HP_POTION),
                                             ('mp', MP_BAR_X, 'blue', MP_POTION_BELOW, Key.MP_POTION)):
        fill = bar_fill(x_range, color)
        if fill is not None and fill < below and now - last_potion[kind] > POTION_COOLDOWN:
            press(key, 1)
            last_potion[kind] = now


def vertical_step(direction):
    """
    Moves one step up or down while Auto Maple holds DIRECTION. Holding the arrow climbs a rope
    or ladder the player is on; if the player isn't moving, jumps instead, which grabs a rope
    above when going up and drops through the platform when going down.
    """

    start = config.player_pos[1]
    time.sleep(0.3)
    moved = config.player_pos[1] - start
    if (direction == 'up' and moved > -0.005) or (direction == 'down' and moved < 0.005):
        press(Key.JUMP, 1)
        time.sleep(0.4)


#########################
#       Commands        #
#########################
def step(direction, target):
    """
    Performs one movement step in the given DIRECTION towards TARGET.
    Should not press any arrow keys, as those are handled by Auto Maple.
    """

    use_potions()
    if direction in ('up', 'down'):
        vertical_step(direction)
    else:
        if target[1] - config.player_pos[1] < -settings.move_tolerance:
            press(Key.JUMP, 1)          # Target is higher, jump while walking
        time.sleep(0.3)


class Adjust(Command):
    """Fine-tunes player position using small movements."""

    def __init__(self, x, y, max_steps=5):
        super().__init__(locals())
        self.target = (float(x), float(y))
        self.max_steps = settings.validate_nonnegative_int(max_steps)

    def main(self):
        counter = self.max_steps
        toggle = True
        threshold = settings.adjust_tolerance / 2 ** 0.5
        while config.enabled and counter > 0 and \
                max(abs(self.target[0] - config.player_pos[0]),
                    abs(self.target[1] - config.player_pos[1])) > threshold:
            if toggle:
                d_x = self.target[0] - config.player_pos[0]
                if abs(d_x) > threshold:
                    direction = 'left' if d_x < 0 else 'right'
                    key_down(direction)
                    walk_counter = 0
                    while config.enabled and walk_counter < 60 and \
                            (self.target[0] - config.player_pos[0]) * (1 if d_x > 0 else -1) > threshold:
                        time.sleep(0.05)
                        walk_counter += 1
                    key_up(direction)
                    counter -= 1
            else:
                d_y = self.target[1] - config.player_pos[1]
                if abs(d_y) > threshold:
                    direction = 'up' if d_y < 0 else 'down'
                    key_down(direction)
                    vertical_step(direction)
                    key_up(direction)
                    counter -= 1
            toggle = not toggle


class Buff(Command):
    """Called every loop of the bot. Drinks potions when needed."""

    def main(self):
        use_potions()


class Attack(Command):
    """Attacks with the basic attack, facing DIRECTION first if one is given."""

    def __init__(self, direction=None, attacks=1, repetitions=1):
        super().__init__(locals())
        self.direction = None if direction is None else settings.validate_horizontal_arrows(direction)
        self.attacks = int(attacks)
        self.repetitions = int(repetitions)

    def main(self):
        attack(Key.ATTACK, self.direction, self.attacks * self.repetitions)


class Skill(Command):
    """Attacks with the skill key (e.g. Power Strike), facing DIRECTION first if one is given."""

    def __init__(self, direction=None, attacks=1, repetitions=1):
        super().__init__(locals())
        self.direction = None if direction is None else settings.validate_horizontal_arrows(direction)
        self.attacks = int(attacks)
        self.repetitions = int(repetitions)

    def main(self):
        attack(Key.SKILL, self.direction, self.attacks * self.repetitions)


def attack(key, direction, times):
    if direction:
        press(direction, 1, down_time=0.05)     # Face the monster
    for _ in range(times):
        use_potions()
        press(key, 1)
        time.sleep(0.5)                         # Let the swing finish


class PickUp(Command):
    """Picks up items under the player."""

    def __init__(self, presses=3):
        super().__init__(locals())
        self.presses = int(presses)

    def main(self):
        press(Key.PICK_UP, self.presses, up_time=0.15)


class HpPotion(Command):
    """Drinks an HP potion."""

    def main(self):
        press(Key.HP_POTION, 1)


class MpPotion(Command):
    """Drinks an MP potion."""

    def main(self):
        press(Key.MP_POTION, 1)
