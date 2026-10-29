# Combat templates

Combat finds your character and monsters on the main game screen with template matching.
The easiest way to add templates is the **Monsters** tab:

1. Press **Capture** (Auto Maple hides itself for a moment so it doesn't cover the game),
   or **Open image...** to use a frame from `recordings/`.
2. Drag a box around your **nametag** and press **Save as my nametag**. The nametag is used
   because skins, mounts and skill effects change how your character's body looks.
3. Make a **monster set** for the map with **New set**. Name it like the routine file
   (e.g. `CCMottledForest2`) and it is picked automatically whenever that routine loads.
4. Drag a tight box around each monster type in the map, name it and press **Save as monster**.
   Facing direction doesn't matter, mirrored copies are matched automatically.
5. Tick **Show matches** to check what the current set detects on the screenshot.

Files: `player.png` is the nametag; monsters live in `mobs/<set name>/<monster>.png`.
Images placed directly in `mobs/` form the "(ungrouped)" set.
