# Prospecting! Automation Macro

Supported Game: https://www.roblox.com/games/129827112113663/Prospecting

An automated macro for the Roblox game *Prospecting!*, built with Python. It functions like the built in auto-pan without the debuffs. 
It reads on-screen prompts to automatically walk between digging and panning, tries to fix mistakes if you get stuck, and reports your stats to a Discord webhook.

> **Notice:** This project was vibe coded by Argon 4 and lightly cleaned up for general use.

---

## Game Policy & Safety

* **Macros are allowed** by the creator of *Prospecting!*.
* This tool uses standard Windows mouse and keyboard simulation. It does not inject code, modify memory, or alter the Roblox client.

---

## Screen & System Setup

* **Primary Screen:** Roblox must be running on your main monitor.
* **Resolution:** Designed for **`2560x1440` (Windowed / Borderless)**.
  * *Note:* At other resolutions like `1920x1080`, screen detection zones may need small tweaks.
* **OS:** Windows 10 or 11 (64-bit).

---

## What It Does

1. **Smart Movement:**
   - **Walks forward (`W`)** until the prompt switches to water/panning, then stops.
   - **Walks backward (`S`)** until the prompt switches back to digging dirt.
2. **Auto-Recovery:**
   - If it sees *"Not enough sand!"*, it backs up onto the dirt.
   - If it sees *"Your pan is full!"*, it steps forward deeper into the water to wash.
3. **Spade Minigame Solver:**
   - Watches the dig slider and releases the click right inside the green zone.
4. **Pan Fill Detection:**
   - Detects when the pan has gathered enough sand and heads straight to the water.
5. **Auto-Sell:**
   - Periodically opens the menu and sells your haul automatically.
6. **Discord Webhook Updates:**
   - Posts status updates (runtime, cycles, perfect dig rate, and earnings) to your Discord channel.

---

## Hotkeys

| Hotkey | Action |
| :--- | :--- |
| **`F1`** | **Start Macro** (focuses Roblox window) |
| **`F2`** | **Pause / Resume** |
| **`F3`** | **Emergency Stop** (releases all keys/mouse clicks) |
| **`F4`** | **Save Debug Screenshot** (creates `debug_vision.png` to check boxes) |
| **`F5`** | **Test Auto-Sell** |
| **`F6`** | **Test Walking Logic** |

---

## Setup

1. **Install Python 3.10+ (64-bit)**.
2. **Install required packages:**
   ```bash
   pip install opencv-python mss numpy
   ```
3. *(Optional, for reading cash balance via OCR and fallbacks)* Install [Tesseract-OCR](https://github.com/tesseract-ocr/tesseract) for Windows and the Python wrapper:
   ```bash
   pip install pytesseract
   ```
4. **In Roblox:**
   - Set the game to **Windowed mode** on your primary monitor (`2560x1440`).
   - Equip your shovel and pan, and stand near the water's edge facing the lake.
5. **Launch:**
   ```bash
   python kaeProspect.py
   ```
   Add your Discord webhook URL in the settings tab if you want updates, then press **`F1`** to start.

   May receive updates, please be cautious with other macros/tools as during my research I came across many malicious results.
