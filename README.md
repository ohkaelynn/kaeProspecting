# Prospecting! Automation Macro

[![Game Link](https://img.shields.io/badge/Roblox-Prospecting!-00A2FF?logo=roblox&logoColor=white)](https://www.roblox.com/games/129827112113663/Prospecting)

An automated macro for the Roblox game [*Prospecting!*](https://www.roblox.com/games/129827112113663/Prospecting), built in Python. It mimics the mechanics of the built-in auto-pan feature without applying the in-game debuffs.

The script reads on-screen visual prompts to navigate seamlessly between digging and panning, handles common recovery scenarios if you get stuck, and streams session telemetry directly to your Discord server via webhooks.

> **Notice:** This project was originally vibe coded by **Argon 4** and lightly cleaned up for general community use.

---

## Game Policy & Safety

* **Macro Compliance:** Macros are officially permitted by the developer of *Prospecting!*.
* **Client Integrity:** This tool relies solely on standard Windows OS mouse and keyboard input simulation (`SendInput`). It does not inject DLLs, inspect memory, or modify the Roblox client in any way.
* **Security Reminder:** Be cautious when downloading third-party macros and scripts—many tools circulating online contain malicious payloads. Always verify source code before running automation tools.

---

## System Requirements

* **Operating System:** Windows 10 or 11 (64-bit).
* **Display Configuration:**
  * **Monitor:** Must run on your **Primary Display**.
  * **Resolution:** Calibrated for **`2560x1440` (Maximized / Windowed)**.
  * *Note:* At other resolutions (e.g., `1920x1080`), detection regions may require manual coordinate adjustments.

---

## Core Features

* **Visual Navigation:**
  * **Walk Forward (`W`):** Holds movement until the screen prompt registers water/panning, then stops.
  * **Walk Backward (`S`):** Returns to dirt once panning completes and the dig prompt reappears.
* **Automatic Error Recovery:**
  * Backs up onto dirt if an alert reads *"Not enough sand!"*.
  * Moves deeper into the water if an alert reads *"Your pan is full!"*.
* **Spade Minigame Timing:**
  * Monitors the dig slider track and releases the mouse click precisely when hitting the green sweet spot.
* **Pan Fill Detection:**
  * Measures sand accumulation inside the pan and triggers transition to water when full.
* **Auto-Sell Routine:**
  * Periodically opens the merchant interface, executes the sell sequence, and resumes farming.
* **Discord Telemetry:**
  * Emits clean webhook embeds tracking runtime, cycle counts, perfect dig success rate, and balance progression.

---

## Hotkeys

| Hotkey | Action | Description |
| :--- | :--- | :--- |
| **`F1`** | **Start Macro** | Focuses the Roblox window and begins execution |
| **`F2`** | **Pause / Resume** | Temporarily halts or resumes input loops |
| **`F3`** | **Emergency Stop** | Immediately stops all operations and releases held keys/clicks |
| **`F4`** | **Debug Snapshot** | Generates `debug_vision.png` to inspect screen bounding boxes |
| **`F5`** | **Test Auto-Sell** | Manually runs the auto-sell routine for calibration |
| **`F6`** | **Test Walk Logic** | Runs a single movement cycle to test threshold alignment |

---

## Installation & Setup

### 1. Environment Setup

Ensure **Python 3.10+ (64-bit)** is installed and added to your system `PATH`. Then install the core dependencies:

```bash
pip install opencv-python mss numpy
```

*(Optional)* If you want automated cash-balance tracking via OCR:
1. Download and install [Tesseract-OCR for Windows](https://github.com/tesseract-ocr/tesseract).
2. Install the Python bindings:
   ```bash
   pip install pytesseract
   ```

---

### 2. In-Game Positioning

1. Launch Roblox and keep the window maximized on your **primary monitor** at `2560x1440`.
2. Equip your **pan**.
3. Position your character at the shoreline facing directly toward the water.

---

### 3. Running the Macro

1. Run the script from your terminal:
   ```bash
   python kaeProspect.py
   ```
2. *(Optional)* Input your Discord webhook URL in the settings interface to enable progress logging.
3. Switch to Roblox and press **`F1`** to begin the macro. Press **`F3`** at any point to stop.
