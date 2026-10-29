"""
Movement paths taught by demonstration: the player records a tricky route once (climbing ropes,
jumping between platforms) and the bot replays it, checking its minimap position along the way.
"""

import os
import json
import time
import heapq
import threading
import keyboard
from src.common import config, settings, utils
from src.common.vkeys import key_down, key_up


PATHS_DIR = os.path.join('resources', 'paths')

# A recording is split into segments wherever no key is held for this long (s)
SEGMENT_GAP = 0.4

# How often (s) the player's minimap position is sampled while recording
SAMPLE_INTERVAL = 0.05

# How long (ms) the driver capture waits for a keystroke before checking whether to stop, and
# how long (s) after a recording stops the record key is ignored, so one press can't stop a
# recording and start a new one
CAPTURE_WAIT_MS = 50
RESTART_GUARD = 1.0

# How close (minimap units) the player must be to a segment's start before replaying it, and
# to its end afterwards for it to count as done. The start is tight because jumps land
# differently from even slightly different take-off spots
START_TOLERANCE = 0.008
END_TOLERANCE = 0.03

# Time (s) to let the player settle after a segment, and how many times a failed path is resumed
SETTLE_TIME = 0.3
MAX_RETRIES = 3

# Route planning: rough walking speed (minimap units per second) and the extra time counted for
# each path taken (walking to its start and lining up), only used to compare routes
WALK_SPEED = 0.12
PATH_OVERHEAD = 1.0
DROP_COST = 1.5             # Extra time counted for dropping down to a lower level

# A segment that rises while moving sideways less than this (minimap units) is a rope climb
CLIMB_MAX_DRIFT = 0.02


#################################
#           Recording           #
#################################
class PathRecorder:
    """Records the player's key presses and minimap positions while they move."""

    def __init__(self):
        self.recording = False
        self.name = None
        self.events = []            # (time, 'down' or 'up', key)
        self.samples = []           # (time, x, y)
        self.held = set()
        self.ignored = set()
        self.stop_key = None
        self.capture = None
        self.hook = None
        self.stopped_at = 0
        self.lock = threading.Lock()

    def toggle(self, name=None):
        """Starts recording, or stops and saves. Returns the saved path's name when stopping."""

        if self.recording:
            return self.stop()
        if time.time() - self.stopped_at < RESTART_GUARD:
            return None             # The same key press that just stopped a recording
        self.start(name)

    def start(self, name=None):
        self.name = clean_path_name(name) or time.strftime('path_%Y%m%d_%H%M%S')
        self.events, self.samples, self.held = [], [], set()
        # Never record Auto Maple's own hotkeys (e.g. the one that stops the recording)
        self.ignored = {str(k).lower() for k in config.listener.config.values()}
        self.stop_key = str(config.listener.config.get('Record path', '')).lower()
        self.recording = True

        # MapleStory hides key presses from normal Windows keyboard hooks while it's the active
        # window, so read them from the Interception driver, below the game, when it's installed
        self.capture = DriverKeyCapture(self._record)
        if not self.capture.start():
            self.capture = None
            self.hook = keyboard.hook(lambda e: self._record((e.name or '').lower(), e.event_type))
            print('\n[!] Interception driver not available: keys pressed while MapleStory is the '
                  'active window may not be recorded')
        threading.Thread(target=self._sample, daemon=True).start()
        how = f'or press {self.stop_key} (works in the game)' if self.capture else ''
        print(f"\n[~] Recording path '{self.name}'. Move in the game, then press Stop recording {how}")

    def stop(self):
        with self.lock:
            if not self.recording:
                return None
            self.recording = False
            self.stopped_at = time.time()
        if self.capture is not None:
            self.capture.stop()
            self.capture = None
        if self.hook is not None:
            keyboard.unhook(self.hook)
            self.hook = None
        path = build_path(self.name, self.events, self.samples)
        if path is None:
            print('\n[!] Path not saved: no movement was recorded')
            return None
        save_path(path)
        print(f"\n[~] Saved path '{self.name}' with {len(path['segments'])} segment(s), "
              f"add it to a point from Edit > Recorded Paths")
        try:
            config.gui.edit.paths.refresh()
        except AttributeError:
            pass
        return self.name

    def _record(self, key, kind):
        """Records KEY going KIND ('down' or 'up')."""

        if not self.recording or not key:
            return
        if key == self.stop_key and self.capture is not None:
            # The stop key works in the game too, since the driver sees it
            if kind == 'down':
                threading.Thread(target=self._stop_from_key, daemon=True).start()
            return
        if key in self.ignored:
            return
        if kind == 'down':
            if key in self.held:            # Auto-repeat while held
                return
            self.held.add(key)
        else:
            self.held.discard(key)
        self.events.append((time.time(), kind, key))

    def _stop_from_key(self):
        self.stop()
        try:
            config.gui.edit.paths.refresh()     # Show 'Record' again
        except AttributeError:
            pass

    def _sample(self):
        while self.recording:
            self.samples.append((time.time(), *config.player_pos))
            time.sleep(SAMPLE_INTERVAL)


class DriverKeyCapture:
    """
    Reads keystrokes from the Interception driver, below the game and its anti-cheat, so they're
    seen even while MapleStory is the active window. Each one is passed on immediately, so
    playing and typing are unaffected, and the capture is removed when stopped.
    """

    def __init__(self, on_key):
        self.on_key = on_key            # Called with (key name, 'down' or 'up')
        self.context = None
        self.running = False
        self.thread = None

    def start(self):
        """Starts capturing. Returns False if the Interception driver isn't available."""

        try:
            from interception import Interception
            context = Interception()
        except Exception:
            return False
        if not context.valid:
            context.destroy()
            return False
        self.context = context
        self.running = True
        self.thread = threading.Thread(target=self._main, daemon=True)
        self.thread.start()
        return True

    def stop(self):
        self.running = False
        if self.thread is not None and self.thread is not threading.current_thread():
            self.thread.join(1)

    def _main(self):
        from interception.constants import FilterKeyFlag, KeyFlag
        from interception.strokes import KeyStroke

        context = self.context
        try:
            context.set_filter(context.is_keyboard, FilterKeyFlag.FILTER_KEY_ALL)
            while self.running:
                device = context.await_input(CAPTURE_WAIT_MS)
                if device is None:
                    continue
                stroke = context.devices[device].receive()
                if stroke is None:
                    continue
                context.send(device, stroke)        # Pass it on first, so the game gets it right away
                if isinstance(stroke, KeyStroke):
                    name = key_name(stroke.code, bool(stroke.flags & KeyFlag.KEY_E0))
                    try:
                        self.on_key(name, 'up' if stroke.flags & KeyFlag.KEY_UP else 'down')
                    except Exception as e:
                        print(f'[!] Path recording: {e}')
        finally:
            # Always stop intercepting, or the keyboard would stop working
            context.set_filter(context.is_keyboard, FilterKeyFlag.FILTER_KEY_NONE)
            context.destroy()


_key_names = {}


def key_name(scan_code, extended):
    """
    Returns the key name (as Interception and the bot use them, e.g. 'left', 'alt') for a scan
    code, or None for keys without one.
    """

    if not _key_names:
        from interception import _keycodes
        for name in sorted(_keycodes._MAPPING, key=lambda n: (len(n), n)):     # Shortest name wins
            try:
                info = _keycodes.get_key_information(name)
            except Exception:
                continue
            if info.shift or info.ctrl or info.alt or not info.scan_code:
                continue                # Characters like '@' that need a modifier
            _key_names.setdefault((info.scan_code, bool(info.is_extended)), name)
    return _key_names.get((scan_code, extended))


def build_path(name, events, samples):
    """
    Splits recorded EVENTS into segments separated by gaps of SEGMENT_GAP with no keys held,
    each with its start and end positions taken from SAMPLES.
    :return:    The path as a dict, or None if nothing usable was recorded.
    """

    if not events or not samples:
        return None
    segments, current, held, released_at = [], [], set(), None
    for t, kind, key in events:
        if kind == 'down' and not held and current and released_at is not None \
                and t - released_at >= SEGMENT_GAP:
            segments.append(current)
            current = []
        if kind == 'down':
            held.add(key)
        else:
            held.discard(key)
        current.append((t, kind, key))
        released_at = t if not held else None
    if current:
        segments.append(current)

    result = []
    for i, seg in enumerate(segments):
        start_t = seg[0][0]
        # A segment ends where the next one starts, or at the end of the recording
        end_t = segments[i + 1][0][0] if i + 1 < len(segments) else samples[-1][0]
        result.append({
            'start': position_at(samples, start_t),
            'end': position_at(samples, end_t),
            'events': [(round(t - start_t, 3), kind, key) for t, kind, key in seg]
        })
    return {'name': name, 'start': result[0]['start'], 'end': result[-1]['end'], 'segments': result}


def position_at(samples, t):
    """Returns the sampled (x, y) closest in time to T."""

    sample = min(samples, key=lambda s: abs(s[0] - t))
    return [round(sample[1], 4), round(sample[2], 4)]


#################################
#            Storage            #
#################################
def clean_path_name(name):
    """Turns NAME into a safe file name, or None if nothing is left."""

    cleaned = ''.join(c for c in str(name or '') if c.isalnum() or c in ' _-').strip()
    return cleaned or None


def save_path(path):
    os.makedirs(PATHS_DIR, exist_ok=True)
    with open(os.path.join(PATHS_DIR, path['name'] + '.json'), 'w') as file:
        json.dump(path, file, indent=1)


def load_path(name):
    file_path = os.path.join(PATHS_DIR, f'{clean_path_name(name)}.json')
    if not os.path.isfile(file_path):
        return None
    with open(file_path) as file:
        return json.load(file)


def list_paths():
    if not os.path.isdir(PATHS_DIR):
        return []
    return sorted(os.path.splitext(n)[0] for n in os.listdir(PATHS_DIR) if n.endswith('.json'))


def delete_path(name):
    file_path = os.path.join(PATHS_DIR, f'{clean_path_name(name)}.json')
    if os.path.isfile(file_path):
        os.remove(file_path)


_cache = {'key': None, 'paths': []}


def all_paths():
    """Returns every saved path, re-reading the files only when they change."""

    names = list_paths()
    key = tuple((n, os.path.getmtime(os.path.join(PATHS_DIR, n + '.json'))) for n in names)
    if key != _cache['key']:
        _cache['key'] = key
        _cache['paths'] = [p for p in (load_path(n) for n in names) if p is not None]
    return _cache['paths']


#################################
#        Route Planning         #
#################################
def same_level(a, b):
    """Whether positions A and B are on the same level, so one can be walked to from the other."""

    return abs(a[1] - b[1]) <= settings.move_tolerance


def path_duration(path):
    """Roughly how long (s) replaying PATH takes."""

    return sum(max((e[0] for e in s['events']), default=0) + SETTLE_TIME for s in path['segments'])


def plan_route(start, target, paths):
    """
    Finds the quickest way from START to TARGET by walking along levels, dropping down to lower
    ones and taking PATHS (each one-way, from its start to its end), like a map of the level's
    connections. Going up needs a path; going down the normal movement drops through platforms.
    :return:    The paths to take in order: [] if TARGET can be reached without any, or None if
                no combination of walking, dropping and paths reaches it.
    """

    nodes = [tuple(start), tuple(target)]
    for path in paths:
        nodes += [tuple(path['start']), tuple(path['end'])]

    # A level with a recorded way down probably can't just be dropped from (or lands somewhere
    # bad), so dropping is only assumed from levels without one
    ways_down = [p['start'] for p in paths if p['end'][1] > p['start'][1] and not same_level(p['start'], p['end'])]

    def neighbours(i):
        can_drop = not any(same_level(nodes[i], s) for s in ways_down)
        for j, node in enumerate(nodes):
            if j == i or j == 0:
                continue
            walk = abs(node[0] - nodes[i][0]) / WALK_SPEED
            if same_level(nodes[i], node):
                yield j, walk, None
            elif can_drop and node[1] > nodes[i][1]:        # Lower down (minimap y grows downward)
                yield j, walk + DROP_COST, None
        if i >= 2 and i % 2 == 0:               # The start of a path leads to its end
            path = paths[(i - 2) // 2]
            yield i + 1, path_duration(path) + PATH_OVERHEAD, path

    best = {0: 0}
    previous = {}
    queue = [(0, 0)]
    while queue:
        cost, i = heapq.heappop(queue)
        if i == 1:
            route = []
            while i in previous:
                i, path = previous[i]
                if path is not None:
                    route.append(path)
            return route[::-1]
        if cost > best.get(i, float('inf')):
            continue
        for j, step, path in neighbours(i):
            if cost + step < best.get(j, float('inf')):
                best[j] = cost + step
                previous[j] = (i, path)
                heapq.heappush(queue, (cost + step, j))
    return None


#################################
#            Replay             #
#################################
def run_path(path, move_to, climb=None):
    """
    Replays PATH segment by segment. Before each segment the player is moved to its start with
    MOVE_TO(x, y) if needed, and afterwards must be near its end. When a segment fails (e.g. a
    missed jump), replay resumes from the segment starting nearest to where the player ended up.
    :param climb:   Optional CLIMB(x, y) that gets onto a rope at x and climbs it to height y.
                    Used instead of replaying the keys for segments that go up a rope, since
                    feeling for the rope works better than the recorded keys from a slightly
                    different spot.
    :return:        Whether the player reached the end of the path.
    """

    segments = path['segments']
    index, retries = 0, 0
    while config.enabled and index < len(segments):
        segment = segments[index]
        if utils.distance(config.player_pos, segment['start']) > START_TOLERANCE:
            move_to(*segment['start'])
        if climb is not None and is_climb(segment):
            climb(segment['start'][0], segment['end'][1])
        else:
            play_events(segment['events'])
        time.sleep(SETTLE_TIME)
        if utils.distance(config.player_pos, segment['end']) <= END_TOLERANCE:
            index += 1
            continue
        retries += 1
        if retries > MAX_RETRIES:
            print(f"[!] Path '{path['name']}': gave up after {MAX_RETRIES} retries")
            return False
        index = min(range(len(segments)), key=lambda i: utils.distance(config.player_pos, segments[i]['start']))
        print(f"[~] Path '{path['name']}': segment missed, retrying from segment {index + 1}")
    return config.enabled


def is_climb(segment):
    """Whether SEGMENT goes up a rope: up was pressed and it ends clearly higher, nearly straight up."""

    (x1, y1), (x2, y2) = segment['start'], segment['end']
    pressed_up = any(kind == 'down' and key == 'up' for _, kind, key in segment['events'])
    return pressed_up and y1 - y2 > settings.move_tolerance / 2 and abs(x2 - x1) <= CLIMB_MAX_DRIFT


def play_events(events):
    """Presses and releases keys with the recorded timing, releasing anything still held."""

    start = time.time()
    held = set()
    try:
        for t, kind, key in events:
            if not config.enabled:
                return
            delay = start + t - time.time()
            if delay > 0:
                time.sleep(delay)
            if kind == 'down':
                key_down(key)
                held.add(key)
            else:
                key_up(key)
                held.discard(key)
    finally:
        for key in held:
            key_up(key)
