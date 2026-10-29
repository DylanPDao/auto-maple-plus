"""
The built-in commands for classic (old-style UI) MapleStory, loaded automatically at startup.
Their keys are edited under Settings > Classic Keybindings.
"""

from src.common import config, settings
import time
import random
from src.routine.components import Command
from src.common.vkeys import press, key_down, key_up
from src.modules.paths import load_path, run_path, plan_route, all_paths


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
POT_SAVE_HP_POTION_BELOW = 0.15     # In pot save mode, HP potions only below this (emergencies)

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
ROPE_CHECK_TIME = 0.4       # Time after a rope jump to check the player is climbing
ROPE_CLIMB_MIN = 0.004      # Rise in that time that means a rope was caught
DROP_MIN = 0.01             # Fall that means a drop down worked
LAND_TIMEOUT = 2.0          # Longest wait for a jump or drop to land
ALIGN_WALK_TOLERANCE = 0.015    # Lining up with a rope or take-off spot: walk this close,
ALIGN_TOLERANCE = 0.006         # then tap until this close
ALIGN_TAPS = 10

# Getting onto a rope: how long (s) to wait for climbing to show after pressing up, how long a
# jump's rise lasts, and the taps (each a few in-game pixels) tried to either side if it misses
GRAB_CHECK_TIME = 0.25
JUMP_SETTLE_TIME = 0.35
ROPE_SEARCH_TAP = 0.02
ROPE_SEARCH_TAPS = 5

# A swing's length; the game ignores attack presses made during a swing
SWING_TIME = 0.75

# Random extra time (s) after each swing, and between turning to face a monster and swinging,
# so the timing never repeats exactly (PixelBotter uses a similar spread)
SWING_JITTER = (0.0, 0.15)
REACTION_DELAY = (0.03, 0.15)

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


def vitals():
    """Returns the (HP, MP) bar fractions from 0 to 1, each None if its bar can't be read."""

    return bar_fill(HP_BAR_X, 'red'), bar_fill(MP_BAR_X, 'blue')


def use_potions():
    """
    Drinks an HP or MP potion if that bar is low. In pot save mode only HP potions are used,
    and only in an emergency, since the bot rests to heal and saves MP by not using skills.
    """

    now = time.time()
    pot_save = config.bot.combat.config['pot_save']
    hp_below = POT_SAVE_HP_POTION_BELOW if pot_save else HP_POTION_BELOW
    mp_below = 0 if pot_save else MP_POTION_BELOW
    for kind, x_range, color, below, key in (('hp', HP_BAR_X, 'red', hp_below, Key.HP_POTION),
                                             ('mp', MP_BAR_X, 'blue', mp_below, Key.MP_POTION)):
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
            if config.bot.combat.monster_nearby():
                # Stop to fight monsters met on the way, then head for X again from wherever
                # the fight left the player
                key_up(direction)
                config.bot.combat.engage()
                d_x = x - config.player_pos[0]
                direction = 'right' if d_x > 0 else 'left'
                sign = 1 if d_x > 0 else -1
                best = abs(d_x)
                last_progress = time.time()
                jumped = False
                key_down(direction)
                continue
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
    """
    Goes to a position. Takes recorded paths when that's the way to another level (e.g. a rope,
    a jump across and another rope), otherwise walks across to its x, then climbs or drops to
    its height if needed.
    """

    def __init__(self, x, y, max_steps=3):
        super().__init__(locals())
        self.target = (float(x), float(y))
        self.max_steps = settings.validate_nonnegative_int(max_steps)

    def main(self):
        take_paths(self.target)
        go_to(self.target, MOVE_TOLERANCE, max(1, self.max_steps))


def take_paths(target):
    """
    Replays the recorded paths, if any, that lead from the player's level toward TARGET's. Stops
    early if one fails, leaving the rest of the way to the normal walking and climbing.
    """

    route = plan_route(config.player_pos, target, all_paths())
    if not route:
        return
    print(f"[~] Move: taking path{'s' if len(route) > 1 else ''} {', '.join(p['name'] for p in route)}")
    for path in route:
        if not config.enabled or not run_path(path, move_to_exactly, climb_rope):
            return


class Adjust(Command):
    """Fine-tunes player position after a Move."""

    def __init__(self, x, y, max_steps=3):
        super().__init__(locals())
        self.target = (float(x), float(y))
        self.max_steps = settings.validate_nonnegative_int(max_steps)

    def main(self):
        go_to(self.target, settings.adjust_tolerance, max(1, self.max_steps))


class Climb(Command):
    """
    Climbs (or goes down) the rope or ladder here to height Y, jumping to grab one just above.
    With X (the rope's position), first lines up with it exactly, since ropes are narrow.
    """

    def __init__(self, y, x=None):
        super().__init__(locals())
        self.y = float(y)
        self.x = optional_float(x)

    def main(self):
        if self.x is not None and self.y < config.player_pos[1]:
            climb_rope(self.x, self.y)          # Feels for the rope, then climbs
        else:
            change_height(self.y)
            finish_climb('up' if self.y < config.player_pos[1] + settings.move_tolerance else 'down')
        if abs(self.y - config.player_pos[1]) > settings.move_tolerance:
            print(f'[!] Climb: stopped at height {config.player_pos[1]:.3f}, wanted {self.y:.3f}')


class RopeJump(Command):
    """
    Jumps toward DIRECTION holding up to catch a rope or ladder in mid-air, then climbs to height
    Y if given. HOLD is how long DIRECTION is held during the jump (longer reaches further). With
    X, first lines up exactly with that take-off spot, since a jump's landing depends on it.
    Tries again if no rope was caught and the player is still where it jumped from.
    """

    def __init__(self, direction, y=None, x=None, hold=0.4, attempts=2):
        super().__init__(locals())
        self.direction = settings.validate_horizontal_arrows(direction)
        self.y = optional_float(y)
        self.x = optional_float(x)
        self.hold = float(hold)
        self.attempts = settings.validate_nonnegative_int(attempts)

    def main(self):
        level = config.player_pos[1]
        for _ in range(self.attempts):
            if not config.enabled:
                return
            if abs(config.player_pos[1] - level) > settings.move_tolerance / 2:
                break                           # Fell off on a miss, jumping again from here won't help
            if self.x is not None:
                align_x(self.x)
            if grab_rope(self.direction, self.hold):
                if self.y is not None:
                    change_height(self.y)
                    finish_climb('up' if self.y < config.player_pos[1] + settings.move_tolerance else 'down')
                return
            wait_to_land()
        print(f'[!] RopeJump: no rope caught jumping {self.direction}')


class JumpAcross(Command):
    """Jumps toward DIRECTION, holding it for HOLD seconds, e.g. across a gap, then waits to land."""

    def __init__(self, direction, hold=0.5):
        super().__init__(locals())
        self.direction = settings.validate_horizontal_arrows(direction)
        self.hold = float(hold)

    def main(self):
        key_down(self.direction)
        try:
            press(Key.JUMP, 1, down_time=0.05)
            time.sleep(self.hold)
        finally:
            key_up(self.direction)
        wait_to_land()


class DropDown(Command):
    """Drops down through the platform the player is standing on (down + jump)."""

    def __init__(self, attempts=2):
        super().__init__(locals())
        self.attempts = settings.validate_nonnegative_int(attempts)

    def main(self):
        for _ in range(self.attempts):
            if not config.enabled:
                return
            start = config.player_pos[1]
            key_down('down')
            try:
                time.sleep(0.05)
                press(Key.JUMP, 1, down_time=0.1)
            finally:
                key_up('down')
            wait_to_land()
            if config.player_pos[1] - start > DROP_MIN:
                return
        print('[!] DropDown: did not drop, is there a platform below?')


class Path(Command):
    """
    Replays a movement path recorded with the Record path key (taught by doing it once), e.g.
    climbing a rope, jumping across and climbing another. Missed jumps are retried.
    """

    def __init__(self, name):
        super().__init__(locals())
        self.name = str(name)

    def main(self):
        path = load_path(self.name)
        if path is None:
            print(f"[!] Path '{self.name}' not found, record it first")
            return
        run_path(path, move_to_exactly, climb_rope)


def move_to_exactly(x, y):
    """Walks (and climbs) to (X, Y), then lines up precisely with X."""

    go_to((x, y), MOVE_TOLERANCE, 2)
    align_x(x)


def optional_float(value):
    """Returns VALUE as a float, or None if it's empty (routine files save missing values as None)."""

    return None if value in (None, '', 'None') else float(value)


def catch_rope(x):
    """
    Gets onto the rope or ladder at X by feeling for it, like a player would. The minimap only
    shows the position to about one of its pixels (several in-game pixels), coarser than the
    narrow spot a rope can be grabbed from, so lining up exactly isn't possible. Instead it
    lines up roughly, tries to grab, and if that fails shuffles a few in-game pixels at a time
    to one side and then the other, trying again after each step.
    :return:    Whether the player is now on the rope.
    """

    align_x(x)
    if try_grab():
        return True
    for direction, taps in (('left', ROPE_SEARCH_TAPS), ('right', 2 * ROPE_SEARCH_TAPS)):
        for i in range(taps):
            if not config.enabled:
                return False
            press(direction, 1, down_time=ROPE_SEARCH_TAP, up_time=0.05)
            # The right-hand pass starts by tapping back to where the search began
            if direction == 'right' and i < ROPE_SEARCH_TAPS:
                continue
            if try_grab():
                return True
    return False


def try_grab():
    """
    Presses up to grab a rope or ladder here, then jumps with up held for one hanging above
    head height. Up is released afterwards, which leaves the player hanging on it.
    :return:    Whether the player is on a rope, judged by them climbing steadily once a jump
                would have come back down.
    """

    caught = False
    key_down('up')
    try:
        start = config.player_pos[1]
        time.sleep(GRAB_CHECK_TIME)
        if start - config.player_pos[1] > ROPE_CLIMB_MIN:
            caught = True                   # A rope reaching the ground: climbing already
            return True
        press(Key.JUMP, 1, down_time=0.05)
        time.sleep(JUMP_SETTLE_TIME)        # Past the jump's rise, which looks like climbing
        before = config.player_pos[1]
        time.sleep(GRAB_CHECK_TIME)
        after = config.player_pos[1]
        caught = before - after > ROPE_CLIMB_MIN and start - after > ROPE_CLIMB_MIN
        return caught
    finally:
        key_up('up')
        if not caught and config.enabled:
            wait_to_land()                  # Missed: let the jump finish before the next try


def climb_rope(x, y):
    """Gets onto the rope at X and climbs it to height Y, onto the platform at its end."""

    if not catch_rope(x):
        print(f'[!] Could not get onto the rope at x={x:.3f}')
        return False
    change_height(y)
    finish_climb('up' if y < config.player_pos[1] else 'down')
    return True


def align_x(x):
    """
    Lines the player up with X precisely, e.g. under a rope or at a jump's take-off spot:
    walks close, then taps toward it.
    """

    walk_to_x(x, ALIGN_WALK_TOLERANCE)
    for _ in range(ALIGN_TAPS):
        d_x = x - config.player_pos[0]
        if not config.enabled or abs(d_x) <= ALIGN_TOLERANCE:
            return
        press('right' if d_x > 0 else 'left', 1, down_time=0.02, up_time=0.08)


def grab_rope(direction, hold):
    """
    Jumps toward DIRECTION while holding up, which catches a rope or ladder the jump passes.
    :return:    Whether a rope was caught, judged by the player still rising with up held after
                the jump would have ended.
    """

    key_down('up')
    try:
        key_down(direction)
        try:
            press(Key.JUMP, 1, down_time=0.05)
            time.sleep(hold)
        finally:
            key_up(direction)
        start = config.player_pos[1]
        time.sleep(ROPE_CHECK_TIME)
        return start - config.player_pos[1] > ROPE_CLIMB_MIN
    finally:
        key_up('up')


def finish_climb(direction):
    """
    Keeps holding DIRECTION until the player stops moving, so a climb ends standing on the
    platform at the rope's end instead of hanging just short of it.
    """

    deadline = time.time() + LAND_TIMEOUT
    key_down(direction)
    try:
        last = config.player_pos[1]
        while config.enabled and time.time() < deadline:
            time.sleep(0.12)
            if abs(config.player_pos[1] - last) < 0.001:
                return
            last = config.player_pos[1]
    finally:
        key_up(direction)


def wait_to_land():
    """Waits until the player's height stops changing (landed, or caught on something)."""

    deadline = time.time() + LAND_TIMEOUT
    time.sleep(0.25)            # Past the top of the jump, where height briefly stops changing
    last = config.player_pos[1]
    still = 0
    while config.enabled and time.time() < deadline and still < 2:
        time.sleep(0.12)
        still = still + 1 if abs(config.player_pos[1] - last) < 0.002 else 0
        last = config.player_pos[1]


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
        # Pot save mode with low MP: the regular attack instead, so no MP potions are needed
        key = Key.ATTACK if config.bot.combat.low_mp() else Key.SKILL
        attack(key, self.direction, self.attacks * self.repetitions)
        if self.loot:
            sweep(self.direction, LOOT_SWEEP_TIME)


def attack(key, direction, times):
    if direction:
        press(direction, 1, down_time=0.05)     # Face the monster
        time.sleep(random.uniform(*REACTION_DELAY))
    for _ in range(times):
        use_potions()
        press(key, 1)
        # Let the swing finish, plus a little randomness so attacks never land on a fixed rhythm
        time.sleep(SWING_TIME + random.uniform(*SWING_JITTER))


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
