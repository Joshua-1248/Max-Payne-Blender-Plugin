# Max Payne Level Tools – Blender Plugin

A Blender plugin for importing and exporting level data from **Max Payne** and **Max Payne 2**, including **LDB**, **LVL**, **KFS**, **KF2**, and **SKD** files.

This plugin allows you to open Max Payne's proprietary file formats inside Blender, edit or view level geometry, animations, and skeletons, and export them back for use in the game.

> **⚠️ Important:** This plugin requires you to own a legitimate copy of Max Payne or Max Payne 2 and have the official MaxED tool installed for certain workflows.

---

## Supported File Formats

| Format | Extension | Purpose |
| :--- | :--- | :--- |
| **LDB** | `.ldb` | Level database files containing level geometry, objects, and collision data. |
| **LVL** | `.lvl` | Compiled level files used by the game engine. |
| **KFS** | `.kfs` | Keyframe animation files (character animations, object animations). |
| **KF2** | `.kf2` | Updated keyframe animation format (used in Max Payne 2). |
| **SKD** | `.skd` | Skeleton definition files (rigging and bone structures for characters). |

---

## Features

- **Import LDB/LVL files** – Load Max Payne and Max Payne 2 level geometry and object data into Blender.
- **Export LDB/LVL files** – Save Blender scenes back to Max Payne level formats.
- **Import KFS/KF2 animations** – Load character and object animations for preview or editing (both games).
- **Export KFS/KF2 animations** – Export Blender animations to Max Payne keyframe formats.
- **Import SKD skeletons** – Load character skeleton rigs for animation work (both games).
- **Export SKD skeletons** – Export Blender armatures to Max Payne skeleton format.
- **Preserves game data** – Maintains original level structure, collision data, object placement, and animation data where supported.

---

## Requirements

- **Blender 2.79 or 4.0+** – This plugin works with Blender 2.79 and has been tested with Blender 4.0. Other versions may work but are not officially tested.
- **MaxED (Max Payne Level Editor)** – Required for opening certain files in the game. You must own a legitimate copy of Max Payne or Max Payne 2 to legally use MaxED.
- **Python 3.x** – Included with Blender.

---

## Installation

1. **Download the plugin:**
   - Clone this repository or download the ZIP from GitHub:
     ```bash
     git clone https://github.com/Joshua-1248/Max-Payne-Blender-Plugin.git
