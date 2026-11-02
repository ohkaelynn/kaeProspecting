# -*- coding: utf-8 -*-
"""
Roblox Prospecting! Automation Macro
"""

import ctypes
from ctypes import wintypes
import datetime
import json
import os
import queue
import re
import subprocess
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox
import urllib.request
import urllib.error
import cv2
import mss
import numpy as np

try:
    import pytesseract
    HAS_TESSERACT = True
except Exception:
    pytesseract = None
    HAS_TESSERACT = False

# ─────────────────────────────────────────────────────────────────────────────
# PREVENT TESSERACT SUBPROCESS FLASH
# ─────────────────────────────────────────────────────────────────────────────
if os.name == "nt":
    _orig_popen = subprocess.Popen

    class _NoWindowPopen(_orig_popen):
        def __init__(self, *args, **kwargs):
            kwargs.setdefault("creationflags", 0)
            kwargs["creationflags"] |= 0x08000000  # CREATE_NO_WINDOW
            super().__init__(*args, **kwargs)

    subprocess.Popen = _NoWindowPopen

# ─────────────────────────────────────────────────────────────────────────────
# WIN32 HIGH-PRECISION TIMER & DPI AWARENESS
# ─────────────────────────────────────────────────────────────────────────────
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

try:
    ctypes.windll.winmm.timeBeginPeriod(1)
except Exception:
    pass

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION & SETTINGS
# ─────────────────────────────────────────────────────────────────────────────
CONFIG_FILE = "prospecting_settings.json"
LOG_QUEUE = queue.Queue()
ROI_SCHEMA_VERSION = 8

DEFAULT_CONFIG = {
    "roi_version": ROI_SCHEMA_VERSION,
    "walk_to_water_timeout": 1.10,
    "walk_to_dirt_timeout": 1.10,
    "dig_hold_max": 1.150,
    "dig_min_poll_delay": 0.050,
    "next_dig_wait": 1200,
    "max_digs_per_cycle": 14,
    "wash_timeout": 15.0,
    "autosell_enabled": True,
    "autosell_cycles": 15,
    "sell_coords": [[0.4297, 0.5475]],
    "scroll_out_notches": 1,
    "scroll_in_notches": 1,
    "always_on_top": True,
    "webhook_enabled": False,
    "webhook_url": "",
    "webhook_interval_min": 5,
    # Client-Area Normalized ROIs [x1, y1, x2, y2]
    "roi_dig_target": [0.588, 0.436, 0.608, 0.460],
    "roi_pan_full":   [0.592, 0.756, 0.623, 0.788],
    "roi_pan_empty":  [0.377, 0.756, 0.412, 0.788],
    "roi_prompt":     [0.435, 0.850, 0.565, 0.884],
    "roi_alert":      [0.380, 0.658, 0.620, 0.692],
    "roi_money":      [0.885, 0.935, 0.995, 0.980],
}


def format_money(amount: float, signed: bool = False) -> str:
    if amount is None:
        return "--"
    sign = "+" if (signed and amount > 0) else ("-" if amount < 0 else ("+" if signed else ""))
    v = abs(float(amount))
    if v >= 1e12:
        return f"{sign}${v / 1e12:.2f}T"
    if v >= 1e9:
        return f"{sign}${v / 1e9:.2f}B"
    if v >= 1e6:
        return f"{sign}${v / 1e6:.2f}M"
    if v >= 1e3:
        return f"{sign}${v / 1e3:.2f}k"
    return f"{sign}${v:,.0f}"


def parse_money_text(txt: str):
    if not txt:
        return None
    cleaned = txt.replace(",", "").replace(" ", "").strip()
    m = re.search(r"\$(\d+(?:\.\d+)?)([kKmMbBtT])?", cleaned)
    if not m:
        return None
    num_str = m.group(1)
    suffix = (m.group(2) or "").upper()
    if "." in num_str and not suffix:
        return None
    try:
        val = float(num_str)
    except ValueError:
        return None
    mult = {"": 1.0, "K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}.get(suffix, 1.0)
    return val * mult


class Stats:
    def __init__(self):
        self.cycles = 0
        self.total_digs = 0
        self.perfect_digs = 0
        self.recoveries = 0
        self.last_water_walk = 0.0
        self.last_dirt_walk = 0.0
        self.current_task = "Idle"
        self.macro_start_time = None
        self.accumulated_runtime = 0.0
        self.start_money = None
        self.current_money = None
        self.total_money_gained = 0.0
        self._money_lock = threading.Lock()

    def get_macro_runtime_seconds(self) -> float:
        total = self.accumulated_runtime
        if self.macro_start_time is not None:
            total += (time.perf_counter() - self.macro_start_time)
        return max(0.0, total)

    def get_macro_runtime_str(self) -> str:
        total_int = int(self.get_macro_runtime_seconds())
        h = total_int // 3600
        m = (total_int % 3600) // 60
        s = total_int % 60
        return f"{h}h {m:02d}m {s:02d}s"

    def get_perfect_rate_str(self) -> str:
        if self.total_digs == 0:
            return "0 / 0 (0.0%)"
        pct = (self.perfect_digs / self.total_digs) * 100.0
        return f"{self.perfect_digs} / {self.total_digs} ({pct:.1f}%)"

    def update_money(self, new_val: float):
        if new_val is None or new_val <= 0:
            return
        with self._money_lock:
            if self.current_money is None:
                self.start_money = new_val
                self.current_money = new_val
                return
            ratio = new_val / max(1.0, self.current_money)
            if ratio < 0.10 or ratio > 25.0:
                return
            delta = new_val - self.current_money
            if delta > 0:
                self.total_money_gained += delta
            self.current_money = new_val

    def reset_money(self):
        with self._money_lock:
            self.start_money = self.current_money
            self.total_money_gained = 0.0

    def get_current_money_str(self) -> str:
        with self._money_lock:
            return format_money(self.current_money, signed=False)

    def get_money_gained_str(self) -> str:
        with self._money_lock:
            return format_money(self.total_money_gained, signed=True)

    def get_money_rate_str(self) -> str:
        elapsed_hr = self.get_macro_runtime_seconds() / 3600.0
        with self._money_lock:
            gained = self.total_money_gained
        if elapsed_hr < (10.0 / 3600.0) or gained <= 0:
            return "$0/hr"
        return f"{format_money(gained / elapsed_hr, signed=False)}/hr"


STATS = Stats()


def load_config() -> dict:
    cfg = DEFAULT_CONFIG.copy()
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
            cfg.update(saved)
        except Exception as e:
            print(f"[Config Warning] Failed to parse {CONFIG_FILE}: {e}. Using defaults.")
    return cfg


def save_config(cfg: dict):
    global CONFIG
    CONFIG.update(cfg)
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(CONFIG, f, indent=4)
    except Exception as e:
        print(f"[Config Error] Could not save {CONFIG_FILE}: {e}")


CONFIG = load_config()


def log_event(msg: str):
    ts = time.strftime("%H:%M:%S")
    formatted = f"[{ts}] {msg}"
    print(formatted)
    LOG_QUEUE.put(formatted)


def set_task(task_name: str):
    STATS.current_task = task_name


# ─────────────────────────────────────────────────────────────────────────────
# CLEAN DISCORD WEBHOOK REPORTER (NO CLUTTERED FOOTER LINE)
# ─────────────────────────────────────────────────────────────────────────────
class DiscordWebhookReporter:
    def __init__(self):
        self._last_sent_time = 0.0
        self._sending_lock = threading.Lock()

    def mark_session_start(self):
        self._last_sent_time = time.perf_counter()

    def check_periodic_send(self):
        if not CONFIG.get("webhook_enabled", False):
            return
        url = str(CONFIG.get("webhook_url", "")).strip()
        if not url:
            return
        interval_s = max(60.0, float(CONFIG.get("webhook_interval_min", 5)) * 60.0)
        now = time.perf_counter()
        if (now - self._last_sent_time) >= interval_s:
            self._last_sent_time = now
            self.send_summary_async(url, is_test=False)

    def send_summary_async(self, url: str = None, is_test: bool = False):
        target_url = (url or str(CONFIG.get("webhook_url", ""))).strip()
        if not target_url:
            if is_test:
                log_event("[Webhook] Enter a valid Discord Webhook URL first.")
            return

        def _worker():
            if not self._sending_lock.acquire(blocking=False):
                return
            try:
                title = "💎 Prospecting! Session Summary"
                if is_test:
                    title += " (Test)"

                embed = {
                    "title": title,
                    "color": 0x38BDF8,  # Modern Cyan
                    "fields": [
                        {
                            "name": "⏱️ Runtime",
                            "value": f"`{STATS.get_macro_runtime_str()}`",
                            "inline": True,
                        },
                        {
                            "name": "🔄 Cycles",
                            "value": f"`{STATS.cycles}`",
                            "inline": True,
                        },
                        {
                            "name": "🛡️ Auto-Recoveries",
                            "value": f"`{STATS.recoveries}`",
                            "inline": True,
                        },
                        {
                            "name": "🎯 Perfect Dig Rate",
                            "value": f"`{STATS.get_perfect_rate_str()}`",
                            "inline": False,
                        },
                        {
                            "name": "💰 Cash Gained",
                            "value": f"`{STATS.get_money_gained_str()}` (`{STATS.get_money_rate_str()}`)",
                            "inline": True,
                        },
                        {
                            "name": "🏦 Balance",
                            "value": f"`{STATS.get_current_money_str()}`",
                            "inline": True,
                        },
                    ],
                    "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                }

                payload = json.dumps({
                    "username": "Prospecting Vision",
                    "embeds": [embed],
                }).encode("utf-8")

                req = urllib.request.Request(
                    target_url,
                    data=payload,
                    headers={
                        "Content-Type": "application/json",
                        "User-Agent": "ProspectingMacro/3.0",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=8) as resp:
                    code = resp.getcode()
                if 200 <= code < 300:
                    log_event("[Webhook] Clean summary sent to Discord.")
                else:
                    log_event(f"[Webhook Warning] Discord returned HTTP {code}.")
            except Exception as e:
                log_event(f"[Webhook Error] Delivery failure: {e}")
            finally:
                self._sending_lock.release()

        threading.Thread(target=_worker, daemon=True).start()


WEBHOOK = DiscordWebhookReporter()


# ─────────────────────────────────────────────────────────────────────────────
# WIN32 64-BIT INPUT SIMULATION
# ─────────────────────────────────────────────────────────────────────────────
user32 = ctypes.windll.user32
ULONG_PTR = ctypes.wintypes.WPARAM


class KeyBdInput(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HardwareInput(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class MouseInput(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class Input_I(ctypes.Union):
    _fields_ = [
        ("ki", KeyBdInput),
        ("mi", MouseInput),
        ("hi", HardwareInput),
    ]


class Input(ctypes.Structure):
    _fields_ = [
        ("type", wintypes.DWORD),
        ("ii", Input_I),
    ]


SC_W = 0x11
SC_S = 0x1F
SC_TILDE = 0x29

VK_F1 = 0x70
VK_F2 = 0x71
VK_F3 = 0x72
VK_F4 = 0x73
VK_F5 = 0x74
VK_F6 = 0x75

KEYEVENTF_SCANCODE = 0x0008
KEYEVENTF_KEYUP = 0x0002
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_WHEEL = 0x0800
WHEEL_DELTA = 120

HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010


def enforce_window_topmost(hwnd: int, enabled: bool = True):
    if not hwnd:
        return
    try:
        z_order = HWND_TOPMOST if enabled else HWND_NOTOPMOST
        user32.SetWindowPos(
            wintypes.HWND(hwnd),
            wintypes.HWND(z_order),
            0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
        )
    except Exception:
        pass


def precise_sleep(duration: float):
    if duration <= 0:
        return
    target = time.perf_counter() + duration
    if duration > 0.004:
        time.sleep(duration - 0.003)
    while time.perf_counter() < target:
        pass


def key_down(scancode: int):
    ii_ = Input_I()
    ii_.ki = KeyBdInput(0, scancode, KEYEVENTF_SCANCODE, 0, 0)
    x = Input(1, ii_)
    user32.SendInput(1, ctypes.byref(x), ctypes.sizeof(x))


def key_up(scancode: int):
    ii_ = Input_I()
    ii_.ki = KeyBdInput(0, scancode, KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP, 0, 0)
    x = Input(1, ii_)
    user32.SendInput(1, ctypes.byref(x), ctypes.sizeof(x))


def press_key(scancode: int, hold: float = 0.05):
    key_down(scancode)
    precise_sleep(hold)
    key_up(scancode)


def mouse_down():
    ii_ = Input_I()
    ii_.mi = MouseInput(0, 0, 0, MOUSEEVENTF_LEFTDOWN, 0, 0)
    x = Input(0, ii_)
    user32.SendInput(1, ctypes.byref(x), ctypes.sizeof(x))


def mouse_up():
    ii_ = Input_I()
    ii_.mi = MouseInput(0, 0, 0, MOUSEEVENTF_LEFTUP, 0, 0)
    x = Input(0, ii_)
    user32.SendInput(1, ctypes.byref(x), ctypes.sizeof(x))


def click(hold: float = 0.04):
    mouse_down()
    precise_sleep(hold)
    mouse_up()


def mouse_scroll(notches: int):
    if notches == 0:
        return
    step = 1 if notches > 0 else -1
    for _ in range(abs(int(notches))):
        amount = int(step * WHEEL_DELTA) & 0xFFFFFFFF
        ii_ = Input_I()
        ii_.mi = MouseInput(0, 0, amount, MOUSEEVENTF_WHEEL, 0, 0)
        x = Input(0, ii_)
        user32.SendInput(1, ctypes.byref(x), ctypes.sizeof(x))
        precise_sleep(0.04)


def move_mouse_abs(x: int, y: int):
    user32.SetCursorPos(int(x), int(y))
    precise_sleep(0.015)
    ii_ = Input_I()
    ii_.mi = MouseInput(1, 1, 0, MOUSEEVENTF_MOVE, 0, 0)
    inp = Input(0, ii_)
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))


# ─────────────────────────────────────────────────────────────────────────────
# VISION & OCR ENGINE
# ─────────────────────────────────────────────────────────────────────────────
class RobloxClientVision:
    def __init__(self):
        self.hwnd = None
        self.gui_hwnd = None
        self._local = threading.local()
        self.client_rect = (0, 0, 2560, 1440)
        self._last_money_poll = 0.0
        self._money_busy = False

    @property
    def sct(self):
        if not hasattr(self._local, "sct"):
            self._local.sct = mss.mss()
        return self._local.sct

    def find_roblox(self) -> bool:
        hwnd = user32.FindWindowW(None, "Roblox")
        if hwnd and user32.IsWindowVisible(hwnd):
            self.hwnd = hwnd
            return True
        return False

    def focus_without_resizing(self) -> bool:
        if not self.find_roblox():
            log_event("[Error] Roblox window not found! Please open Roblox.")
            return False

        if user32.IsIconic(self.hwnd):
            user32.ShowWindow(self.hwnd, 9)
            time.sleep(0.2)

        user32.SetForegroundWindow(self.hwnd)
        time.sleep(0.12)

        if self.gui_hwnd and CONFIG.get("always_on_top", True):
            enforce_window_topmost(self.gui_hwnd, True)

        cx, cy, cw, ch = self.update_client_rect()
        log_event(f"[System] Focused Roblox ({cw}x{ch} at {cx},{cy})")
        move_mouse_abs(cx + int(cw * 0.565), cy + int(ch * 0.614))
        return True

    def update_client_rect(self):
        if not self.hwnd or not user32.IsWindow(self.hwnd):
            self.find_roblox()
        if not self.hwnd:
            return self.client_rect
        rect = wintypes.RECT()
        user32.GetClientRect(self.hwnd, ctypes.byref(rect))
        pt = wintypes.POINT(0, 0)
        user32.ClientToScreen(self.hwnd, ctypes.byref(pt))
        w = max(100, rect.right - rect.left)
        h = max(100, rect.bottom - rect.top)
        self.client_rect = (pt.x, pt.y, w, h)
        return self.client_rect

    def grab_roi(self, roi_ratios) -> np.ndarray:
        cx, cy, cw, ch = self.client_rect
        rx1, ry1, rx2, ry2 = roi_ratios
        x1 = int(cx + cw * rx1)
        y1 = int(cy + ch * ry1)
        x2 = int(cx + cw * rx2)
        y2 = int(cy + ch * ry2)

        region = {
            "left": max(0, x1),
            "top": max(0, y1),
            "width": max(8, x2 - x1),
            "height": max(8, y2 - y1),
        }
        raw = self.sct.grab(region)
        return np.frombuffer(raw.raw, dtype=np.uint8).reshape(raw.height, raw.width, 4)

    def check_dig_slider_hit(self) -> bool:
        crop = self.grab_roi(CONFIG.get("roi_dig_target", DEFAULT_CONFIG["roi_dig_target"]))
        if crop.size == 0:
            return False

        b = crop[:, :, 0]
        g = crop[:, :, 1]
        r = crop[:, :, 2]

        white_mask = (
            (r >= 210) & (g >= 210) & (b >= 200) &
            (np.abs(r.astype(np.int16) - b.astype(np.int16)) <= 35)
        )
        row_counts = np.sum(white_mask, axis=1)
        min_bar_width = max(4, int(crop.shape[1] * 0.06))
        return bool(np.any(row_counts >= min_bar_width))

    def is_pan_full(self) -> bool:
        crop = self.grab_roi(CONFIG.get("roi_pan_full", DEFAULT_CONFIG["roi_pan_full"]))
        if crop.size == 0:
            return False
        bgr = cv2.cvtColor(crop, cv2.COLOR_BGRA2BGR)
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
        yellow_mask = cv2.inRange(hsv, (15, 110, 140), (45, 255, 255))
        return bool(np.sum(yellow_mask > 0) > (crop.shape[0] * crop.shape[1] * 0.10))

    def is_pan_empty(self) -> bool:
        crop = self.grab_roi(CONFIG.get("roi_pan_empty", DEFAULT_CONFIG["roi_pan_empty"]))
        if crop.size == 0:
            return False
        bgr = cv2.cvtColor(crop, cv2.COLOR_BGRA2BGR)
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
        yellow_mask = cv2.inRange(hsv, (15, 110, 140), (45, 255, 255))
        return bool(np.sum(yellow_mask > 0) < 8)

    def get_prompt_state(self) -> str:
        """
        Vision classification for interaction prompt:
        - 'DIRT'  : 'Collect Deposit'
        - 'WATER' : 'Shake' / 'Pan'
        - 'NONE'  : No prompt
        """
        crop = self.grab_roi(CONFIG.get("roi_prompt", DEFAULT_CONFIG["roi_prompt"]))
        if crop.size == 0:
            return "NONE"
        w = max(1, crop.shape[1])
        b = crop[:, :, 0].astype(np.int16)
        g = crop[:, :, 1].astype(np.int16)
        r = crop[:, :, 2].astype(np.int16)
        gray = (r + g + b) // 3
        white = (r >= 215) & (g >= 215) & (b >= 195) & (np.abs(r - g) <= 25)
        col_contrast = (gray.max(axis=0) - gray.min(axis=0)) >= 80
        col_has_text = (np.sum(white, axis=0) >= 2) & col_contrast
        active_cols_ratio = np.sum(col_has_text) / w
        outer_cols = int(np.sum(col_has_text[:int(w * 0.24)]) + np.sum(col_has_text[int(w * 0.74):]))
        xs = np.where(col_has_text)[0]
        span = (xs[-1] - xs[0]) / w if len(xs) >= 6 else 0.0

        if active_cols_ratio > 0.35 or outer_cols >= 20 or span > 0.60:
            return "DIRT"
        elif active_cols_ratio >= 0.10 and span >= 0.20:
            return "WATER"
        return "NONE"

    def get_alert_banner_state(self) -> str:
        """Checks for red in-game alert banners above the pan."""
        crop = self.grab_roi(CONFIG.get("roi_alert", DEFAULT_CONFIG["roi_alert"]))
        if crop.size == 0:
            return "NONE"

        b = crop[:, :, 0].astype(np.int16)
        g = crop[:, :, 1].astype(np.int16)
        r = crop[:, :, 2].astype(np.int16)

        dark_outline = (r < 55) & (g < 55) & (b < 55)
        if int(np.sum(dark_outline)) < 120:
            return "NONE"

        near_dark = cv2.dilate(dark_outline.astype(np.uint8), np.ones((7, 7), np.uint8)) > 0
        red_text = near_dark & (r > 150) & (g < 75) & (b < 75)
        if int(np.sum(red_text)) < 180:
            return "NONE"

        if HAS_TESSERACT:
            try:
                mask_img = 255 - cv2.dilate(red_text.astype(np.uint8) * 255, np.ones((2, 2), np.uint8))
                ocr_txt = pytesseract.image_to_string(mask_img, config="--psm 7").strip().lower()
                if "sand" in ocr_txt or "enough" in ocr_txt:
                    return "NOT_ENOUGH_SAND"
                if "full" in ocr_txt or "wash" in ocr_txt or "water" in ocr_txt:
                    return "PAN_FULL" if not self.is_pan_empty() else "NONE"
                return "NONE"
            except Exception:
                return "NONE"

        xs = np.where(red_text)[1]
        if len(xs) > 0:
            span_ratio = (xs.max() - xs.min()) / max(1, crop.shape[1])
            if span_ratio > 0.60:
                return "PAN_FULL" if not self.is_pan_empty() else "NONE"
            return "NOT_ENOUGH_SAND"
        return "NONE"

    def poll_money_async(self, force: bool = False):
        if not HAS_TESSERACT or self._money_busy:
            return
        now = time.perf_counter()
        if not force and (now - self._last_money_poll) < 3.5:
            return
        if not self.find_roblox():
            return

        self._money_busy = True
        self._last_money_poll = now

        def _worker():
            try:
                self.update_client_rect()
                crop = self.grab_roi(CONFIG.get("roi_money", DEFAULT_CONFIG["roi_money"]))
                if crop.size == 0:
                    return
                b = crop[:, :, 0].astype(np.int16)
                g = crop[:, :, 1].astype(np.int16)
                r = crop[:, :, 2].astype(np.int16)
                ymask = ((r > 180) & (g > 150) & (b < 115) & ((r - b) > 90)).astype(np.uint8) * 255
                if int(np.sum(ymask > 0)) < 50:
                    return
                up = cv2.resize(ymask, (0, 0), fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
                inv = 255 - (up > 127).astype(np.uint8) * 255
                inv = cv2.copyMakeBorder(inv, 10, 10, 10, 10, cv2.BORDER_CONSTANT, value=255)
                wl = "$0123456789.,kKmMbBtT"
                txt = pytesseract.image_to_string(
                    inv, config=f"--psm 7 -c tessedit_char_whitelist={wl}"
                ).strip()
                val = parse_money_text(txt)
                if val is not None:
                    STATS.update_money(val)
            except Exception:
                pass
            finally:
                self._money_busy = False

        threading.Thread(target=_worker, daemon=True).start()

    def save_debug_snapshot(self):
        if not self.find_roblox():
            log_event("[Debug] Roblox window not found for snapshot.")
            return
        cx, cy, cw, ch = self.update_client_rect()
        raw = self.sct.grab({"left": max(0, cx), "top": max(0, cy), "width": cw, "height": ch})
        img = np.frombuffer(raw.raw, dtype=np.uint8).reshape(raw.height, raw.width, 4)
        bgr = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)

        rois = [
            ("DIG_TARGET", CONFIG.get("roi_dig_target", DEFAULT_CONFIG["roi_dig_target"]), (0, 255, 0)),
            ("PAN_FULL",   CONFIG.get("roi_pan_full", DEFAULT_CONFIG["roi_pan_full"]),     (0, 255, 255)),
            ("PAN_EMPTY",  CONFIG.get("roi_pan_empty", DEFAULT_CONFIG["roi_pan_empty"]),   (255, 255, 0)),
            ("PROMPT",     CONFIG.get("roi_prompt", DEFAULT_CONFIG["roi_prompt"]),         (255, 0, 255)),
            ("ALERT",      CONFIG.get("roi_alert", DEFAULT_CONFIG["roi_alert"]),           (80, 80, 255)),
            ("MONEY_OCR",  CONFIG.get("roi_money", DEFAULT_CONFIG["roi_money"]),           (0, 215, 255)),
        ]
        for name, (rx1, ry1, rx2, ry2), color in rois:
            p1 = (int(cw * rx1), int(ch * ry1))
            p2 = (int(cw * rx2), int(ch * ry2))
            cv2.rectangle(bgr, p1, p2, color, 2)
            cv2.putText(bgr, name, (p1[0], max(16, p1[1] - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.52, color, 2)

        out_path = os.path.abspath("debug_vision.png")
        cv2.imwrite(out_path, bgr)
        log_event(f"[Debug F4] Saved vision overlay to: {out_path}")


# ─────────────────────────────────────────────────────────────────────────────
# VISION-ONLY MACRO CONTROLLER (WITH RECOVERIES)
# ─────────────────────────────────────────────────────────────────────────────
class ProspectingMacro:
    def __init__(self):
        self.running = False
        self.paused = False
        self.vision = RobloxClientVision()
        self._worker_thread = None

    def release_all_inputs(self):
        mouse_up()
        key_up(SC_W)
        key_up(SC_S)

    def start(self):
        if self.running:
            if self.paused:
                self.toggle_pause()
            return

        if not self.vision.focus_without_resizing():
            return

        self.running = True
        self.paused = False
        STATS.macro_start_time = time.perf_counter()
        WEBHOOK.mark_session_start()
        self.release_all_inputs()
        self.vision.poll_money_async(force=True)

        self._worker_thread = threading.Thread(target=self.run_loop, daemon=True)
        self._worker_thread.start()

    def toggle_pause(self):
        if not self.running:
            self.start()
            return
        self.paused = not self.paused
        self.release_all_inputs()
        if self.paused:
            if STATS.macro_start_time is not None:
                STATS.accumulated_runtime += (time.perf_counter() - STATS.macro_start_time)
                STATS.macro_start_time = None
            set_task("Paused")
            log_event("[Hotkey F2] Macro PAUSED.")
        else:
            STATS.macro_start_time = time.perf_counter()
            log_event("[Hotkey F2] Macro RESUMED.")
            self.vision.focus_without_resizing()

    def stop(self):
        if not self.running:
            return
        self.running = False
        self.paused = False
        if STATS.macro_start_time is not None:
            STATS.accumulated_runtime += (time.perf_counter() - STATS.macro_start_time)
            STATS.macro_start_time = None
        self.release_all_inputs()
        set_task("Idle")
        log_event("[System] Macro STOPPED.")

    def sleep_interruptible(self, duration: float) -> bool:
        t0 = time.perf_counter()
        while (time.perf_counter() - t0) < duration:
            if not self.running or self.paused:
                return False
            time.sleep(0.010)
        return True

    def walk_to_water_vision(self) -> float:
        """Walks forward until prompt switches to WATER (Shake/Pan)."""
        max_timeout = float(CONFIG.get("walk_to_water_timeout", 1.10))
        set_task("Walking to Water (Vision)")
        log_event("[Vision] Walking W to water...")

        t0 = time.perf_counter()
        key_down(SC_W)
        try:
            while self.running and not self.paused and (time.perf_counter() - t0 < max_timeout):
                if (time.perf_counter() - t0 > 0.15) and self.vision.get_prompt_state() == "WATER":
                    precise_sleep(0.090)  # Shoreline stabilization buffer
                    break
                precise_sleep(0.005)
        finally:
            key_up(SC_W)

        elapsed = time.perf_counter() - t0
        STATS.last_water_walk = round(elapsed, 3)
        log_event(f"[Vision] Reached water ({STATS.last_water_walk:.3f}s).")
        return elapsed

    def walk_to_dirt_vision(self) -> float:
        """Walks backward until prompt switches to DIRT (Collect Deposit)."""
        max_timeout = float(CONFIG.get("walk_to_dirt_timeout", 1.10))
        set_task("Walking to Dirt (Vision)")
        log_event("[Vision] Walking S to dirt...")

        t0 = time.perf_counter()
        key_down(SC_S)
        try:
            while self.running and not self.paused and (time.perf_counter() - t0 < max_timeout):
                if (time.perf_counter() - t0 > 0.15) and self.vision.get_prompt_state() == "DIRT":
                    precise_sleep(0.090)  # Safe deposit stabilization buffer
                    break
                precise_sleep(0.005)
        finally:
            key_up(SC_S)

        elapsed = time.perf_counter() - t0
        STATS.last_dirt_walk = round(elapsed, 3)
        log_event(f"[Vision] Reached dirt ({STATS.last_dirt_walk:.3f}s).")
        return elapsed

    def test_vision_walk(self):
        if self.running:
            log_event("[Warning] Stop the macro before testing walk.")
            return
        if not self.vision.focus_without_resizing():
            return

        def _run():
            self.running = True
            self.paused = False
            try:
                log_event("[Test] Testing vision forward to water -> backward to dirt...")
                self.walk_to_water_vision()
                time.sleep(0.5)
                self.walk_to_dirt_vision()
                log_event(f"[Test] Test complete: W={STATS.last_water_walk:.3f}s | S={STATS.last_dirt_walk:.3f}s")
            finally:
                self.running = False
                self.release_all_inputs()
                set_task("Idle")

        threading.Thread(target=_run, daemon=True).start()

    def _wash_and_return_sequence(self, wash_timeout: float, cycles_since_sell: int) -> int:
        if self.vision.is_pan_empty():
            log_event("[Vision] Pan is empty (0 sand) — skipping water trip.")
            return cycles_since_sell

        self.walk_to_water_vision()
        if not self.sleep_interruptible(0.20):
            return cycles_since_sell

        set_task("Washing Pan")
        log_event("[Action] Washing pan in water...")
        t_wash = time.perf_counter()
        empty_confirm_frames = 0
        water_nudges = 0

        while self.running and not self.paused and (time.perf_counter() - t_wash < wash_timeout):
            click(hold=0.025)
            precise_sleep(0.035)
            elapsed_wash = time.perf_counter() - t_wash
            if elapsed_wash > 0.65:
                if self.vision.is_pan_empty():
                    empty_confirm_frames += 1
                    if empty_confirm_frames >= 3:
                        log_event("[Vision] Pan confirmed empty! Waiting 0.85s...")
                        self.sleep_interruptible(0.85)
                        break
                else:
                    empty_confirm_frames = 0
                    alert_now = self.vision.get_alert_banner_state()
                    if elapsed_wash > 1.0 and alert_now == "NOT_ENOUGH_SAND":
                        log_event('[Recovery] "Not enough sand!" while washing -> Pan empty.')
                        self.sleep_interruptible(0.55)
                        break
                    elif alert_now == "PAN_FULL" and water_nudges < 2:
                        water_nudges += 1
                        STATS.recoveries += 1
                        log_event('[Recovery] "Your pan is full!" while washing -> Stepping W deeper.')
                        self.walk_to_water_vision()
                        self.sleep_interruptible(0.12)

        if not self.running or self.paused:
            return cycles_since_sell

        self.vision.poll_money_async(force=True)
        self.walk_to_dirt_vision()
        self.sleep_interruptible(0.22)

        # Recovery check: still seeing 'Not enough sand!' at dirt -> step back
        if self.vision.get_alert_banner_state() == "NOT_ENOUGH_SAND":
            STATS.recoveries += 1
            log_event('[Recovery] Still seeing "Not enough sand!" -> Stepping S.')
            self.walk_to_dirt_vision()
            self.sleep_interruptible(0.20)

        STATS.cycles += 1
        cycles_since_sell += 1
        log_event(f"[System] Cycle #{STATS.cycles} complete.")

        if CONFIG.get("autosell_enabled") and cycles_since_sell >= max(1, int(CONFIG.get("autosell_cycles", 15))):
            self.auto_sell(standalone_test=False)
            cycles_since_sell = 0
            self.vision.poll_money_async(force=True)

        WEBHOOK.check_periodic_send()
        return cycles_since_sell

    def run_loop(self):
        log_event("[Macro Active] Positioned on dirt facing water (F2=Pause, F3=Stop)")
        self.sleep_interruptible(0.35)

        dig_count_this_cycle = 0
        cycles_since_sell = 0

        try:
            while self.running:
                if self.paused:
                    time.sleep(0.1)
                    continue

                self.vision.update_client_rect()
                self.vision.poll_money_async(force=False)
                WEBHOOK.check_periodic_send()

                max_digs = int(CONFIG.get("max_digs_per_cycle", 14))
                dig_hold_max = float(CONFIG.get("dig_hold_max", 1.150))
                min_poll_delay = float(CONFIG.get("dig_min_poll_delay", 0.050))
                wash_timeout = float(CONFIG.get("wash_timeout", 15.0))

                # ── Recovery 1: Not enough sand banner ──────────────────────
                alert_state = self.vision.get_alert_banner_state()
                if alert_state == "NOT_ENOUGH_SAND":
                    STATS.recoveries += 1
                    log_event('[Recovery] "Not enough sand!" -> Walking S to dirt.')
                    self.walk_to_dirt_vision()
                    self.sleep_interruptible(0.22)
                    continue

                # ── Check Pan Full ──────────────────────────────────────────
                pan_empty_now = self.vision.is_pan_empty()
                pan_full_now = (not pan_empty_now) and (
                    alert_state == "PAN_FULL"
                    or self.vision.is_pan_full()
                    or dig_count_this_cycle >= max_digs
                )
                if pan_full_now:
                    log_event(f"[Vision] Pan is full ({dig_count_this_cycle} digs). Washing...")
                    dig_count_this_cycle = 0
                    cycles_since_sell = self._wash_and_return_sequence(wash_timeout, cycles_since_sell)
                    continue

                # ── Spade Hold-and-Release ──────────────────────────────────
                set_task(f"Digging (#{dig_count_this_cycle + 1})")
                mouse_down()
                t_dig = time.perf_counter()
                hit_perfect = False

                try:
                    while self.running and not self.paused and (time.perf_counter() - t_dig < dig_hold_max):
                        if (time.perf_counter() - t_dig) >= min_poll_delay:
                            if self.vision.check_dig_slider_hit():
                                hit_perfect = True
                                break
                        precise_sleep(0.001)
                finally:
                    mouse_up()

                dig_time = round(time.perf_counter() - t_dig, 3)

                if not self.sleep_interruptible(0.18):
                    continue

                post_dig_alert = self.vision.get_alert_banner_state()
                if post_dig_alert == "NOT_ENOUGH_SAND":
                    STATS.recoveries += 1
                    log_event('[Recovery] "Not enough sand!" on scoop -> Walking S.')
                    self.walk_to_dirt_vision()
                    self.sleep_interruptible(0.22)
                    continue
                elif post_dig_alert == "PAN_FULL" and not self.vision.is_pan_empty():
                    dig_count_this_cycle = 0
                    cycles_since_sell = self._wash_and_return_sequence(wash_timeout, cycles_since_sell)
                    continue

                dig_count_this_cycle += 1
                STATS.total_digs += 1

                if hit_perfect:
                    STATS.perfect_digs += 1
                    log_event(f"[Dig #{dig_count_this_cycle}] Perfect release ({dig_time:.3f}s)")
                else:
                    log_event(f"[Dig #{dig_count_this_cycle}] Max-hold release ({dig_time:.3f}s)")

                set_task("Dig Cooldown")
                wait_s = max(0.05, (float(CONFIG.get("next_dig_wait", 1200)) / 1000.0) - 0.18)
                self.sleep_interruptible(wait_s)

        except Exception as e:
            log_event(f"[Error] Macro loop failure: {e}")
        finally:
            self.release_all_inputs()
            set_task("Idle")

    def auto_sell(self, standalone_test: bool = False):
        if standalone_test and not self.vision.focus_without_resizing():
            return

        set_task("Auto-Selling")
        log_event("[Auto-Sell] Executing Tilde auto-sell...")
        self.release_all_inputs()
        time.sleep(0.15)

        cx, cy, cw, ch = self.vision.update_client_rect()
        move_mouse_abs(cx + int(cw * 0.50), cy + int(ch * 0.50))
        time.sleep(0.08)

        out_notches = int(CONFIG.get("scroll_out_notches", 1))
        mouse_scroll(-out_notches)
        time.sleep(0.20)

        press_key(SC_TILDE, hold=0.06)
        time.sleep(0.40)

        sell_coords = CONFIG.get("sell_coords", [[0.4297, 0.5475]])
        for idx, pt in enumerate(sell_coords, start=1):
            if not standalone_test and (not self.running or self.paused):
                break
            rx, ry = float(pt[0]), float(pt[1])
            target_x = cx + int(cw * rx)
            target_y = cy + int(ch * ry)
            log_event(f"[Auto-Sell] Clicking Sell #{idx} ({rx:.4f}, {ry:.4f})")
            move_mouse_abs(target_x, target_y)
            time.sleep(0.10)
            click(hold=0.04)
            time.sleep(0.12)
            click(hold=0.04)
            time.sleep(0.20)

        time.sleep(0.30)
        press_key(SC_TILDE, hold=0.06)
        time.sleep(0.25)

        in_notches = int(CONFIG.get("scroll_in_notches", out_notches))
        mouse_scroll(in_notches)
        time.sleep(0.20)

        move_mouse_abs(cx + int(cw * 0.565), cy + int(ch * 0.614))
        self.vision.poll_money_async(force=True)
        log_event("[Auto-Sell] Sequence complete.")


# ─────────────────────────────────────────────────────────────────────────────
# GUI HUD
# ─────────────────────────────────────────────────────────────────────────────
class ProspectingGUI:
    BG_DARK = "#12141c"
    BG_PANEL = "#1a1d28"
    BG_CARD = "#212534"
    BG_INPUT = "#151822"
    FG_MAIN = "#f3f4f6"
    FG_MUTED = "#9ca3af"
    ACCENT_CYAN = "#38bdf8"
    ACCENT_GOLD = "#fbbf24"
    ACCENT_GREEN = "#4ade80"
    BORDER_CLR = "#2e3446"

    def __init__(self, root: tk.Tk, macro: ProspectingMacro):
        self.root = root
        self.macro = macro
        self.gui_start_time = time.perf_counter()

        self.root.title("Prospecting! — Vision Macro")
        self.root.geometry("830x580+15+15")
        self.root.minsize(770, 520)
        self.root.configure(bg=self.BG_DARK)

        self._configure_styles()
        self._build_header()
        self._build_tabs()
        self._build_bottom_bar()

        self.root.update_idletasks()
        self.gui_hwnd = user32.GetParent(self.root.winfo_id()) or self.root.winfo_id()
        self.macro.vision.gui_hwnd = self.gui_hwnd
        self.toggle_topmost()

        self._start_background_updaters()
        self.root.protocol("WM_DELETE_WINDOW", self.on_exit)

    def _configure_styles(self):
        style = ttk.Style()
        if "clam" in style.theme_names():
            style.theme_use("clam")

        style.configure(".", background=self.BG_PANEL, foreground=self.FG_MAIN, font=("Segoe UI", 9))
        style.configure("TFrame", background=self.BG_PANEL)
        style.configure("Dark.TFrame", background=self.BG_DARK)
        style.configure("Card.TFrame", background=self.BG_CARD)

        style.configure(
            "TNotebook",
            background=self.BG_DARK,
            borderwidth=0,
            tabmargins=(4, 4, 4, 0),
        )
        style.configure(
            "TNotebook.Tab",
            background=self.BG_CARD,
            foreground=self.FG_MUTED,
            padding=(18, 7),
            font=("Segoe UI", 9, "bold"),
            borderwidth=0,
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", self.BG_PANEL), ("active", "#2a2f42")],
            foreground=[("selected", self.ACCENT_CYAN), ("active", self.FG_MAIN)],
        )

        style.configure(
            "TLabelframe",
            background=self.BG_CARD,
            foreground=self.ACCENT_CYAN,
            bordercolor=self.BORDER_CLR,
            borderwidth=1,
            relief="solid",
        )
        style.configure(
            "TLabelframe.Label",
            background=self.BG_CARD,
            foreground=self.ACCENT_CYAN,
            font=("Segoe UI", 9, "bold"),
            padding=(4, 0),
        )

        style.configure("TLabel", background=self.BG_CARD, foreground=self.FG_MAIN, font=("Segoe UI", 9))
        style.configure("Muted.TLabel", background=self.BG_CARD, foreground=self.FG_MUTED, font=("Segoe UI", 8))
        style.configure(
            "TCheckbutton",
            background=self.BG_CARD,
            foreground=self.FG_MAIN,
            font=("Segoe UI", 9),
            indicatorColor=self.BG_INPUT,
        )
        style.configure(
            "Bar.TCheckbutton",
            background=self.BG_DARK,
            foreground=self.FG_MAIN,
            font=("Segoe UI", 9, "bold"),
        )
        style.configure(
            "TEntry",
            fieldbackground=self.BG_INPUT,
            foreground=self.FG_MAIN,
            insertcolor=self.FG_MAIN,
            bordercolor=self.BORDER_CLR,
            padding=4,
        )
        style.configure(
            "TButton",
            background="#2a3042",
            foreground=self.FG_MAIN,
            bordercolor=self.BORDER_CLR,
            font=("Segoe UI", 9, "bold"),
            padding=(10, 5),
        )
        style.map(
            "TButton",
            background=[("active", "#363e57"), ("pressed", "#1f2433")],
            foreground=[("active", self.ACCENT_CYAN)],
        )

        style.configure("Start.TButton", background="#166534", foreground="#ffffff", font=("Segoe UI", 9, "bold"))
        style.map("Start.TButton", background=[("active", "#15803d")])

        style.configure("Pause.TButton", background="#854d0e", foreground="#ffffff", font=("Segoe UI", 9, "bold"))
        style.map("Pause.TButton", background=[("active", "#a16207")])

        style.configure("Stop.TButton", background="#991b1b", foreground="#ffffff", font=("Segoe UI", 9, "bold"))
        style.map("Stop.TButton", background=[("active", "#b91c1c")])

        style.configure("Accent.TButton", background="#0369a1", foreground="#e0f2fe", font=("Segoe UI", 9, "bold"))
        style.map("Accent.TButton", background=[("active", "#0284c7")])

    def _build_header(self):
        hdr = tk.Frame(self.root, bg=self.BG_DARK, padx=14, pady=6)
        hdr.pack(fill="x")

        tk.Label(
            hdr,
            text="Prospecting! Macro",
            font=("Segoe UI", 12, "bold"),
            bg=self.BG_DARK,
            fg=self.ACCENT_CYAN,
        ).pack(side="left")

        tk.Label(
            hdr,
            text="Vision Prompt Navigation • Auto-Recoveries • Clean Webhook",
            font=("Segoe UI", 8),
            bg=self.BG_DARK,
            fg=self.FG_MUTED,
        ).pack(side="left", padx=(12, 0))

        self.lbl_status_badge = tk.Label(
            hdr,
            text="● IDLE",
            font=("Segoe UI", 9, "bold"),
            bg=self.BG_CARD,
            fg=self.ACCENT_CYAN,
            padx=12,
            pady=2,
        )
        self.lbl_status_badge.pack(side="right")

    def _build_tabs(self):
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=(2, 4))

        self.tab_info = ttk.Frame(self.notebook)
        self.tab_move = ttk.Frame(self.notebook)
        self.tab_dig = ttk.Frame(self.notebook)
        self.tab_sell = ttk.Frame(self.notebook)
        self.tab_webhook = ttk.Frame(self.notebook)

        self.notebook.add(self.tab_info, text=" Dashboard & Telemetry ")
        self.notebook.add(self.tab_move, text=" Vision Movement ")
        self.notebook.add(self.tab_dig, text=" Dig & Panning ")
        self.notebook.add(self.tab_sell, text=" Auto-Sell ")
        self.notebook.add(self.tab_webhook, text=" Webhook ")

        self._init_info_tab()
        self._init_move_tab()
        self._init_dig_tab()
        self._init_sell_tab()
        self._init_webhook_tab()

    def _init_info_tab(self):
        top_frame = ttk.Frame(self.tab_info, padding=(8, 8, 8, 6))
        top_frame.pack(fill="x")
        top_frame.grid_columnconfigure((0, 1, 2), weight=1, uniform="cards")

        col1 = ttk.LabelFrame(top_frame, text=" Runtime & Status ", padding=(10, 6))
        col1.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        self.lbl_gui_rt = ttk.Label(col1, text="GUI: 0h 00m 00s")
        self.lbl_gui_rt.pack(anchor="w", pady=1)
        self.lbl_macro_rt = ttk.Label(col1, text="Active: 0h 00m 00s")
        self.lbl_macro_rt.pack(anchor="w", pady=1)
        self.lbl_task = ttk.Label(col1, text="Task: Idle", font=("Segoe UI", 9, "bold"), foreground=self.ACCENT_CYAN)
        self.lbl_task.pack(anchor="w", pady=(2, 0))

        col2 = ttk.LabelFrame(top_frame, text=" Session Telemetry ", padding=(10, 6))
        col2.grid(row=0, column=1, sticky="nsew", padx=5)
        self.lbl_cycles = ttk.Label(col2, text="Cycles: 0 (Recoveries: 0)")
        self.lbl_cycles.pack(anchor="w", pady=1)
        self.lbl_digs = ttk.Label(col2, text="Perfect: 0 / 0 (0.0%)", font=("Segoe UI", 9, "bold"), foreground=self.ACCENT_GOLD)
        self.lbl_digs.pack(anchor="w", pady=1)
        self.lbl_walks = ttk.Label(col2, text="Walk W / S: 0.00s / 0.00s")
        self.lbl_walks.pack(anchor="w", pady=(2, 0))

        col3 = ttk.LabelFrame(top_frame, text=" Cash Telemetry ", padding=(10, 6))
        col3.grid(row=0, column=2, sticky="nsew", padx=(5, 0))
        self.lbl_money_cur = ttk.Label(col3, text="Balance: --")
        self.lbl_money_cur.pack(anchor="w", pady=1)
        self.lbl_money_gain = ttk.Label(col3, text="Gained: +$0", font=("Segoe UI", 9, "bold"), foreground=self.ACCENT_GREEN)
        self.lbl_money_gain.pack(anchor="w", pady=1)
        self.lbl_money_rate = ttk.Label(col3, text="Rate: $0/hr", foreground=self.ACCENT_CYAN)
        self.lbl_money_rate.pack(anchor="w", pady=(2, 0))

        log_outer = ttk.LabelFrame(self.tab_info, text=" Live Activity Console ", padding=(8, 4, 8, 8))
        log_outer.pack(fill="both", expand=True, padx=8, pady=(0, 6))

        log_toolbar = ttk.Frame(log_outer, style="Card.TFrame")
        log_toolbar.pack(fill="x", pady=(0, 4))
        self.var_autoscroll = tk.BooleanVar(value=True)
        ttk.Checkbutton(log_toolbar, text="Auto-Scroll", variable=self.var_autoscroll).pack(side="left")
        ttk.Button(log_toolbar, text="Clear", command=self.clear_log).pack(side="right", padx=(6, 0))
        ttk.Button(log_toolbar, text="Reset Cash", command=self.reset_cash_stats).pack(side="right")

        txt_container = tk.Frame(log_outer, bg="#0d0f16", bd=1, relief="solid")
        txt_container.pack(fill="both", expand=True)

        self.log_scroll = ttk.Scrollbar(txt_container, orient="vertical")
        self.log_scroll.pack(side="right", fill="y")

        self.txt_log = tk.Text(
            txt_container,
            height=12,
            font=("Consolas", 9),
            state="disabled",
            bg="#0d0f16",
            fg="#e5e7eb",
            padx=8,
            pady=6,
            wrap="word",
            yscrollcommand=self.log_scroll.set,
            relief="flat",
        )
        self.txt_log.pack(side="left", fill="both", expand=True)
        self.log_scroll.config(command=self.txt_log.yview)

    def clear_log(self):
        self.txt_log.config(state="normal")
        self.txt_log.delete("1.0", "end")
        self.txt_log.config(state="disabled")

    def reset_cash_stats(self):
        STATS.reset_money()
        self.macro.vision.poll_money_async(force=True)
        log_event("[Cash] Counter reset.")

    def _init_move_tab(self):
        frame = ttk.Frame(self.tab_move, padding=12)
        frame.pack(fill="both", expand=True)

        grp = ttk.LabelFrame(frame, text="Vision Prompt Navigation Settings", padding=12)
        grp.pack(fill="x")
        grp.grid_columnconfigure(2, weight=1)

        ttk.Label(grp, text="Forward Timeout (sec):").grid(row=0, column=0, sticky="w", pady=6)
        self.var_walk_w = tk.StringVar(value=str(CONFIG.get("walk_to_water_timeout", 1.10)))
        ttk.Entry(grp, textvariable=self.var_walk_w, width=12).grid(row=0, column=1, sticky="w", padx=12, pady=6)
        ttk.Label(grp, text="Holds W until 'Shake/Pan' appears on screen", style="Muted.TLabel").grid(
            row=0, column=2, sticky="w"
        )

        ttk.Label(grp, text="Backward Timeout (sec):").grid(row=1, column=0, sticky="w", pady=6)
        self.var_walk_s = tk.StringVar(value=str(CONFIG.get("walk_to_dirt_timeout", 1.10)))
        ttk.Entry(grp, textvariable=self.var_walk_s, width=12).grid(row=1, column=1, sticky="w", padx=12, pady=6)
        ttk.Label(grp, text="Holds S until 'Collect Deposit' appears on screen", style="Muted.TLabel").grid(
            row=1, column=2, sticky="w"
        )

        btn_test = ttk.Button(
            frame, text="Test Vision Walk Now (F6)", style="Accent.TButton", command=self.on_test_walk
        )
        btn_test.pack(anchor="w", pady=(14, 0))

    def _init_dig_tab(self):
        frame = ttk.Frame(self.tab_dig, padding=12)
        frame.pack(fill="both", expand=True)

        grp = ttk.LabelFrame(frame, text="Spade Minigame & Panning Parameters", padding=12)
        grp.pack(fill="x")
        grp.grid_columnconfigure(2, weight=1)

        ttk.Label(grp, text="Max Dig Hold (sec):").grid(row=0, column=0, sticky="w", pady=5)
        self.var_dig_max = tk.StringVar(value=str(CONFIG.get("dig_hold_max", 1.150)))
        ttk.Entry(grp, textvariable=self.var_dig_max, width=12).grid(row=0, column=1, sticky="w", padx=12, pady=5)
        ttk.Label(grp, text="Safety hold cap waiting for white bar release", style="Muted.TLabel").grid(
            row=0, column=2, sticky="w"
        )

        ttk.Label(grp, text="Min Poll Delay (sec):").grid(row=1, column=0, sticky="w", pady=5)
        self.var_dig_min = tk.StringVar(value=str(CONFIG.get("dig_min_poll_delay", 0.050)))
        ttk.Entry(grp, textvariable=self.var_dig_min, width=12).grid(row=1, column=1, sticky="w", padx=12, pady=5)
        ttk.Label(grp, text="Initial settle delay after holding LMB", style="Muted.TLabel").grid(
            row=1, column=2, sticky="w"
        )

        ttk.Label(grp, text="Next Dig Wait (ms):").grid(row=2, column=0, sticky="w", pady=5)
        self.var_dig_wait = tk.StringVar(value=str(CONFIG.get("next_dig_wait", 1200)))
        ttk.Entry(grp, textvariable=self.var_dig_wait, width=12).grid(row=2, column=1, sticky="w", padx=12, pady=5)
        ttk.Label(grp, text="Cooldown animation wait between digs", style="Muted.TLabel").grid(
            row=2, column=2, sticky="w"
        )

        ttk.Label(grp, text="Max Digs Per Cycle:").grid(row=3, column=0, sticky="w", pady=5)
        self.var_max_digs = tk.StringVar(value=str(CONFIG.get("max_digs_per_cycle", 14)))
        ttk.Entry(grp, textvariable=self.var_max_digs, width=12).grid(row=3, column=1, sticky="w", padx=12, pady=5)
        ttk.Label(grp, text="Fallback scoop count before washing", style="Muted.TLabel").grid(
            row=3, column=2, sticky="w"
        )

        ttk.Label(grp, text="Wash Timeout (sec):").grid(row=4, column=0, sticky="w", pady=5)
        self.var_wash_timeout = tk.StringVar(value=str(CONFIG.get("wash_timeout", 15.0)))
        ttk.Entry(grp, textvariable=self.var_wash_timeout, width=12).grid(row=4, column=1, sticky="w", padx=12, pady=5)
        ttk.Label(grp, text="Max seconds washing pan in water", style="Muted.TLabel").grid(
            row=4, column=2, sticky="w"
        )

        btn_snap = ttk.Button(
            frame,
            text="Save ROI Vision Snapshot (F4)",
            style="Accent.TButton",
            command=self.macro.vision.save_debug_snapshot,
        )
        btn_snap.pack(anchor="w", pady=(14, 0))

    def _init_sell_tab(self):
        frame = ttk.Frame(self.tab_sell, padding=12)
        frame.pack(fill="both", expand=True)

        grp = ttk.LabelFrame(frame, text="Tilde Backpack Auto-Sell", padding=12)
        grp.pack(fill="x")
        grp.grid_columnconfigure(2, weight=1)

        self.var_autosell = tk.BooleanVar(value=bool(CONFIG.get("autosell_enabled", True)))
        ttk.Checkbutton(grp, text="Enable Periodic Auto-Sell", variable=self.var_autosell).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(2, 8)
        )

        ttk.Label(grp, text="Sell Every N Cycles:").grid(row=1, column=0, sticky="w", pady=5)
        self.var_sell_cycles = tk.StringVar(value=str(CONFIG.get("autosell_cycles", 15)))
        ttk.Entry(grp, textvariable=self.var_sell_cycles, width=10).grid(row=1, column=1, sticky="w", padx=12, pady=5)
        ttk.Label(grp, text="Full wash cycles between selling ore/sand", style="Muted.TLabel").grid(
            row=1, column=2, sticky="w"
        )

        ttk.Label(grp, text="Scroll Out / In Notches:").grid(row=2, column=0, sticky="w", pady=5)
        scroll_frame = ttk.Frame(grp, style="Card.TFrame")
        scroll_frame.grid(row=2, column=1, sticky="w", padx=12, pady=5)
        self.var_scroll_out = tk.StringVar(value=str(CONFIG.get("scroll_out_notches", 1)))
        self.var_scroll_in = tk.StringVar(value=str(CONFIG.get("scroll_in_notches", 1)))
        ttk.Entry(scroll_frame, textvariable=self.var_scroll_out, width=5).pack(side="left")
        ttk.Label(scroll_frame, text=" / ").pack(side="left")
        ttk.Entry(scroll_frame, textvariable=self.var_scroll_in, width=5).pack(side="left")
        ttk.Label(grp, text="Camera zoom adjustments before opening backpack", style="Muted.TLabel").grid(
            row=2, column=2, sticky="w"
        )

        coords = CONFIG.get("sell_coords", [[0.4297, 0.5475]])
        first_pt = coords[0] if coords else [0.4297, 0.5475]
        ttk.Label(grp, text="Sell Button Ratio (X, Y):").grid(row=3, column=0, sticky="w", pady=5)
        pt_frame = ttk.Frame(grp, style="Card.TFrame")
        pt_frame.grid(row=3, column=1, columnspan=2, sticky="w", padx=12, pady=5)
        self.var_sell_x = tk.StringVar(value=f"{float(first_pt[0]):.4f}")
        self.var_sell_y = tk.StringVar(value=f"{float(first_pt[1]):.4f}")
        ttk.Entry(pt_frame, textvariable=self.var_sell_x, width=9).pack(side="left")
        ttk.Label(pt_frame, text=" , ").pack(side="left")
        ttk.Entry(pt_frame, textvariable=self.var_sell_y, width=9).pack(side="left")

        btn_row = ttk.Frame(frame)
        btn_row.pack(fill="x", pady=(14, 0))
        ttk.Button(btn_row, text="Pick Sell Button (3s Delay)", command=self.pick_sell_coord).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(
            btn_row,
            text="Test Auto-Sell (F5)",
            style="Accent.TButton",
            command=lambda: threading.Thread(
                target=self.macro.auto_sell, kwargs={"standalone_test": True}, daemon=True
            ).start(),
        ).pack(side="left")

    def _init_webhook_tab(self):
        frame = ttk.Frame(self.tab_webhook, padding=12)
        frame.pack(fill="both", expand=True)

        grp = ttk.LabelFrame(frame, text="Discord Webhook Configuration", padding=12)
        grp.pack(fill="x")
        grp.grid_columnconfigure(1, weight=1)

        self.var_webhook_enabled = tk.BooleanVar(value=bool(CONFIG.get("webhook_enabled", False)))
        ttk.Checkbutton(
            grp,
            text="Enable Discord Periodic Embeds",
            variable=self.var_webhook_enabled,
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(2, 8))

        ttk.Label(grp, text="Webhook URL:").grid(row=1, column=0, sticky="w", pady=6)
        self.var_webhook_url = tk.StringVar(value=str(CONFIG.get("webhook_url", "")))
        ttk.Entry(grp, textvariable=self.var_webhook_url).grid(
            row=1, column=1, columnspan=2, sticky="ew", padx=12, pady=6
        )

        ttk.Label(grp, text="Interval (Minutes):").grid(row=2, column=0, sticky="w", pady=6)
        interval_row = ttk.Frame(grp, style="Card.TFrame")
        interval_row.grid(row=2, column=1, columnspan=2, sticky="w", padx=12, pady=6)
        self.var_webhook_interval = tk.StringVar(value=str(CONFIG.get("webhook_interval_min", 5)))
        ttk.Entry(interval_row, textvariable=self.var_webhook_interval, width=8).pack(side="left")
        ttk.Label(
            interval_row,
            text="  Sends condensed embed report with runtime, cycles, recoveries, and cash gains",
            style="Muted.TLabel",
        ).pack(side="left")

        btn_row = ttk.Frame(frame)
        btn_row.pack(fill="x", pady=(14, 0))
        ttk.Button(
            btn_row,
            text="Send Test Webhook Embed",
            style="Accent.TButton",
            command=self.on_test_webhook,
        ).pack(side="left")

    def on_test_webhook(self):
        self.apply_and_save_gui(silent=True)
        self.macro.vision.poll_money_async(force=True)
        WEBHOOK.send_summary_async(url=self.var_webhook_url.get().strip(), is_test=True)

    def _build_bottom_bar(self):
        bar = ttk.Frame(self.root, style="Dark.TFrame", padding=(10, 6))
        bar.pack(fill="x")

        self.var_topmost = tk.BooleanVar(value=bool(CONFIG.get("always_on_top", True)))
        ttk.Checkbutton(
            bar,
            text="Always on Top",
            style="Bar.TCheckbutton",
            variable=self.var_topmost,
            command=self.toggle_topmost,
        ).pack(side="left")

        ttk.Button(bar, text="Save Settings", command=self.apply_and_save_gui).pack(side="left", padx=10)

        ttk.Button(bar, text="Stop (F3)", style="Stop.TButton", command=self.macro.stop).pack(
            side="right", padx=3
        )
        ttk.Button(
            bar, text="Pause (F2)", style="Pause.TButton", command=self.macro.toggle_pause
        ).pack(side="right", padx=3)
        ttk.Button(bar, text="Start (F1)", style="Start.TButton", command=self.on_start).pack(
            side="right", padx=3
        )

    def toggle_topmost(self):
        top = bool(self.var_topmost.get())
        try:
            self.root.attributes("-topmost", top)
        except Exception:
            pass
        if getattr(self, "gui_hwnd", None):
            enforce_window_topmost(self.gui_hwnd, top)
        CONFIG["always_on_top"] = top

    def apply_and_save_gui(self, silent: bool = False):
        try:
            updated = {
                "walk_to_water_timeout": float(self.var_walk_w.get()),
                "walk_to_dirt_timeout": float(self.var_walk_s.get()),
                "dig_hold_max": float(self.var_dig_max.get()),
                "dig_min_poll_delay": float(self.var_dig_min.get()),
                "next_dig_wait": int(float(self.var_dig_wait.get())),
                "max_digs_per_cycle": int(float(self.var_max_digs.get())),
                "wash_timeout": float(self.var_wash_timeout.get()),
                "autosell_enabled": bool(self.var_autosell.get()),
                "autosell_cycles": int(float(self.var_sell_cycles.get())),
                "scroll_out_notches": int(float(self.var_scroll_out.get())),
                "scroll_in_notches": int(float(self.var_scroll_in.get())),
                "sell_coords": [[round(float(self.var_sell_x.get()), 4), round(float(self.var_sell_y.get()), 4)]],
                "always_on_top": bool(self.var_topmost.get()),
                "webhook_enabled": bool(self.var_webhook_enabled.get()),
                "webhook_url": self.var_webhook_url.get().strip(),
                "webhook_interval_min": max(1, int(float(self.var_webhook_interval.get()))),
            }
            save_config(updated)
            self.toggle_topmost()
            if not silent:
                log_event("[Config] Settings saved.")
        except ValueError as e:
            messagebox.showerror("Invalid Input", f"Check numeric inputs:\n{e}")

    def on_start(self):
        self.apply_and_save_gui(silent=True)
        self.macro.start()

    def on_test_walk(self):
        self.apply_and_save_gui(silent=True)
        self.macro.test_vision_walk()

    def pick_sell_coord(self):
        def _capture():
            log_event("[Picker] Hover over Sell button in Roblox... Capturing in 3 seconds!")
            time.sleep(3.0)
            if not self.macro.vision.find_roblox():
                log_event("[Picker] Roblox window not found.")
                return
            cx, cy, cw, ch = self.macro.vision.update_client_rect()
            pt = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            rx = max(0.0, min(1.0, (pt.x - cx) / max(1, cw)))
            ry = max(0.0, min(1.0, (pt.y - cy) / max(1, ch)))
            self.var_sell_x.set(f"{rx:.4f}")
            self.var_sell_y.set(f"{ry:.4f}")
            self.apply_and_save_gui(silent=True)
            log_event(f"[Picker] Captured coordinates: ({rx:.4f}, {ry:.4f})")

        threading.Thread(target=_capture, daemon=True).start()

    def _start_background_updaters(self):
        self._hotkey_state = {vk: False for vk in (VK_F1, VK_F2, VK_F3, VK_F4, VK_F5, VK_F6)}
        self._poll_hotkeys()
        self._update_ui_telemetry()

    def _poll_hotkeys(self):
        actions = {
            VK_F1: self.on_start,
            VK_F2: self.macro.toggle_pause,
            VK_F3: self.macro.stop,
            VK_F4: self.macro.vision.save_debug_snapshot,
            VK_F5: lambda: threading.Thread(
                target=self.macro.auto_sell, kwargs={"standalone_test": True}, daemon=True
            ).start(),
            VK_F6: self.on_test_walk,
        }
        for vk, callback in actions.items():
            is_down = (user32.GetAsyncKeyState(vk) & 0x8000) != 0
            if is_down and not self._hotkey_state[vk]:
                callback()
            self._hotkey_state[vk] = is_down

        self.root.after(30, self._poll_hotkeys)

    def _append_log_line(self, formatted_msg: str):
        self.txt_log.config(state="normal")
        self.txt_log.insert("end", formatted_msg + "\n")
        lines = int(self.txt_log.index("end-1c").split(".")[0])
        if lines > 250:
            self.txt_log.delete("1.0", f"{lines - 250}.0")
        if self.var_autoscroll.get():
            self.txt_log.see("end")
        self.txt_log.config(state="disabled")

    def _update_ui_telemetry(self):
        if self.var_topmost.get() and getattr(self, "gui_hwnd", None):
            enforce_window_topmost(self.gui_hwnd, True)

        gui_elapsed = int(time.perf_counter() - self.gui_start_time)
        gh, gm, gs = gui_elapsed // 3600, (gui_elapsed % 3600) // 60, gui_elapsed % 60
        self.lbl_gui_rt.config(text=f"GUI: {gh}h {gm:02d}m {gs:02d}s")
        self.lbl_macro_rt.config(text=f"Active: {STATS.get_macro_runtime_str()}")
        self.lbl_task.config(text=f"Task: {STATS.current_task}")

        if self.macro.running and not self.macro.paused:
            self.lbl_status_badge.config(text="● RUNNING", fg=self.ACCENT_GREEN)
        elif self.macro.paused:
            self.lbl_status_badge.config(text="● PAUSED", fg=self.ACCENT_GOLD)
        else:
            self.lbl_status_badge.config(text="● IDLE", fg=self.ACCENT_CYAN)

        self.lbl_cycles.config(text=f"Cycles: {STATS.cycles} (Recoveries: {STATS.recoveries})")
        self.lbl_digs.config(text=f"Perfect: {STATS.get_perfect_rate_str()}")
        self.lbl_walks.config(text=f"Walk W / S: {STATS.last_water_walk:.2f}s / {STATS.last_dirt_walk:.2f}s")

        self.lbl_money_cur.config(text=f"Balance: {STATS.get_current_money_str()}")
        self.lbl_money_gain.config(text=f"Gained: {STATS.get_money_gained_str()}")
        self.lbl_money_rate.config(text=f"Rate: {STATS.get_money_rate_str()}")

        while not LOG_QUEUE.empty():
            msg = LOG_QUEUE.get_nowait()
            self._append_log_line(msg)

        self.root.after(200, self._update_ui_telemetry)

    def on_exit(self):
        self.macro.stop()
        try:
            ctypes.windll.winmm.timeEndPeriod(1)
        except Exception:
            pass
        self.root.destroy()


if __name__ == "__main__":
    macro_instance = ProspectingMacro()
    app_root = tk.Tk()
    gui = ProspectingGUI(app_root, macro_instance)
    log_event("Ready. F1=Start | F2=Pause | F3=Stop | F4=Snap | F5=Sell | F6=WalkTest")
    app_root.mainloop()
