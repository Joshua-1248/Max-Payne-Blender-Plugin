# Max Payne Level Tools – Blender Plugin

A Blender add-on plugin for importing data from **Max Payne** and **Max Payne 2**, including **LVL**, **LDB**, **KFS**, **KF2**, and **SKD** files.

> **🚧 Work in Progress:** LVL import is currently **unfinished** and not yet functional. All other formats (LDB, KFS, KF2, SKD) are fully or partially supported as **importers only**. Exporters are not currently available.

This plugin allows you to open Max Payne's proprietary file formats directly inside Blender to view and edit level geometry, animations, and skeletons.

---

## Supported File Formats

| Format | Extension | Purpose | Status |
| :--- | :--- | :--- | :--- |
| **LVL** | `.lvl` | **Uncompiled maps** — editable level source files used for development and editing. | 🚧 **Unfinished** (import in progress) |
| **LDB** | `.ldb` | **Compiled maps** — optimized level files ready for the game engine. | ✅ Importer available |
| **KFS** | `.kfs` | Keyframe animation files (character animations, object animations). | ✅ Importer available |
| **KF2** | `.kf2` | Updated keyframe animation format (used in Max Payne 2). | ✅ Importer available |
| **SKD** | `.skd` | Skeleton definition files (rigging and bone structures for characters). | ✅ Importer available |

> **Note:** Exporters are not currently available for any format. This tool is **import-only** at this time.

---

## Features

- **Import LVL files** — 🚧 Currently **unfinished**; coming soon.
- **Import LDB files** – Load compiled Max Payne and Max Payne 2 level geometry and object data into Blender. ✅
- **Import KFS/KF2 animations** – Load character and object animations for preview or editing (both games). ✅
- **Import SKD skeletons** – Load character skeleton rigs for animation work (both games). ✅
- **Preserves game data** – Maintains original structure, collision data, object placement, and animation data where supported.

> **Note:** This tool currently only supports **importing**. Export functionality is not yet available.

---

## Requirements

- **Blender 2.79 or 4.0+** – This plugin works with Blender 2.79 and has been tested with Blender 4.0. Other versions may work but are not officially tested.
- **Python 3.x** – Included with Blender.

---

## Installation

1. **Download the plugin:**
   - Clone this repository or download the ZIP from GitHub:
     ```bash
     git clone https://github.com/Joshua-1248/Max-Payne-Blender-Plugin.git
