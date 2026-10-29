"""
Runs combat detection over recorded frames and writes annotated copies, so thresholds and
ranges can be tuned without the game running.

Usage (from the repository root):
    python tools/replay_combat.py recordings/<folder> [--threshold 0.75] [--layer-height 80]
                                  [--offset -40] [--out <folder>]

Uses the templates in assets/combat and the saved Combat settings; flags override settings
for this run only. Colours: cyan player, green monster in range, red monster ignored,
magenta target, yellow platform band, orange attack range.
"""

import argparse
import os
import sys
import time
import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.modules.combat import Combat, draw_detection


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('frames', help='folder of recorded frames')
    parser.add_argument('--out', help='folder for annotated frames (default: <frames>/annotated)')
    parser.add_argument('--threshold', type=float)
    parser.add_argument('--layer-height', type=float)
    parser.add_argument('--engage-range', type=float)
    parser.add_argument('--attack-range', type=float)
    parser.add_argument('--offset', type=float, help='nametag to body offset (px)')
    args = parser.parse_args()

    combat = Combat()
    overrides = {'threshold': args.threshold, 'layer_height': args.layer_height,
                 'engage_range': args.engage_range, 'attack_range': args.attack_range,
                 'player_offset_y': args.offset}
    for key, value in overrides.items():
        if value is not None:
            combat.config[key] = value
    if combat.player_template is None or not combat.mob_templates:
        sys.exit("Missing templates: add assets/combat/player.png and images in assets/combat/mobs/")

    out = args.out or os.path.join(args.frames, 'annotated')
    os.makedirs(out, exist_ok=True)
    names = sorted(n for n in os.listdir(args.frames) if n.lower().endswith('.png'))
    if not names:
        sys.exit(f"No .png frames in '{args.frames}'")

    found_player = with_target = total_mobs = 0
    elapsed = 0
    for name in names:
        frame = cv2.imread(os.path.join(args.frames, name), cv2.IMREAD_UNCHANGED)
        if frame is None or frame.ndim != 3:
            continue
        start = time.perf_counter()
        result = combat.detect(frame, whole_frame=True)
        elapsed += time.perf_counter() - start
        cv2.imwrite(os.path.join(out, name), draw_detection(frame, result, combat))

        player = result['player'] is not None
        mobs = len(result['mobs'])
        in_range = sum(1 for m in result['mobs'] if m[3])
        found_player += player
        with_target += result['target'] is not None
        total_mobs += mobs
        score = f"{result['player_score']:.2f}" if player else '----'
        print(f'{name}: player {score}  mobs {mobs:2d} ({in_range} in range)'
              f"{'  -> target' if result['target'] is not None else ''}")

    n = len(names)
    print(f'\n{n} frames: player found in {found_player} ({found_player / n:.0%}), '
          f'target in {with_target}, avg {total_mobs / n:.1f} monsters/frame, '
          f'{elapsed / n * 1000:.0f} ms/frame (whole-frame search)')
    print(f"Annotated frames written to '{out}'")


if __name__ == '__main__':
    main()
