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

# Movement, in minimap-relative units and seconds
MOVE_TOLERANCE = 0.02       # How close in x a Move gets to its Point
STUCK_TIME = 1.0            # Time without getting closer before jumping, then giving up
CLIMB_TIMEOUT = 10          # Longest a single climb or drop may take
MAX_CLIMB_STALLS = 4        # Jumps tried while not moving up or down before giving up

# A swing's length; the game ignores attack presses made during a swing
SWING_TIME = 0.75

# Seconds spent walking forward over the drops after attacking with loot=True
LOOT_SWEEP_TIME = 1.0


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


def walk_to_x(x, tolerance):
    """
    Walks left or right until the player is within TOLERANCE of X. Jumps once if the player
    stops getting closer (e.g. a step or slope in the way), and gives up if that doesn't help.
    """

    d_x = x - config.player_pos[0]
    if abs(d_x) <= tolerance:
        return
    direction = 'right' if d_x > 0 else 'left'
    sign = 1 if d_x > 0 else -1
    best = abs(d_x)
    last_progress = time.time()
    jumped = False
    key_down(direction)
    try:
        while config.enabled:
            use_potions()
            remaining = (x - config.player_pos[0]) * sign
            if remaining <= tolerance:
                break
            now = time.time()
            if remaining < best - 0.002:
                best = remaining
                last_progress = now
                jumped = False
            elif now - last_progress > STUCK_TIME:
                if jumped:
                    break
                press(Key.JUMP, 1)
                jumped = True
                last_progress = now
            time.sleep(0.05)
    finally:
        key_up(direction)


def change_height(y):
    """
    Climbs or drops toward height Y. Holding up or down climbs a rope or ladder the player is
    on; whenever the player isn't moving, jumps instead, which grabs a rope above when going up
    and drops through the platform when going down.
    """

    direction = 'up' if y < config.player_pos[1] else 'down'
    sign = -1 if direction == 'up' else 1
    stalls = 0
    deadline = time.time() + CLIMB_TIMEOUT
    key_down(direction)
    try:
        last = config.player_pos[1]
        while config.enabled and time.time() < deadline and stalls < MAX_CLIMB_STALLS:
            if (y - config.player_pos[1]) * sign <= settings.move_tolerance / 2:
                break
            time.sleep(0.3)
            if (config.player_pos[1] - last) * sign < 0.003:
                stalls += 1
                press(Key.JUMP, 1)
                time.sleep(0.5)
            last = config.player_pos[1]
    finally:
        key_up(direction)


def go_to(target, tolerance, attempts):
    """
    Walks across to TARGET's x, then climbs or drops only if its height really differs (so walking
    up or down a slope isn't mistaken for a level change), repeating up to ATTEMPTS times.
    """

    for _ in range(attempts):
        if not config.enabled:
            return
        walk_to_x(target[0], tolerance)
        if abs(target[1] - config.player_pos[1]) <= settings.move_tolerance:
            if abs(target[0] - config.player_pos[0]) <= tolerance:
                return
        else:
            change_height(target[1])


#########################
#       Commands        #
#########################
class Move(Command):
    """Walks to a position: across to its x, then up or down to its height if needed."""

    def __init__(self, x, y, max_steps=3):
        super().__init__(locals())
        self.target = (float(x), float(y))
        self.max_steps = settings.validate_nonnegative_int(max_steps)

    def main(self):
        go_to(self.target, MOVE_TOLERANCE, max(1, self.max_steps))


class Adjust(Command):
    """Fine-tunes player position after a Move."""

    def __init__(self, x, y, max_steps=3):
        super().__init__(locals())
        self.target = (float(x), float(y))
        self.max_steps = settings.validate_nonnegative_int(max_steps)

    def main(self):
        go_to(self.target, settings.adjust_tolerance, max(1, self.max_steps))


class Buff(Command):
    """Called every loop of the bot. Drinks potions when needed."""

    def main(self):
        use_potions()


class Attack(Command):
    """
    Attacks with the basic attack, facing DIRECTION first if one is given. With LOOT, then
    sweeps forward picking up the drops.
    """

    def __init__(self, direction=None, attacks=1, repetitions=1, loot='False'):
        super().__init__(locals())
        self.direction = None if direction is None else settings.validate_horizontal_arrows(direction)
        self.attacks = int(attacks)
        self.repetitions = int(repetitions)
        self.loot = settings.validate_boolean(loot)

    def main(self):
        attack(Key.ATTACK, self.direction, self.attacks * self.repetitions)
        if self.loot:
            sweep(self.direction, LOOT_SWEEP_TIME)


class Skill(Command):
    """
    Attacks with the skill key (e.g. Power Strike), facing DIRECTION first if one is given.
    With LOOT, then sweeps forward picking up the drops.
    """

    def __init__(self, direction=None, attacks=1, repetitions=1, loot='False'):
        super().__init__(locals())
        self.direction = None if direction is None else settings.validate_horizontal_arrows(direction)
        self.attacks = int(attacks)
        self.repetitions = int(repetitions)
        self.loot = settings.validate_boolean(loot)

    def main(self):
        attack(Key.SKILL, self.direction, self.attacks * self.repetitions)
        if self.loot:
            sweep(self.direction, LOOT_SWEEP_TIME)


def attack(key, direction, times):
    if direction:
        press(direction, 1, down_time=0.05)     # Face the monster
    for _ in range(times):
        use_potions()
        press(key, 1)
        time.sleep(SWING_TIME)                  # Let the swing finish


def sweep(direction, duration):
    """
    Walks in DIRECTION for DURATION seconds tapping the pick up key, collecting the drops of
    monsters killed in front of the player. Without a direction, only loots in place.
    """

    if direction:
        key_down(direction)
        try:
            end = time.time() + duration
            while config.enabled and time.time() < end:
                press(Key.PICK_UP, 1, down_time=0.05, up_time=0.1)
        finally:
            key_up(direction)
    press(Key.PICK_UP, 3, up_time=0.15)         # Whatever is under the player now


class PickUp(Command):
    """
    Picks up items under the player. With a DIRECTION, first walks that way for DURATION
    seconds tapping the pick up key, collecting drops along the way.
    """

    def __init__(self, presses=3, direction=None, duration=LOOT_SWEEP_TIME):
        super().__init__(locals())
        self.presses = int(presses)
        self.direction = None if direction is None else settings.validate_horizontal_arrows(direction)
        self.duration = float(duration)

    def main(self):
        if self.direction:
            sweep(self.direction, self.duration)
        else:
            press(Key.PICK_UP, self.presses, up_time=0.15)


class HpPotion(Command):
    """Drinks an HP potion."""

    def main(self):
        press(Key.HP_POTION, 1)


class MpPotion(Command):
    """Drinks an MP potion."""

    def main(self):
        press(Key.MP_POTION, 1)
