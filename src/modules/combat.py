"""
Reactive combat that runs between routine Points. Finds the player and nearby monsters on
the main game screen using template matching, then chases and attacks monsters that are on
the player's platform before handing control back to the routine.
"""

import os
import re
import math
import time
import random
import shutil
import inspect
import cv2
import numpy as np
from src.common import config
from src.common.interfaces import Configurable
from src.common.vkeys import key_down, key_up, press


COMBAT_DIR = os.path.join('assets', 'combat')
PLAYER_TEMPLATE_PATH = os.path.join(COMBAT_DIR, 'player.png')
MOBS_DIR = os.path.join(COMBAT_DIR, 'mobs')
DROPS_DIR = os.path.join(COMBAT_DIR, 'drops')

# Something only seen in town, cropped on the Combat tab ("Save as town marker") for the watcher
TOWN_MARKER_PATH = os.path.join('assets', 'town_marker.png')
IMAGE_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.bmp')

# Upper bound on raw template hits considered per frame, keeps suppression cheap
MAX_RAW_MATCHES = 400

# Coarse-to-fine matching for masked templates over big areas: scale of the coarse pass, how
# much lower its threshold is, how many candidates are rescored at full size and how far (px)
# around each. Tuned on recorded frames to give exactly the full-size results
COARSE_SCALE = 0.5
COARSE_MARGIN = 0.35
COARSE_CANDIDATES = 40
COARSE_PAD = 4
COARSE_MIN_SIZE = 16            # Smaller templates lose too much detail at half size
COARSE_MIN_AREA = 120000        # Smaller areas (e.g. tracking one monster) are scanned directly

# Padding (px) around the player's last known position searched before the whole frame
PLAYER_SEARCH_PADDING = 150

# Minimum seconds between monster checks made while walking
SCAN_INTERVAL = 0.25

# Seconds a target may go unseen (hit flashes, skill effects) before the fight counts as over
LOST_GRACE = 0.8

# How far (px) a monster may move between checks (walking, knock-back) and still count as the
# same target
LOCK_RADIUS = 100

# Monster HP bars: a black box about 52x10 px with a bright fill from the left, shown above a
# monster's head once it has been hit. Sizes allow for other resolutions and big monsters
HP_BAR_WIDTH = (36, 80)
HP_BAR_HEIGHT = (7, 14)
HP_BAR_DARK = 50            # Outline and empty part are darker than this
HP_BAR_ABOVE = 220          # How far above the monster's middle (px) its bar may be
HP_BAR_BELOW = 20

# Commands that take a direction but aren't attacks, left out of the attack command dropdown
NOT_ATTACKS = {'pickup', 'walk', 'move', 'adjust', 'fall', 'wait', 'buff', 'ropejump', 'jumpacross'}

# Nametag text is drawn in pure white, anything at least this bright counts as text
TEXT_LEVEL = 250
MIN_TEXT_PIXELS = 10

# Looting after a kill: the longest walk (s) toward where the monster died, and how close (px)
# to that spot counts as there
LOOT_TIMEOUT = 1.5
LOOT_REACH = 20

# Looting by sight (when drop images are saved): how far to look (px), the longest time spent,
# and the most drops picked up per kill
LOOT_RANGE = 250
LOOT_SEARCH_TIME = 5.0
MAX_LOOT_PICKUPS = 6

# A monster cut-out covering less or more of its box than this is treated as a failed cut
MIN_CUTOUT_COVERAGE = 0.05
MAX_CUTOUT_COVERAGE = 0.97

# Skill rotation: number of skill slots, and the random spread added to delays and cooldowns so
# they never repeat exactly
SKILL_SLOTS = 4
REACTION_DELAY = (0.03, 0.15)
SKILL_DELAY_JITTER = (0.0, 0.15)
COOLDOWN_JITTER = (0.0, 0.5)

# Pot save mode: how often (s) HP and nearby monsters are checked while resting
REST_CHECK_INTERVAL = 1.0


class Combat(Configurable):
    DEFAULT_CONFIG = {
        'enabled': False,
        'loot': True,               # Walk over each killed monster's spot tapping the pick up key
        'attack_command': '',       # Command book command called as Command(direction), e.g. 'cruelstab'
        'attack_key': '',           # Key pressed after facing the mob if no attack command is set
        'engage_range': 400,        # Max horizontal distance (px) to a mob for it to be engaged
        'attack_range': 150,        # Horizontal distance (px) at which the bot stops walking and attacks
        'layer_height': 60,         # Max vertical distance (px) for a mob to count as on the same platform
        'player_offset_y': -40,     # Shifts the nametag match up to the character's body
        'chase_timeout': 4.0,       # Seconds to walk toward a mob before giving up on it
        'engage_timeout': 15.0,     # Max seconds spent fighting before returning to the routine
        'give_up_cooldown': 8.0,    # Seconds to skip combat after a chase times out
        'threshold': 0.8,           # Template matching confidence to pick a new monster
        'track_threshold': 0.65,    # Lower confidence for keeping track of the monster being fought
        'mob_set': '',              # Folder in assets/combat/mobs to use, '' = images directly in it
        'movement': 'loop',         # 'loop' visits routine Points in order, 'hunt' picks where to go
        'skill_cast_time': 0.8,     # Seconds a rotation skill takes before the next action
        **{f'skill{i}_key': '' for i in range(1, SKILL_SLOTS + 1)},          # Rotation, top first
        **{f'skill{i}_cooldown': 10.0 for i in range(1, SKILL_SLOTS + 1)},
        'pot_save': False,          # Rest instead of drinking HP potions, regular attack at low MP
        'rest_below_hp': 25,        # Rest when HP (%) is below this and no monster is around
        'rest_until_hp': 90,        # ...until HP (%) is back to this
        'max_rest_time': 120,       # Longest rest (s)
        'chair_key': '',            # Key to sit in a chair while resting ('' = just stand still)
        'skills_below_mp': 40,      # Below this MP (%), the regular attack instead of skills
        'low_mp_command': 'attack', # Command book command used as the regular attack
        'rest_spot': ''             # 'x,y' minimap position to rest at, '' = rest where the bot is
    }

    def __init__(self):
        super().__init__('combat')
        self.player_template = None
        self.mob_templates = []
        self.drop_templates = []
        self.skill_ready = {}           # Time each rotation skill's key can next be used
        self.cooldown_until = 0
        self.last_scan = 0
        self.locked = None              # Current target's (dx, dy) offset from the player
        self.last_result = None         # Latest detect() result from find_target
        self.last_player = None
        self.kills = 0                  # Monsters fought to the end, used by hunt movement
        self.warned = set()
        self.load_templates()

    def load_templates(self):
        """
        Loads the player nametag template and every mob template (plus its mirror image).
        Mob templates are (gray, mask) pairs, the mask coming from the image's transparency.
        """

        self.player_template = None
        self.player_is_text = False
        if os.path.isfile(PLAYER_TEMPLATE_PATH):
            nametag = cv2.imread(PLAYER_TEMPLATE_PATH, 0)
            text = text_template(nametag) if nametag is not None else None
            self.player_is_text = text is not None
            self.player_template = text if text is not None else nametag

        self.mob_templates = []
        for _, path in self.list_mobs(self.config['mob_set']):
            template = load_masked(path)
            if template is not None:
                gray, mask = template
                self.mob_templates.append((gray, mask))
                self.mob_templates.append((cv2.flip(gray, 1), None if mask is None else cv2.flip(mask, 1)))

        self.drop_templates = [t for t in (load_masked(p) for _, p in self.list_drops()) if t is not None]
        self.warned.clear()
        return self.player_template is not None, len(self.mob_templates) // 2

    #################################
    #             Drops             #
    #################################
    @staticmethod
    def list_drops():
        """Returns (name, path) for every drop image. Drops are shared by all monster sets."""

        if not os.path.isdir(DROPS_DIR):
            return []
        return [(os.path.splitext(n)[0], os.path.join(DROPS_DIR, n)) for n in sorted(os.listdir(DROPS_DIR))
                if n.lower().endswith(IMAGE_EXTENSIONS)]

    def save_drop(self, name, image):
        """Saves IMAGE as drop NAME. Returns the file path."""

        os.makedirs(DROPS_DIR, exist_ok=True)
        path = os.path.join(DROPS_DIR, clean_name(name) + '.png')
        cv2.imwrite(path, image)
        self.load_templates()
        return path

    #################################
    #         Monster Sets          #
    #################################
    @staticmethod
    def list_sets():
        """Returns the names of all monster sets (subfolders of the mobs folder)."""

        if not os.path.isdir(MOBS_DIR):
            return []
        return sorted(n for n in os.listdir(MOBS_DIR) if os.path.isdir(os.path.join(MOBS_DIR, n)))

    @staticmethod
    def list_mobs(mob_set):
        """Returns (name, path) for every monster image in MOB_SET."""

        directory = os.path.join(MOBS_DIR, mob_set) if mob_set else MOBS_DIR
        if not os.path.isdir(directory):
            return []
        return [(os.path.splitext(n)[0], os.path.join(directory, n)) for n in sorted(os.listdir(directory))
                if n.lower().endswith(IMAGE_EXTENSIONS)]

    def use_set(self, mob_set):
        """Makes MOB_SET the active monster set and reloads its templates."""

        self.config['mob_set'] = mob_set
        self.save_config()
        return self.load_templates()

    def use_set_for_routine(self, routine_path):
        """
        Switches to the monster set named after ROUTINE_PATH's file, if one exists.
        :return:    The name of the set switched to, otherwise None.
        """

        name = os.path.splitext(os.path.basename(routine_path))[0]
        if name in self.list_sets() and name != self.config['mob_set']:
            self.use_set(name)
            print(f" ~  Combat: using monster set '{name}'")
            return name

    @staticmethod
    def create_set(name):
        name = clean_name(name)
        if name:
            os.makedirs(os.path.join(MOBS_DIR, name), exist_ok=True)
        return name

    def delete_set(self, name):
        """Deletes monster set NAME and its images. Switches to ungrouped if it was active."""

        if name not in self.list_sets():        # Never resolve paths like '..' outside the mobs folder
            return
        shutil.rmtree(os.path.join(MOBS_DIR, name))
        if self.config['mob_set'] == name:
            self.use_set('')

    def save_mob(self, name, image):
        """Saves IMAGE as monster NAME in the active set. Returns the file path."""

        mob_set = self.config['mob_set']
        directory = os.path.join(MOBS_DIR, mob_set) if mob_set else MOBS_DIR
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, clean_name(name) + '.png')
        cv2.imwrite(path, image)
        self.load_templates()
        return path

    def save_player(self, image):
        os.makedirs(COMBAT_DIR, exist_ok=True)
        cv2.imwrite(PLAYER_TEMPLATE_PATH, image)
        self.load_templates()

    def delete_mob(self, path):
        if os.path.isfile(path):
            os.remove(path)
        self.load_templates()

    def number(self, key):
        """Returns setting KEY as a float, falling back to its default if it is invalid."""

        try:
            return float(self.config[key])
        except (TypeError, ValueError):
            return float(self.DEFAULT_CONFIG[key])

    def engage(self):
        """
        Fights monsters on the player's platform until none are left in range or the
        engage timeout passes.
        :return:    Whether any monster was engaged.
        """

        if not self._ready():
            return False

        engaged = False
        cleared = False
        lost_since = None
        finished = False            # Whether the finishing swing was used since last seeing the target
        seen_bar = False            # Whether the locked monster's HP bar has been seen
        self.locked = None
        deadline = time.time() + self.number('engage_timeout')
        while config.enabled and time.time() < deadline:
            target = self.find_target(self.locked)
            if self.locked is not None and self.last_result and self.last_result['hp_bar']:
                seen_bar = True
            if target is None:
                # A monster whose HP bar was showing and is now gone is dead, no need to swing again
                if self.locked is not None and not finished and not seen_bar and \
                        abs(self.locked[0]) <= self.number('attack_range'):
                    # Lost sight of a monster that was in reach, most likely hidden by the hit
                    # itself, so swing once more where it was before deciding it's gone
                    finished = True
                    direction = 'right' if self.locked[0] > 0 else 'left'
                    log(f'lost sight of monster, finishing swing {direction}')
                    if not self._attack(direction):
                        break
                    lost_since = None
                    continue
                # Hit flashes, knock-back and skill effects hide monsters for a moment, so only
                # call the fight over once nothing has been seen for LOST_GRACE seconds
                now = time.time()
                lost_since = lost_since or now
                if now - lost_since < LOST_GRACE:
                    time.sleep(0.05)
                    continue
                if self.locked is not None:
                    # The locked monster is gone for good (dead or out of reach), so pick up
                    # whatever it dropped, then look for another
                    self.kills += 1
                    if self.config['loot']:
                        self._loot(self.locked[0])
                    self.locked = None
                    seen_bar = False
                    lost_since = None
                    continue
                cleared = True
                if engaged:
                    log('no monsters left, back to the routine')
                break
            lost_since = None
            finished = False
            player, mob = target
            d_x = mob[0] - player[0]
            if self.locked is None:
                log(f'monster {d_x:+.0f}px away, engaging')
            engaged = True
            self.locked = (d_x, mob[1] - player[1])
            direction = 'right' if d_x > 0 else 'left'
            if abs(d_x) > self.number('attack_range'):
                log(f'chasing {direction} ({abs(d_x):.0f}px)')
                if not self._chase(direction):
                    log('could not reach monster, back to the routine')
                    break
            else:
                log(f'attacking {direction} ({abs(d_x):.0f}px)')
                if not self._attack(direction):
                    break
        else:
            if engaged and config.enabled:
                log('fight took too long, back to the routine')
        self.locked = None

        # Couldn't finish the fight (unreachable, timed out or no attack set), so leave these
        # monsters alone for a while instead of stopping for them again straight away
        if engaged and not cleared and config.enabled:
            self.cooldown_until = time.time() + self.number('give_up_cooldown')
            log(f"ignoring monsters for {self.number('give_up_cooldown'):.0f}s")
        return engaged

    def monster_nearby(self):
        """
        Cheap check for movement code to call while walking: looks for a monster on the player's
        platform at most every SCAN_INTERVAL seconds.
        :return:    True if the caller should stop and call engage().
        """

        now = time.time()
        if now - self.last_scan < SCAN_INTERVAL or not self._ready(warn=False):
            return False
        self.last_scan = now
        return self.find_target() is not None

    def _ready(self, warn=True):
        """Returns whether combat is enabled, set up and not cooling down."""

        if not self.config['enabled'] or time.time() < self.cooldown_until:
            return False
        if config.bot.rune_active or config.char_in_town:
            return False
        if self.player_template is None or not self.mob_templates:
            if warn:
                self._warn_once('templates', f"[!] Combat is enabled but templates are missing, add "
                                             f"'{PLAYER_TEMPLATE_PATH}' and images in '{MOBS_DIR}'")
            return False
        return True

    def find_target(self, locked=None):
        """
        Locates the player and the monster to fight on the player's platform.
        :param locked:  The current target's previous (dx, dy) offset from the player, to keep
                        fighting the same monster instead of switching to whichever is closest.
        :return:        A tuple of (player position, monster position), or None.
        """

        result = self.detect(config.capture.frame, locked=locked)
        self.last_result = result
        if result is None or result['target'] is None:
            return None
        return result['player'], result['target']

    def detect(self, frame, whole_frame=False, locked=None):
        """
        Runs player and monster detection on FRAME.
        :param whole_frame: Search the whole frame for monsters instead of only the band around
                            the player's platform. Slower, used by the debug views.
        :param locked:      Previous (dx, dy) offset of the current target from the player. The
                            monster nearest that offset is kept as the target if within LOCK_RADIUS.
        :return:            None if FRAME or the templates are missing, otherwise a dict with
                            'player' (body position or None), 'player_score', 'region' (monster
                            search area), 'mobs' (list of (x, y, score, in_range)) and 'target'.
        """

        if frame is None or self.player_template is None or not self.mob_templates:
            return None
        if frame.shape[2] == 4:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGRA2GRAY)
        else:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        result = {'player': None, 'player_score': None, 'region': None, 'mobs': [], 'target': None,
                  'hp_bars': [], 'hp_bar': None}

        threshold = self.number('threshold')
        found = self._find_player(gray)
        if found is None:
            if whole_frame:
                result['mobs'] = [m + (False,) for m in match_templates(gray, self.mob_templates, threshold)]
            return result
        player, result['player_score'] = found
        result['player'] = player

        # Only search the horizontal band around the player's platform
        layer_height = self.number('layer_height')
        engage_range = self.number('engage_range')
        tallest = max(t.shape[0] for t, _ in self.mob_templates)
        widest = max(t.shape[1] for t, _ in self.mob_templates)
        region = (player[0] - engage_range - widest, player[1] - layer_height - tallest,
                  player[0] + engage_range + widest, player[1] + layer_height + tallest)
        result['region'] = region

        if whole_frame:
            result['hp_bars'] = find_hp_bars(frame)

        in_range = []
        for m in match_templates(gray, self.mob_templates, threshold, None if whole_frame else region):
            ok = abs(m[1] - player[1]) <= layer_height and abs(m[0] - player[0]) <= engage_range
            result['mobs'].append(m + (ok,))
            if ok:
                in_range.append(m)
        if locked is None:
            if in_range:
                result['target'] = min(in_range, key=lambda m: abs(m[0] - player[0]))[:2]
            return result

        # Only the locked monster counts, even if another is closer or it is briefly hidden,
        # otherwise the bot ping-pongs between monsters on either side of it. It is searched for
        # around where it should be with the lower tracking confidence, since hit flashes and
        # turning poses lower its score without it being gone (the same trick as PixelBotter's
        # 0.65 tracking threshold)
        expected = (player[0] + locked[0], player[1] + locked[1])
        pad_x, pad_y = LOCK_RADIUS + widest, LOCK_RADIUS + tallest
        track_region = (expected[0] - pad_x, expected[1] - pad_y, expected[0] + pad_x, expected[1] + pad_y)
        offset = lambda m: math.hypot(m[0] - expected[0], m[1] - expected[1])
        candidates = [m for m in match_templates(gray, self.mob_templates,
                                                 min(threshold, self.number('track_threshold')), track_region)
                      if offset(m) <= LOCK_RADIUS and abs(m[1] - player[1]) <= layer_height]

        # A hit monster shows an HP bar above its head until it dies, and the bar looks the same
        # whatever the monster is doing (PixelBotter's anchored target). Cheap, so always checked:
        # it keeps the target when the sprite isn't matched, and tells engage() the monster is alive
        bar_region = (expected[0] - LOCK_RADIUS - HP_BAR_WIDTH[1], expected[1] - HP_BAR_ABOVE,
                      expected[0] + LOCK_RADIUS + HP_BAR_WIDTH[1], expected[1] + HP_BAR_BELOW)
        bars = [b for b in find_hp_bars(frame, bar_region) if abs(b[0] - expected[0]) <= LOCK_RADIUS]
        if bars:
            result['hp_bar'] = min(bars, key=lambda b: abs(b[0] - expected[0]))

        if candidates:
            result['target'] = min(candidates, key=offset)[:2]
        elif bars:
            result['target'] = (result['hp_bar'][0], expected[1])
        return result

    def _find_player(self, gray):
        """
        Finds the player's nametag in GRAY, near its last position first.
        :return:    ((body x, body y), match score), or None if the nametag isn't visible.
        """

        threshold = self.number('threshold')
        # The nametag box is see-through, so match only its pure white text when possible
        player_image = text_pixels(gray) if self.player_is_text else gray
        player_templates = [(self.player_template, None)]
        players = []
        if self.last_player is not None:
            x, y = self.last_player
            p = PLAYER_SEARCH_PADDING
            players = match_templates(player_image, player_templates, threshold, (x - p, y - p, x + p, y + p))
        if not players:
            players = match_templates(player_image, player_templates, threshold)
        if not players:
            self.last_player = None
            return None
        self.last_player = players[0][:2]
        return (players[0][0], players[0][1] + self.number('player_offset_y')), players[0][2]

    def nearest_drop(self):
        """
        Looks for drops on the player's platform within LOOT_RANGE.
        :return:    The nearest drop's horizontal offset (px) from the player, or None.
        """

        frame = config.capture.frame
        if frame is None or not self.drop_templates or self.player_template is None:
            return None
        gray = cv2.cvtColor(frame, cv2.COLOR_BGRA2GRAY if frame.shape[2] == 4 else cv2.COLOR_BGR2GRAY)
        found = self._find_player(gray)
        if found is None:
            return None
        (px, py), _ = found
        layer_height = self.number('layer_height')
        region = (px - LOOT_RANGE - 60, py - layer_height - 60, px + LOOT_RANGE + 60, py + layer_height + 60)
        drops = [d[0] - px for d in match_templates(gray, self.drop_templates, self.number('threshold'), region)
                 if abs(d[0] - px) <= LOOT_RANGE and abs(d[1] - py) <= layer_height]
        return min(drops, key=abs) if drops else None

    def _loot_by_sight(self, key):
        """Walks to the drops it can see nearby, nearest first, tapping KEY on each."""

        log('looking for drops')
        deadline = time.time() + LOOT_SEARCH_TIME
        pickups = 0
        while config.enabled and time.time() < deadline and pickups < MAX_LOOT_PICKUPS:
            d_x = self.nearest_drop()
            if d_x is None:
                break
            if abs(d_x) > LOOT_REACH:
                direction = 'right' if d_x > 0 else 'left'
                key_down(direction)
                try:
                    while config.enabled and time.time() < deadline:
                        d_x = self.nearest_drop()
                        # Arrived, walked past it, or it's gone (picked up by someone, or hidden)
                        if d_x is None or abs(d_x) <= LOOT_REACH or (d_x > 0) != (direction == 'right'):
                            break
                finally:
                    key_up(direction)
            press(key, 2, down_time=0.05, up_time=0.1)
            pickups += 1
        press(key, 1)

    def _chase(self, direction):
        """
        Walks in DIRECTION until a monster is within attack range or no target remains.
        :return:    False if the chase timed out, otherwise True.
        """

        attack_range = self.number('attack_range')
        deadline = time.time() + self.number('chase_timeout')
        lost_since = None
        key_down(direction)
        try:
            # No sleep between checks: detection itself takes long enough, and checking as
            # often as possible keeps the bot from walking past the monster
            while config.enabled and time.time() < deadline:
                target = self.find_target(self.locked)
                if target is None:
                    now = time.time()
                    lost_since = lost_since or now
                    if now - lost_since >= LOST_GRACE:
                        return True
                    time.sleep(0.02)
                    continue
                lost_since = None
                player, mob = target
                d_x = mob[0] - player[0]
                self.locked = (d_x, mob[1] - player[1])
                if abs(d_x) <= attack_range or (d_x > 0) != (direction == 'right'):
                    return True
            return not config.enabled
        finally:
            key_up(direction)

    def _attack(self, direction):
        """
        Attacks in DIRECTION using the configured command book command or attack key.
        :return:    False if no attack is configured.
        """

        low_mp = self.low_mp()
        skill = None if low_mp else self._ready_skill()
        if skill is not None:
            log(f'casting skill {skill} {direction}')
            press(direction, 1, down_time=0.05)     # Face the monster
            time.sleep(random.uniform(*REACTION_DELAY))
            press(skill, 1)
            time.sleep(self.number('skill_cast_time') + random.uniform(*SKILL_DELAY_JITTER))
            return True

        # Pot save mode with low MP: the regular attack instead of the (MP using) attack command
        name = str(self.config['low_mp_command' if low_mp else 'attack_command']).strip().lower()
        command_book = config.bot.command_book
        if name and command_book is not None and name in command_book:
            try:
                command_book[name](direction).execute()
                return True
            except (TypeError, ValueError) as e:
                self._warn_once(f'command:{name}', f"[!] Combat: command '{name}' cannot be "
                                                   f"called with a direction ({e})")
        elif name:
            self._warn_once(f'missing:{name}', f"[!] Combat: command '{name}' is not in the "
                                               f"current command book")

        key = str(self.config['attack_key']).strip()
        if key:
            press(direction, 1, down_time=0.05)     # Face the monster
            press(key, 1)
            return True
        self._warn_once('no_attack', '[!] Combat: set an attack command or attack key in Settings')
        return False

    #################################
    #           Pot Save            #
    #################################
    @staticmethod
    def vitals():
        """Returns (HP, MP) fractions from the command book's bar reading, each None if unknown."""

        command_book = config.bot.command_book
        read = getattr(getattr(command_book, 'module', None), 'vitals', None)
        return read() if read is not None else (None, None)

    def low_mp(self):
        """Whether pot save mode is on and MP is below the point where skills are skipped."""

        if not self.config['pot_save']:
            return False
        _, mp = self.vitals()
        return mp is not None and mp * 100 < self.number('skills_below_mp')

    def rest_spot(self):
        """Returns the saved rest spot as (x, y) minimap coordinates, or None if none is set."""

        try:
            x, y = (float(v) for v in str(self.config['rest_spot']).split(','))
            return x, y
        except ValueError:
            return None

    def rest_if_needed(self):
        """
        In pot save mode, rests (in a chair if a key is set) while HP is low, until HP recovers,
        a monster shows up or the rest takes too long. With a rest spot set it goes there first
        and clears it of monsters; otherwise it rests in place, only when no monster is around.
        :return:    Whether the bot rested.
        """

        if not self.config['pot_save'] or not config.enabled:
            return False
        hp, _ = self.vitals()
        if hp is None or hp * 100 >= self.number('rest_below_hp'):
            return False
        spot = self.rest_spot()
        command_book = config.bot.command_book
        if spot is not None and command_book is not None and 'move' in command_book:
            log(f'HP {hp:.0%}, going to the rest spot')
            command_book['move'](*spot).execute()       # Takes recorded paths to other levels
            if not config.enabled:
                return True
            self.engage()                               # Clear the spot before sitting down
        elif self.find_target() is not None:
            return False                # Fight first, sitting down mid-fight just gets the player hit
        log(f'HP {hp:.0%}, resting')
        chair = str(self.config['chair_key']).strip()
        if chair:
            press(chair, 1)
        deadline = time.time() + self.number('max_rest_time')
        while config.enabled and time.time() < deadline:
            time.sleep(REST_CHECK_INTERVAL)
            hp, _ = self.vitals()
            if hp is not None and hp * 100 >= self.number('rest_until_hp'):
                log(f'HP back to {hp:.0%}, done resting')
                break
            if self.find_target() is not None:
                log('monster nearby, getting up')
                break
        else:
            if config.enabled:
                log('rested as long as allowed, carrying on')
        if chair and config.enabled:
            press(jump_key(), 1)        # Stand up from the chair
        return True

    def _ready_skill(self):
        """
        Returns the key of the highest rotation skill that is off cooldown and marks it used,
        or None if none is ready (or none are set).
        """

        now = time.time()
        for i in range(1, SKILL_SLOTS + 1):
            key = str(self.config[f'skill{i}_key']).strip()
            if key and now >= self.skill_ready.get(key, 0):
                self.skill_ready[key] = now + self.number(f'skill{i}_cooldown') + random.uniform(*COOLDOWN_JITTER)
                return key
        return None

    def _loot(self, d_x):
        """
        Walks to where the monster D_X px from the player was last seen, tapping the pick up
        key over its drops, then picks up whatever is underfoot.
        """

        key = pick_up_key()
        if key is None:
            self._warn_once('no_pick_up', '[!] Combat: no pick up key, set PICK_UP in the keybindings')
            return
        if self.drop_templates:
            self._loot_by_sight(key)
            return
        log('picking up drops')
        if self.last_player is not None and abs(d_x) > LOOT_REACH:
            goal = self.last_player[0] + d_x
            direction = 'right' if d_x > 0 else 'left'
            sign = 1 if d_x > 0 else -1
            deadline = time.time() + LOOT_TIMEOUT
            key_down(direction)
            try:
                while config.enabled and time.time() < deadline:
                    press(key, 1, down_time=0.05, up_time=0.05)
                    self.detect(config.capture.frame)       # Updates last_player
                    if self.last_player is not None and (goal - self.last_player[0]) * sign <= LOOT_REACH:
                        break
            finally:
                key_up(direction)
        press(key, 3, up_time=0.15)

    def _warn_once(self, key, message):
        if key not in self.warned:
            self.warned.add(key)
            print(message)


def log(message):
    """Prints a combat message and shows it as the combat status in the View tab."""

    print(f'[~] Combat: {message}')
    try:
        config.gui.view.monitoringconsole.set_combatstat(message)
    except AttributeError:      # No GUI yet (or running without one)
        pass


def pick_up_key():
    """Returns the loaded command book's PICK_UP key, or None if it has none."""

    command_book = config.bot.command_book
    if command_book is not None and hasattr(command_book.module.Key, 'PICK_UP'):
        return command_book.module.Key.PICK_UP
    return None


def jump_key():
    """Returns the loaded command book's JUMP key, or 'space' if it has none."""

    key = getattr(getattr(getattr(config.bot.command_book, 'module', None), 'Key', None), 'JUMP', None)
    return key or 'space'


def attack_commands(command_book):
    """
    Returns the names of COMMAND_BOOK's commands that combat can call as Command(direction):
    those taking a 'direction' argument whose other arguments all have defaults.
    """

    if command_book is None:
        return []
    names = []
    for name, command in command_book.dict.items():
        if name in NOT_ATTACKS:
            continue
        try:
            params = list(inspect.signature(command.__init__).parameters.values())[1:]
        except (TypeError, ValueError):
            continue
        if not any(p.name == 'direction' for p in params):
            continue
        if all(p.name == 'direction' or p.default is not inspect.Parameter.empty
               or p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD) for p in params):
            names.append(name)
    return sorted(names)


def find_hp_bars(frame, region=None):
    """
    Finds monster HP bars: black boxes of HP_BAR_WIDTH x HP_BAR_HEIGHT whose inside starts with
    a bright, saturated fill (green on a healthy monster) at the left edge.
    :param region:  Optional (left, top, right, bottom) area to search, clipped to the frame.
    :return:        A list of (center x, center y, fill fraction) in frame coordinates.
    """

    left, top = 0, 0
    if region is not None:
        left, top = max(0, int(region[0])), max(0, int(region[1]))
        right, bottom = min(frame.shape[1], int(region[2])), min(frame.shape[0], int(region[3]))
        frame = frame[top:bottom, left:right]
    if frame.size == 0:
        return []
    bgr = frame[:, :, :3]
    dark = (bgr.max(axis=2) < HP_BAR_DARK).astype(np.uint8)
    hsv = cv2.cvtColor(np.ascontiguousarray(bgr), cv2.COLOR_BGR2HSV)
    fill = (hsv[:, :, 1] > 150) & (hsv[:, :, 2] > 150)

    bars = []
    count, _, stats, _ = cv2.connectedComponentsWithStats(dark)
    for x, y, w, h, area in stats[1:]:
        if not (HP_BAR_WIDTH[0] <= w <= HP_BAR_WIDTH[1] and HP_BAR_HEIGHT[0] <= h <= HP_BAR_HEIGHT[1]):
            continue
        # Inside the outer outline there can be a thin light ring, then the inner box holding the
        # fill, so only the inner box has to be all empty part (dark) or fill
        inner = (slice(y + 2, y + h - 2), slice(x + 2, x + w - 2))
        inside = fill[inner]
        if inside.size == 0:
            continue
        columns = np.nonzero(inside.any(axis=0))[0]
        if len(columns) < 2 or columns[0] > 4:          # The fill must start at the left edge
            continue
        if (dark[inner].astype(bool) | inside).mean() < 0.8:
            continue
        fraction = (columns[-1] - columns[0] + 1) / max(1, inside.shape[1] - 2 * columns[0])
        bars.append((left + int(x + w // 2), top + int(y + h // 2), min(1.0, fraction)))
    return bars


def clean_name(name):
    """Turns NAME into a safe file or folder name."""

    return re.sub(r'[^A-Za-z0-9 _-]+', '', str(name)).strip()


#################################
#     Template Preparation      #
#################################
def load_masked(path):
    """
    Loads the image at PATH as a (grayscale, mask) template pair. The mask comes from the
    image's transparency (saved cut-outs) and is None for images without any.
    """

    image = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if image is None:
        return None
    if image.ndim == 2:
        return image, None
    if image.shape[2] == 4:
        return cv2.cvtColor(image, cv2.COLOR_BGRA2GRAY), np.where(image[:, :, 3] > 0, 255, 0).astype(np.uint8)
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), None


def text_pixels(gray):
    """Returns a binary image of GRAY's pure white pixels, which is what nametag text is drawn in."""

    return np.where(gray >= TEXT_LEVEL, 255, 0).astype(np.uint8)


def text_template(nametag):
    """
    Converts a grayscale nametag crop into a binary template of just its white text, trimmed
    to the text. The see-through box behind the text changes with the background, the text doesn't.
    :return:    The template, or None if the crop has too little white text to use.
    """

    text = text_pixels(nametag)
    ys, xs = np.nonzero(text)
    if len(xs) < MIN_TEXT_PIXELS:
        return None
    pad = 2
    return text[max(0, ys.min() - pad):ys.max() + pad + 1, max(0, xs.min() - pad):xs.max() + pad + 1]


def trim_nametag(crop):
    """Trims a BGR nametag crop down to its text plus a small margin, if it has any white text."""

    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    ys, xs = np.nonzero(gray >= TEXT_LEVEL)
    if len(xs) < MIN_TEXT_PIXELS:
        return crop
    pad = 4
    return crop[max(0, ys.min() - pad):ys.max() + pad + 1, max(0, xs.min() - pad):xs.max() + pad + 1]


def cut_out(image, box, margin=40):
    """
    Separates the monster inside BOX from the background with GrabCut, using the area around the
    box as examples of background.
    :param image:   Full BGR screenshot.
    :param box:     (x1, y1, x2, y2) selection around the monster.
    :return:        A BGRA crop trimmed to the monster, transparent where the background was,
                    or None if no clear monster shape was found.
    """

    x1, y1, x2, y2 = box
    height, width = image.shape[:2]
    cx1, cy1 = max(0, x1 - margin), max(0, y1 - margin)
    cx2, cy2 = min(width, x2 + margin), min(height, y2 + margin)
    context = image[cy1:cy2, cx1:cx2].copy()
    mask = np.zeros(context.shape[:2], np.uint8)
    background, foreground = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
    try:
        cv2.grabCut(context, mask, (x1 - cx1, y1 - cy1, x2 - x1, y2 - y1),
                    background, foreground, 5, cv2.GC_INIT_WITH_RECT)
    except cv2.error:
        return None
    keep = np.isin(mask, (cv2.GC_FGD, cv2.GC_PR_FGD)).astype(np.uint8)
    keep = keep[y1 - cy1:y2 - cy1, x1 - cx1:x2 - cx1]

    # Keep only the largest blob, dropping bits of scenery GrabCut wasn't sure about
    count, labels, stats, _ = cv2.connectedComponentsWithStats(keep)
    if count < 2:
        return None
    largest = 1 + np.argmax(stats[1:, cv2.CC_STAT_AREA])
    keep = labels == largest
    coverage = keep.mean()
    if coverage < MIN_CUTOUT_COVERAGE or coverage > MAX_CUTOUT_COVERAGE:
        return None

    crop = cv2.cvtColor(image[y1:y2, x1:x2], cv2.COLOR_BGR2BGRA)
    crop[:, :, 3] = np.where(keep, 255, 0)
    bx, by, bw, bh = stats[largest, :4]
    return crop[by:by + bh, bx:bx + bw]


def draw_detection(frame, result, combat):
    """
    Draws a detection RESULT from Combat.detect onto a BGR copy of FRAME:
    cyan = player, green = monster in range, red = monster ignored, magenta = target,
    yellow box = platform band and engage range, orange lines = attack range.
    """

    img = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR) if frame.shape[2] == 4 else frame.copy()
    font = cv2.FONT_HERSHEY_SIMPLEX
    player = result['player'] if result else None
    if player is not None:
        px, py = int(player[0]), int(player[1])
        layer_height = int(combat.number('layer_height'))
        engage_range = int(combat.number('engage_range'))
        attack_range = int(combat.number('attack_range'))
        cv2.rectangle(img, (px - engage_range, py - layer_height),
                      (px + engage_range, py + layer_height), (0, 255, 255), 1)
        for x in (px - attack_range, px + attack_range):
            cv2.line(img, (x, py - layer_height), (x, py + layer_height), (0, 165, 255), 1)
        cv2.circle(img, (px, py), 8, (255, 255, 0), 2)
        cv2.putText(img, f"player {result['player_score']:.2f}", (px + 10, py - 10),
                    font, 0.45, (255, 255, 0), 1)
    for x, y, score, in_range in (result['mobs'] if result else []):
        color = (0, 255, 0) if in_range else (0, 0, 255)
        cv2.circle(img, (int(x), int(y)), 6, color, 2)
        cv2.putText(img, f'{score:.2f}', (int(x) + 8, int(y) + 4), font, 0.4, color, 1)
    for x, y, fill in (result.get('hp_bars', []) if result else []):
        cv2.rectangle(img, (x - 30, y - 8), (x + 30, y + 8), (255, 255, 255), 1)
        cv2.putText(img, f'HP {fill:.0%}', (x + 33, y + 4), font, 0.4, (255, 255, 255), 1)
    if result and result['target'] is not None:
        tx, ty = int(result['target'][0]), int(result['target'][1])
        cv2.circle(img, (tx, ty), 14, (255, 0, 255), 2)
        cv2.line(img, (int(player[0]), int(player[1])), (tx, ty), (255, 0, 255), 1)
    return img


def _match(gray, template, mask):
    """TM_CCOEFF_NORMED score map of TEMPLATE over GRAY, with MASK if given."""

    if mask is None:
        result = cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED)
    else:
        result = cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED, mask=mask)
    # Flat areas (e.g. no text at all) have no variance and come back as NaN or inf
    return np.nan_to_num(result, nan=-1, posinf=-1, neginf=-1)


def _coarse_to_fine(gray, small, template, mask, threshold):
    """
    Finds TEMPLATE in GRAY via SMALL (GRAY at COARSE_SCALE): the best-scoring spot of each area
    on SMALL, loosely thresholded, is rescored at full size around where it should be. Gives
    the same matches as scanning GRAY at full size in about a third of the time.
    :return:    A list of (score, top left x, top left y) in GRAY coordinates.
    """

    h, w = template.shape
    small_template = cv2.resize(template, None, fx=COARSE_SCALE, fy=COARSE_SCALE, interpolation=cv2.INTER_AREA)
    small_mask = np.where(cv2.resize(mask, None, fx=COARSE_SCALE, fy=COARSE_SCALE,
                                     interpolation=cv2.INTER_AREA) > 127, 255, 0).astype(np.uint8)
    if small_template.shape[0] > small.shape[0] or small_template.shape[1] > small.shape[1]:
        return []
    coarse = _match(small, small_template, small_mask)

    # One candidate per area (local maxima), otherwise a busy background's many near-duplicate
    # hits crowd the real monster out of the candidates
    size = max(3, min(small_template.shape) // 2) | 1
    peaks = (coarse == cv2.dilate(coarse, np.ones((size, size), np.uint8))) & \
            (coarse >= threshold - COARSE_MARGIN)
    ys, xs = np.nonzero(peaks)
    order = np.argsort(-coarse[ys, xs])[:COARSE_CANDIDATES]

    found = []
    pad = COARSE_PAD
    for y, x in zip(ys[order], xs[order]):
        fx, fy = int(x / COARSE_SCALE), int(y / COARSE_SCALE)
        x1, y1 = max(0, fx - pad), max(0, fy - pad)
        window = gray[y1:fy + h + pad, x1:fx + w + pad]
        if window.shape[0] < h or window.shape[1] < w:
            continue
        _, score, _, loc = cv2.minMaxLoc(_match(window, template, mask))
        if score >= threshold:
            found.append((float(score), x1 + loc[0], y1 + loc[1]))
    return found


def match_templates(gray, templates, threshold, region=None):
    """
    Finds every location in GRAY matching any of TEMPLATES with at least THRESHOLD confidence,
    merging overlapping hits of the same object.
    :param region:  Optional (left, top, right, bottom) area to search, clipped to the frame.
    :return:        A list of (x, y, score) centers in frame coordinates, best match first.
    """

    left, top = 0, 0
    if region is not None:
        left, top = max(0, int(region[0])), max(0, int(region[1]))
        right, bottom = min(gray.shape[1], int(region[2])), min(gray.shape[0], int(region[3]))
        gray = gray[top:bottom, left:right]

    hits = []
    small = None
    for template, mask in templates:
        h, w = template.shape
        if h > gray.shape[0] or w > gray.shape[1]:
            continue
        if mask is not None and min(h, w) >= COARSE_MIN_SIZE and gray.size >= COARSE_MIN_AREA:
            # Masked matching is slow, so on big areas find candidates on a half-size copy
            # first and only score those at full size
            if small is None:
                small = cv2.resize(gray, None, fx=COARSE_SCALE, fy=COARSE_SCALE, interpolation=cv2.INTER_AREA)
            for score, x, y in _coarse_to_fine(gray, small, template, mask, threshold):
                hits.append((score, left + x + w // 2, top + y + h // 2, w // 2, h // 2))
            continue
        result = _match(gray, template, mask)
        ys, xs = np.where(result >= threshold)
        if len(xs) > MAX_RAW_MATCHES:
            best = np.argpartition(result[ys, xs], -MAX_RAW_MATCHES)[-MAX_RAW_MATCHES:]
            ys, xs = ys[best], xs[best]
        for x, y in zip(xs, ys):
            hits.append((float(result[y, x]), left + int(x) + w // 2, top + int(y) + h // 2, w // 2, h // 2))

    hits.sort(reverse=True)
    kept = []
    for score, x, y, half_w, half_h in hits:
        if all(abs(x - k[0]) > half_w or abs(y - k[1]) > half_h for k in kept):
            kept.append((x, y, score))
    return kept
