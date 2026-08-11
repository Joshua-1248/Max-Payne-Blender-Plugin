# Max Payne Level Tools – Blender Plugin

A Blender 2.79 plugin for importing and exporting level data from Max Payne file formats, including **LDB**, **LVL**, **KFS**, **KF2**, and **SKD** files.

This plugin allows you to open Max Payne's proprietary file formats inside Blender, edit or view level geometry, animations, and skeletons, and export them back for use in the game.

> **⚠️ Important:** This plugin requires you to own a legitimate copy of Max Payne and have the official MaxED tool installed for certain workflows.

---

## Supported File Formats

| Format | Extension | Purpose |
| :--- | :--- | :--- |
| **LDB** | `.ldb` | Level database files containing level geometry, objects, and collision data. |
| **LVL** | `.lvl` | Compiled level files used by the game engine. |
| **KFS** | `.kfs` | Keyframe animation files (character animations, object animations). |
| **KF2** | `.kf2` | Updated keyframe animation format (used in later Max Payne versions). |
| **SKD** | `.skd` | Skeleton definition files (rigging and bone structures for characters). |

---

## Features

- **Import LDB/LVL files** – Load Max Payne level geometry and object data into Blender.
- **Export LDB/LVL files** – Save Blender scenes back to Max Payne level formats.
- **Import KFS/KF2 animations** – Load character and object animations for preview or editing.
- **Export KFS/KF2 animations** – Export Blender animations to Max Payne keyframe formats.
- **Import SKD skeletons** – Load character skeleton rigs for animation work.
- **Export SKD skeletons** – Export Blender armatures to Max Payne skeleton format.
- **Preserves game data** – Maintains original level structure, collision data, object placement, and animation data where supported.

---

## Requirements

- **Blender 2.79** – This plugin is built for Blender 2.79. It may not work with newer versions.
- **MaxED (Max Payne Level Editor)** – Required for opening certain files in the game. You must own a legitimate copy of Max Payne to legally use MaxED.
- **Python 3.x** – Included with Blender 2.79.

---

## Installation

1. **Download the plugin:**
   - Clone this repository or download the ZIP from GitHub:
     ```bash
     git clone https://github.com/Joshua-1248/Max-Payne-Blender-Plugin.git
