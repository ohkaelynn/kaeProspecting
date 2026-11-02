# kaeProspect — Prospecting! Automation Macro

[![Game Link](https://img.shields.io/badge/Roblox-Prospecting!-00A2FF?logo=roblox&logoColor=white)](https://www.roblox.com/games/129827112113663/Prospecting)

A Python automation macro for the Roblox game [*Prospecting!*](https://www.roblox.com/games/129827112113663/Prospecting). It replicates the core digging and panning loop without the in-game auto-pan debuffs, using screen-space vision to time spade releases, detect prompts, recover from shoreline drift, and track session earnings.

> **Credits:** Created by **kaelynn** & **Argon 4 (Antigravity)** for public community release.

---

## Video Showcase

[![Watch the Demo on YouTube](https://img.youtube.com/vi/V2zM7D6ncGg/maxresdefault.jpg)](https://youtu.be/V2zM7D6ncGg)

*Click the thumbnail above or watch directly on YouTube: **[https://youtu.be/V2zM7D6ncGg](https://youtu.be/V2zM7D6ncGg)***

---

## Interface & Screenshots

### Main Page (Dashboard & Logs)
Tracks live runtime, cycle counts, perfect dig accuracy, cash balance/hourly rate, and color-coded event logs.

![Main Page - Dashboard & Logs](assets/main.png)

### Movement Page
Configures shoreline navigation (`Vision`, `Mirror`, or `Fixed`) and `W`/`S` walk durations with drift trim adjustments.

![Movement Page](assets/movement.png)

### Dig & Pan Settings
Controls spade hold-and-release timing, scoop cooldowns, fallback dig limits, and pan wash timeouts.

![Dig & Pan Settings](assets/dig_pan.png)

### Auto-Sell Settings
Configures periodic backpack selling, camera zoom scroll notches, and a 3-second interactive Sell coordinate picker.

![Auto-Sell Settings](assets/autosell.png)

### Webhook Settings
Sends periodic, non-spammy session summary embeds to Discord with runtime, cycles, perfect dig rate, and cash progression.

![Webhook Settings](assets/webhook.png)
![Discord Webhook Summary Embed](assets/webhook_embed.png)

---

## Game Policy & Safety

* **Macro Compliance:** Macros are permitted by the developers of *Prospecting!*.
* **Client Integrity:** Uses standard Windows `SendInput` events and screen capture (`mss`). It does not read/write process memory, inject DLLs, or modify the Roblox client.
* **Security Reminder:** Always inspect the source code of any macro before running it on your machine.

---

## System Requirements

* **OS:** Windows 10 or 11 (64-bit)
* **Python:** Python 3.10+ (64-bit)
* **Display:** Run Roblox in **Windowed** or **Maximized Windowed** mode on your **Primary Monitor** (uses client-area normalized coordinates, calibrated at `2560x1440` / 16:9).

---

## Features

* **Spade Minigame Vision:** Tracks the moving slider bar and releases LMB at the top green target zone.
* **Three Movement Modes:**
  * **Vision (Default):** Watches on-screen interaction prompts (`Collect Deposit` vs. `Pan`/`Shake`) to stop at water and dirt automatically.
  * **Mirror:** Stops on the water prompt walking forward (`W`) and matches that exact duration walking back (`S`).
  * **Fixed:** Uses static walk timings as a failsafe.
* **Debounced Alert Recovery:**
  * Steps back toward dirt (`S`) if *"Not enough sand!"* appears.
  * Steps forward into water (`W`) if *"Your pan is full!"* appears while washing.
* **Auto-Sell Routine:** Periodically zooms out, opens the backpack (`~`), clicks your configured Sell button coordinate, and resumes farming. Includes a 3-second coordinate picker in the GUI.
* **Cash OCR & Discord Webhooks:** Tracks current balance (both abbreviated `5.56B` and full comma-separated numbers), total money gained, and hourly rate, with optional periodic Discord summary embeds.

---

## Hotkeys

| Hotkey | Action | Description |
| :--- | :--- | :--- |
| **`F1`** | **Start** | Locks onto the Roblox window and starts the macro loop |
| **`F2`** | **Pause / Resume** | Pauses or resumes the macro and releases held inputs |
| **`F3`** | **Stop** | Stops execution immediately and releases all keys/clicks |
| **`F4`** | **ROI Snapshot** | Saves `debug_vision.png` with bounding boxes for all vision regions |
| **`F5`** | **Test Auto-Sell** | Runs the auto-sell sequence once to verify coordinates |
| **`F6`** | **Test Walk Parity** | Runs a single `W -> Wait -> S` walk cycle to test shoreline alignment |

---

## Setup & Usage

### 1. Install Dependencies

```bash
pip install opencv-python mss numpy
```

*(Optional — Required for Cash OCR & Text Alert Fallback)*:
1. Install [Tesseract-OCR for Windows](https://github.com/tesseract-ocr/tesseract) and ensure `tesseract` is on your system `PATH`.
2. Install the Python wrapper:
   ```bash
   pip install pytesseract
   ```

### 2. In-Game Setup

1. Open Roblox on your primary monitor and equip your **Pan**.
2. Stand on the dirt shoreline facing directly toward the water so `Collect Deposit` is visible.

### 3. Run the Macro

```bash
python kaeProspect.py
```

1. Adjust movement, dig timing, auto-sell, or Discord webhook settings in the GUI if needed, then click **Save Config**.
2. Press **`F1`** to start farming, **`F2`** to pause, or **`F3`** to stop.
