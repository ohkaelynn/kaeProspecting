# -*- coding: utf-8 -*-
"""
kaeProspect — Prospecting! Automation Macro 1.0.0
===========================================
Authors : kaelynn & Antigravity (Gemini 4 Argon)
License : Public Community Release

Automated vision-based digging, shoreline navigation, pan washing,
auto-selling, and OCR telemetry macro for Roblox Prospecting!.
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

__author__ = "kaelynn & Antigravity (Gemini 4 Argon)"
__version__ = "1.0.0"

try:
    import pytesseract
    HAS_TESSERACT = True
except Exception:
    pytesseract = None
    HAS_TESSERACT = False

if os.name == "nt":
    _orig_popen = subprocess.Popen

    class _NoWindowPopen(_orig_popen):
        def __init__(self, *args, **kwargs):
            kwargs.setdefault("creationflags", 0)
            kwargs["creationflags"] |= 0x08000000  # CREATE_NO_WINDOW
            super().__init__(*args, **kwargs)

    subprocess.Popen = _NoWindowPopen

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
# CONFIGURATION & TELEMETRY
# ─────────────────────────────────────────────────────────────────────────────
CONFIG_FILE = "prospecting_settings.json"
LOG_QUEUE = queue.Queue()
ROI_SCHEMA_VERSION = 8

DEFAULT_CONFIG = {
    "roi_version": ROI_SCHEMA_VERSION,
    "movement_mode": "Vision",
    "walk_to_water_time": 0.8,
    "walk_to_dirt_time": 0.8,
    "s_key_trim_ms": 0.0,
    "dig_hold_max": 1.150,
    "dig_min_poll_delay": 0.050,
    "next_dig_wait": 1200,
    "max_digs_per_cycle": 14,
    "wash_timeout": 15.0,
    "autosell_enabled": True,
    "autosell_cycles": 5,
    "sell_coords": [[0.4297, 0.5475]],
    "scroll_out_notches": 1,
    "scroll_in_notches": 1,
    "always_on_top": False,
    "webhook_enabled": False,
    "webhook_url": "",
    "webhook_interval_min": 5,
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
    """
    Parse Roblox Prospecting money display (e.g. '$5.56B', 'S1.51k', '5,558,952,146').
    Handles missing or misread '$' symbols (such as 'S'), abbreviated strings with unit
    suffixes, and raw comma-separated or standalone digit strings.
    """
    if not txt:
        return None
    cleaned = txt.replace(" ", "").strip()

    # Primary match: optional '$' or misread 'S' prefix, abbreviated or comma-separated number, optional suffix
    m = re.search(r"[\$Ss]?(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)([kKmMbBtT])?", cleaned)
    if m:
        num_str = m.group(1).replace(",", "")
        suffix = (m.group(2) or "").upper()
        if not ("." in num_str and not suffix):
            try:
                val = float(num_str)
                mult = {"": 1.0, "K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}.get(suffix, 1.0)
                return val * mult
            except ValueError:
                pass

    # Fallback: standalone string of digits/commas with no suffix
    fallback = re.search(r"(?<![.\d])(\d[\d,]*)(?![.\d])", cleaned)
    if fallback:
        raw_digits = fallback.group(1).replace(",", "")
        if raw_digits.isdigit():
            try:
                return float(raw_digits)
            except ValueError:
                pass

    return None


class Stats:
    def __init__(self):
        self.cycles = 0
        self.total_digs = 0
        self.perfect_digs = 0
        self.failsafes = 0
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
                raw = f.read()
            sanitized = re.sub(r"\bTrue\b", "true", raw)
            sanitized = re.sub(r"\bFalse\b", "false", sanitized)
            sanitized = re.sub(r"\bNone\b", "null", sanitized)
            saved = json.loads(sanitized)
            old_roi_ver = saved.get("roi_version", 1)
            cfg.update(saved)
            if old_roi_ver < ROI_SCHEMA_VERSION:
                for k in (
                    "roi_version",
                    "roi_dig_target",
                    "roi_pan_full",
                    "roi_pan_empty",
                    "roi_prompt",
                    "roi_alert",
                    "roi_money",
                ):
                    cfg[k] = DEFAULT_CONFIG[k]
                if float(cfg.get("dig_hold_max", 0.65)) <= 0.65 or float(cfg.get("dig_hold_max", 0.65)) >= 1.80:
                    cfg["dig_hold_max"] = DEFAULT_CONFIG["dig_hold_max"]
                if float(cfg.get("dig_min_poll_delay", 0.08)) >= 0.08:
                    cfg["dig_min_poll_delay"] = DEFAULT_CONFIG["dig_min_poll_delay"]
        except Exception as e:
            print(f"[Config Warning] Failed to parse {CONFIG_FILE}: {e}. Using defaults.")
    return cfg


def save_config(cfg: dict):
    global CONFIG
    CONFIG.update(cfg)
    CONFIG["roi_version"] = ROI_SCHEMA_VERSION
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(CONFIG, f, indent=4)
    except Exception as e:
        print(f"[Config Error] Could not save {CONFIG_FILE}: {e}")


CONFIG = load_config()
save_config(CONFIG)


def log_event(msg: str):
    ts = time.strftime("%H:%M:%S")
    formatted = f"[{ts}] {msg}"
    print(formatted)
    LOG_QUEUE.put(formatted)


def set_task(task_name: str):
    STATS.current_task = task_name


# ─────────────────────────────────────────────────────────────────────────────
# DISCORD WEBHOOK REPORTER
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
                log_event("[Webhook] Please enter a valid Discord Webhook URL first.")
            return

        def _worker():
            if not self._sending_lock.acquire(blocking=False):
                return
            try:
                title = "💎 Prospecting! Macro — Session Summary"
                if is_test:
                    title += " (Test)"

                embed = {
                    "title": title,
                    "color": 0xF1C40F,
                    "fields": [
                        {
                            "name": "⏱️ Macro Runtime",
                            "value": f"`{STATS.get_macro_runtime_str()}`",
                            "inline": True,
                        },
                        {
                            "name": "🔄 Cycles Completed",
                            "value": f"`{STATS.cycles}`",
                            "inline": True,
                        },
                        {
                            "name": "🎯 Perfect Dig Rate",
                            "value": f"`{STATS.get_perfect_rate_str()}`",
                            "inline": True,
                        },
                        {
                            "name": "💰 Money Gained",
                            "value": f"`{STATS.get_money_gained_str()}` (`{STATS.get_money_rate_str()}`)",
                            "inline": True,
                        },
                        {
                            "name": "🏦 Current Balance",
                            "value": f"`{STATS.get_current_money_str()}`",
                            "inline": True,
                        },
                        {
                            "name": "🛡️ Auto-Recoveries",
                            "value": f"`{STATS.failsafes}`",
                            "inline": True,
                        },
                    ],
                    "footer": {
                        "text": "kaeProspect Macro • by kaelynn & Argon 4"
                    },
                    "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                }

                payload = json.dumps({
                    "username": "kaeProspect",
                    "embeds": [embed],
                }).encode("utf-8")

                req = urllib.request.Request(
                    target_url,
                    data=payload,
                    headers={
                        "Content-Type": "application/json",
                        "User-Agent": "kaeProspectMacro/1.0",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=8) as resp:
                    code = resp.getcode()
                if 200 <= code < 300:
                    log_event("[Webhook] Sent Discord summary embed.")
                else:
                    log_event(f"[Webhook Warning] Discord returned HTTP {code}.")
            except Exception as e:
                log_event(f"[Webhook Error] Could not send summary: {e}")
            finally:
                self._sending_lock.release()

        threading.Thread(target=_worker, daemon=True).start()


WEBHOOK = DiscordWebhookReporter()


# ─────────────────────────────────────────────────────────────────────────────
# WINDOWS SENDINPUT & HIGH-PRECISION TIMING
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
    """Sub-millisecond sleep using OS sleep + spinlock tail."""
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
# ROI VISION & OCR ENGINE
# ─────────────────────────────────────────────────────────────────────────────
class RobloxClientVision:
    def __init__(self):
        self.hwnd = None
        self.gui_hwnd = None
        self._local = threading.local()
        self.client_rect = (0, 0, 1920, 1080)
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

        if self.gui_hwnd and CONFIG.get("always_on_top", False):
            enforce_window_topmost(self.gui_hwnd, True)

        cx, cy, cw, ch = self.update_client_rect()
        log_event(f"[System] Locked onto Roblox ({cw}x{ch} at {cx},{cy}).")
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
        """Detects the moving white marker bar within the digging minigame target zone."""
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
        Classifies the bottom interaction prompt:
        - 'DIRT'  : 'Collect Deposit' (wide two-word prompt)
        - 'WATER' : 'Shake' or 'Pan'  (compact centered prompt)
        - 'NONE'  : No prompt visible
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
        """
        Detects red warning banners above Pan Fill ('NOT_ENOUGH_SAND', 'PAN_FULL', or 'NONE').
        """
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
                if "full" in ocr_txt or "wash" in ocr_txt or "water" in ocr_txt or "nearby" in ocr_txt:
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
        """Non-blocking background OCR poll of the bottom-right cash balance."""
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
                wl = "$Ss0123456789.,kKmMbBtT"
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
            ("ALERT_OCR",  CONFIG.get("roi_alert", DEFAULT_CONFIG["roi_alert"]),           (80, 80, 255)),
            ("MONEY_OCR",  CONFIG.get("roi_money", DEFAULT_CONFIG["roi_money"]),           (0, 215, 255)),
        ]
        for name, (rx1, ry1, rx2, ry2), color in rois:
            p1 = (int(cw * rx1), int(ch * ry1))
            p2 = (int(cw * rx2), int(ch * ry2))
            cv2.rectangle(bgr, p1, p2, color, 2)
            cv2.putText(bgr, name, (p1[0], max(16, p1[1] - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.52, color, 2)

        for idx, pt in enumerate(CONFIG.get("sell_coords", []), start=1):
            sx, sy = int(cw * float(pt[0])), int(ch * float(pt[1]))
            cv2.circle(bgr, (sx, sy), 8, (0, 0, 255), -1)
            cv2.putText(bgr, f"SELL #{idx}", (sx + 10, sy + 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)

        out_path = os.path.abspath("debug_vision.png")
        cv2.imwrite(out_path, bgr)
        log_event(f"[Debug F4] Saved calibrated ROI overlay to: {out_path}")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN MACRO CONTROLLER
# ─────────────────────────────────────────────────────────────────────────────
class ProspectingMacro:
    SAND_ALERT_DEBOUNCE_SEC = 2.8

    def __init__(self):
        self.running = False
        self.paused = False
        self.vision = RobloxClientVision()
        self._worker_thread = None
        self._last_sand_alert_time = 0.0
        self._sand_recovery_active = False

    def _can_trigger_sand_alert(self) -> bool:
        now = time.perf_counter()
        if (now - self._last_sand_alert_time) < self.SAND_ALERT_DEBOUNCE_SEC:
            return False
        if self._sand_recovery_active:
            return False
        return True

    def _mark_sand_alert_handled(self):
        self._last_sand_alert_time = time.perf_counter()
        self._sand_recovery_active = True

    def _clear_sand_recovery_if_expired(self, current_alert: str):
        if current_alert != "NOT_ENOUGH_SAND":
            if (time.perf_counter() - self._last_sand_alert_time) >= self.SAND_ALERT_DEBOUNCE_SEC:
                self._sand_recovery_active = False

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
        self._sand_recovery_active = False
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
            log_event("[Hotkey F2] Macro PAUSED. All keys released.")
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
        log_event("[System] Macro STOPPED. All inputs released.")

    def sleep_interruptible(self, duration: float) -> bool:
        t0 = time.perf_counter()
        while (time.perf_counter() - t0) < duration:
            if not self.running or self.paused:
                return False
            time.sleep(0.010)
        return True

    def walk_toward_water(self) -> float:
        mode = CONFIG.get("movement_mode", "Vision")
        target_duration = float(CONFIG.get("walk_to_water_time", 0.550))
        set_task(f"Walking to Water ({mode})")
        log_event(f"[Move] Walking W to water (mode={mode}, target={target_duration:.3f}s)...")

        t0 = time.perf_counter()
        key_down(SC_W)
        try:
            if mode == "Fixed":
                while self.running and not self.paused:
                    rem = target_duration - (time.perf_counter() - t0)
                    if rem <= 0:
                        break
                    precise_sleep(min(0.010, rem))
            else:
                while self.running and not self.paused and (time.perf_counter() - t0 < target_duration):
                    if (time.perf_counter() - t0 > 0.16) and self.vision.get_prompt_state() == "WATER":
                        precise_sleep(0.090)
                        break
                    precise_sleep(0.005)
        finally:
            key_up(SC_W)

        elapsed = time.perf_counter() - t0
        STATS.last_water_walk = round(elapsed, 3)
        log_event(f"[Move] Stopped at water ({STATS.last_water_walk:.3f}s).")
        return elapsed

    def walk_toward_dirt(self, forward_elapsed: float = None, extra_nudge: float = 0.0):
        mode = CONFIG.get("movement_mode", "Vision")
        trim_s = float(CONFIG.get("s_key_trim_ms", 0.0)) / 1000.0

        if mode == "Mirror" and forward_elapsed is not None:
            target_duration = max(0.05, forward_elapsed + trim_s)
        else:
            target_duration = max(0.05, float(CONFIG.get("walk_to_dirt_time", 0.550)) + extra_nudge + trim_s)

        set_task(f"Walking to Dirt ({mode})")
        log_event(f"[Move] Walking S to dirt (mode={mode}, target={target_duration:.3f}s)...")

        t0 = time.perf_counter()
        key_down(SC_S)
        try:
            if mode in ("Fixed", "Mirror"):
                while self.running and not self.paused:
                    rem = target_duration - (time.perf_counter() - t0)
                    if rem <= 0:
                        break
                    precise_sleep(min(0.010, rem))
            else:
                while self.running and not self.paused and (time.perf_counter() - t0 < target_duration):
                    if (time.perf_counter() - t0 > 0.16) and self.vision.get_prompt_state() == "DIRT":
                        precise_sleep(0.090)
                        break
                    precise_sleep(0.005)
        finally:
            key_up(SC_S)

        elapsed = time.perf_counter() - t0
        STATS.last_dirt_walk = round(elapsed, 3)
        log_event(f"[Move] Back at dirt ({STATS.last_dirt_walk:.3f}s).")

    def test_walk_cycle(self):
        if self.running:
            log_event("[Warning] Stop the macro before running Walk Test.")
            return
        if not self.vision.focus_without_resizing():
            return

        def _run():
            self.running = True
            self.paused = False
            try:
                log_event("[Test] Starting W -> Wait 0.5s -> S calibration test...")
                fwd = self.walk_toward_water()
                time.sleep(0.5)
                self.walk_toward_dirt(forward_elapsed=fwd)
                log_event(f"[Test] Complete! W={STATS.last_water_walk:.3f}s | S={STATS.last_dirt_walk:.3f}s.")
            finally:
                self.running = False
                self.release_all_inputs()
                set_task("Idle")

        threading.Thread(target=_run, daemon=True).start()

    def _nudge_forward_to_water(self, max_duration: float = 0.38) -> float:
        t0 = time.perf_counter()
        key_down(SC_W)
        try:
            while self.running and not self.paused and (time.perf_counter() - t0 < max_duration):
                if self.vision.get_prompt_state() == "WATER":
                    precise_sleep(0.085)
                    break
                precise_sleep(0.005)
        finally:
            key_up(SC_W)
        return time.perf_counter() - t0

    def _wash_and_return_sequence(self, wash_timeout: float, cycles_since_sell: int) -> int:
        if self.vision.is_pan_empty():
            log_event("[Vision] Pan is already empty (0 sand) — skipping water trip.")
            return cycles_since_sell

        fwd_duration = self.walk_toward_water()
        extra_nudge = 0.0
        if not self.sleep_interruptible(0.20):
            return cycles_since_sell

        set_task("Washing Pan")
        log_event("[Action] Panning in water until empty...")
        t_wash = time.perf_counter()
        empty_confirm_frames = 0
        wash_succeeded = False
        water_nudges = 0

        while self.running and not self.paused and (time.perf_counter() - t_wash < wash_timeout):
            click(hold=0.025)
            precise_sleep(0.035)
            elapsed_wash = time.perf_counter() - t_wash
            if elapsed_wash > 0.65:
                if self.vision.is_pan_empty():
                    empty_confirm_frames += 1
                    if empty_confirm_frames >= 3:
                        wash_succeeded = True
                        log_event("[Vision] Pan confirmed empty! Waiting 0.85s...")
                        self.sleep_interruptible(0.85)
                        break
                else:
                    empty_confirm_frames = 0
                    alert_now = self.vision.get_alert_banner_state()
                    self._clear_sand_recovery_if_expired(alert_now)
                    if elapsed_wash > 1.0 and alert_now == "NOT_ENOUGH_SAND":
                        wash_succeeded = True
                        if self._can_trigger_sand_alert():
                            self._mark_sand_alert_handled()
                            log_event('[OCR Fallback] "Not enough sand!" in water -> Pan is empty.')
                        self.sleep_interruptible(0.55)
                        break
                    elif alert_now == "PAN_FULL" and water_nudges < 2:
                        water_nudges += 1
                        log_event('[OCR Fallback] "Your pan is full!" while washing -> Stepping W into water.')
                        step_s = self._nudge_forward_to_water(0.35)
                        fwd_duration += step_s
                        extra_nudge += step_s
                        self.sleep_interruptible(0.12)

        if not self.running or self.paused:
            return cycles_since_sell

        if not wash_succeeded:
            STATS.failsafes += 1
            log_event("[Failsafe] Wash timed out or incomplete! Returning to dirt.")

        self.vision.poll_money_async(force=True)

        self.walk_toward_dirt(forward_elapsed=fwd_duration, extra_nudge=extra_nudge)
        self.sleep_interruptible(0.22)

        post_return_alert = self.vision.get_alert_banner_state()
        self._clear_sand_recovery_if_expired(post_return_alert)
        if post_return_alert == "NOT_ENOUGH_SAND" and self._can_trigger_sand_alert():
            self._mark_sand_alert_handled()
            STATS.failsafes += 1
            log_event('[OCR Fallback] Still seeing "Not enough sand!" -> Stepping S to dirt.')
            self.walk_toward_dirt(forward_elapsed=None)
            self.sleep_interruptible(0.20)

        STATS.cycles += 1
        cycles_since_sell += 1
        log_event(f"[System] Cycle #{STATS.cycles} complete.")

        if CONFIG.get("autosell_enabled") and cycles_since_sell >= max(1, int(CONFIG.get("autosell_cycles", 5))):
            self.auto_sell(standalone_test=False)
            cycles_since_sell = 0
            self.vision.poll_money_async(force=True)

        WEBHOOK.check_periodic_send()
        return cycles_since_sell

    def run_loop(self):
        log_event("[Macro Active] Stand at dirt facing water! (F2=Pause, F3=Stop)")
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

                # 0. Check game alert banners with state/time debounce
                alert_state = self.vision.get_alert_banner_state()
                self._clear_sand_recovery_if_expired(alert_state)
                if alert_state == "NOT_ENOUGH_SAND":
                    if self._can_trigger_sand_alert():
                        self._mark_sand_alert_handled()
                        STATS.failsafes += 1
                        log_event('[OCR Fallback] "Not enough sand!" detected -> Walking backward (S) to dirt.')
                        self.walk_toward_dirt(forward_elapsed=None)
                        self.sleep_interruptible(0.22)
                        continue

                # 1. Check if pan is full
                pan_empty_now = self.vision.is_pan_empty()
                pan_full_now = (not pan_empty_now) and (
                    alert_state == "PAN_FULL"
                    or self.vision.is_pan_full()
                    or dig_count_this_cycle >= max_digs
                )
                if pan_full_now:
                    if alert_state == "PAN_FULL":
                        log_event('[OCR Fallback] "Your pan is full!" detected -> Moving to water & washing.')
                    else:
                        log_event(f"[Vision] Pan full ({dig_count_this_cycle} digs). Moving to water...")
                    dig_count_this_cycle = 0
                    cycles_since_sell = self._wash_and_return_sequence(wash_timeout, cycles_since_sell)
                    continue

                # 2. Hold-and-release spade digging minigame
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
                self._clear_sand_recovery_if_expired(post_dig_alert)
                if post_dig_alert == "NOT_ENOUGH_SAND":
                    if self._can_trigger_sand_alert():
                        self._mark_sand_alert_handled()
                        STATS.failsafes += 1
                        log_event('[OCR Fallback] "Not enough sand!" on scoop -> Walking backward (S) to dirt.')
                        self.walk_toward_dirt(forward_elapsed=None)
                        self.sleep_interruptible(0.22)
                        continue
                elif post_dig_alert == "PAN_FULL" and not self.vision.is_pan_empty():
                    log_event('[OCR Fallback] "Your pan is full!" on scoop -> Heading to water.')
                    dig_count_this_cycle = 0
                    cycles_since_sell = self._wash_and_return_sequence(wash_timeout, cycles_since_sell)
                    continue

                self._sand_recovery_active = False
                dig_count_this_cycle += 1
                STATS.total_digs += 1

                if hit_perfect:
                    STATS.perfect_digs += 1
                    log_event(f"[Dig #{dig_count_this_cycle}] Perfect release ({dig_time:.3f}s).")
                else:
                    log_event(f"[Dig #{dig_count_this_cycle}] Max-hold release ({dig_time:.3f}s).")

                set_task("Dig Cooldown")
                wait_s = max(0.05, (float(CONFIG.get("next_dig_wait", 1200)) / 1000.0) - 0.18)
                self.sleep_interruptible(wait_s)

        except Exception as e:
            log_event(f"[Error] Macro loop exception: {e}")
        finally:
            self.release_all_inputs()
            set_task("Idle")

    def auto_sell(self, standalone_test: bool = False):
        if standalone_test and not self.vision.focus_without_resizing():
            return

        set_task("Auto-Selling")
        log_event("[Auto-Sell] Starting Tilde + Scroll Sell sequence...")
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
            log_event(f"[Auto-Sell] Clicking Point #{idx} ({rx:.4f}, {ry:.4f})...")
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
        log_event("[Auto-Sell] Sequence completed.")


# ─────────────────────────────────────────────────────────────────────────────
# HOVERTIP & GUI INTERFACE
# ─────────────────────────────────────────────────────────────────────────────
class Hovertip:
    """Lightweight dark-themed tooltip bound to <Enter>/<Leave> events."""

    def __init__(self, widget: tk.Widget, text: str):
        self.widget = widget
        self.text = text
        self.tip_window = None
        self.widget.bind("<Enter>", self.show_tip, add="+")
        self.widget.bind("<Leave>", self.hide_tip, add="+")

    def show_tip(self, event=None):
        if self.tip_window or not self.text:
            return
        x = self.widget.winfo_rootx() + 14
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.tip_window = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        try:
            tw.wm_attributes("-topmost", True)
            tw.lift()
        except Exception:
            pass
        lbl = tk.Label(
            tw,
            text=self.text,
            justify="left",
            bg="#151822",
            fg="#f3f4f6",
            relief="solid",
            borderwidth=1,
            font=("Segoe UI", 8),
            padx=8,
            pady=4,
        )
        lbl.pack()

    def hide_tip(self, event=None):
        if self.tip_window:
            self.tip_window.destroy()
            self.tip_window = None


class ProspectingGUI:
    BG_DARK = "#12141c"
    BG_PANEL = "#1a1d28"
    BG_CARD = "#212534"
    BG_INPUT = "#151822"
    FG_MAIN = "#f3f4f6"
    FG_MUTED = "#9ca3af"
    ACCENT_PINK = "#f472b6"
    ACCENT_GOLD = "#fbbf24"
    ACCENT_GREEN = "#4ade80"
    ACCENT_BLUE = "#60a5fa"
    BORDER_CLR = "#2e3446"
    LABELFRAME_PAD = (12, 10)

    def __init__(self, root: tk.Tk, macro: ProspectingMacro):
        self.root = root
        self.macro = macro
        self.gui_start_time = time.perf_counter()
        self._tooltips = []

        self.root.title("kaeProspect — Prospecting! Macro v1.0")
        self.root.resizable(True, True)
        self.root.configure(bg=self.BG_DARK)

        self._configure_styles()
        self._build_header()
        self._build_bottom_bar()
        self._build_tabs()

        # Calculate DPI-aware initial size after widgets are realized so nothing clips
        self.root.update_idletasks()
        init_w = max(1000, self.root.winfo_reqwidth() + 60)
        init_h = max(780, self.root.winfo_reqheight() + 120)
        self.root.geometry(f"{init_w}x{init_h}+20+20")
        self.root.minsize(max(900, self.root.winfo_reqwidth() + 20), max(660, self.root.winfo_reqheight() + 20))

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
            foreground=[("selected", self.ACCENT_PINK), ("active", self.FG_MAIN)],
        )

        style.configure(
            "TLabelframe",
            background=self.BG_CARD,
            foreground=self.ACCENT_PINK,
            bordercolor=self.BORDER_CLR,
            lightcolor=self.BORDER_CLR,
            darkcolor=self.BORDER_CLR,
            borderwidth=1,
            relief="solid",
        )
        style.configure(
            "TLabelframe.Label",
            background=self.BG_CARD,
            foreground=self.ACCENT_PINK,
            font=("Segoe UI", 9, "bold"),
            padding=(6, 0),
        )

        style.configure("TLabel", background=self.BG_CARD, foreground=self.FG_MAIN, font=("Segoe UI", 9))
        style.configure("Muted.TLabel", background=self.BG_CARD, foreground=self.FG_MUTED, font=("Segoe UI", 8))
        style.configure(
            "Help.TLabel",
            background=self.BG_CARD,
            foreground=self.ACCENT_BLUE,
            font=("Segoe UI", 8, "bold"),
        )
        style.configure("Panel.TLabel", background=self.BG_PANEL, foreground=self.FG_MAIN, font=("Segoe UI", 9))

        style.configure(
            "TCheckbutton",
            background=self.BG_CARD,
            foreground=self.FG_MAIN,
            font=("Segoe UI", 9),
            indicatorColor=self.BG_INPUT,
        )
        style.map(
            "TCheckbutton",
            background=[("active", self.BG_CARD)],
            foreground=[("active", self.ACCENT_PINK)],
        )
        style.configure(
            "Bar.TCheckbutton",
            background=self.BG_DARK,
            foreground=self.FG_MAIN,
            font=("Segoe UI", 9, "bold"),
        )
        style.map(
            "Bar.TCheckbutton",
            background=[("active", self.BG_DARK)],
            foreground=[("active", self.ACCENT_PINK)],
        )

        style.configure(
            "TEntry",
            fieldbackground=self.BG_INPUT,
            foreground=self.FG_MAIN,
            insertcolor=self.FG_MAIN,
            bordercolor=self.BORDER_CLR,
            lightcolor=self.BORDER_CLR,
            darkcolor=self.BORDER_CLR,
            padding=4,
        )
        style.configure(
            "TCombobox",
            fieldbackground=self.BG_INPUT,
            background=self.BG_CARD,
            foreground=self.FG_MAIN,
            arrowcolor=self.ACCENT_PINK,
            padding=3,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", self.BG_INPUT)],
            foreground=[("readonly", self.FG_MAIN)],
        )

        style.configure(
            "TButton",
            background="#2a3042",
            foreground=self.FG_MAIN,
            bordercolor=self.BORDER_CLR,
            focuscolor=self.BORDER_CLR,
            font=("Segoe UI", 9, "bold"),
            padding=(10, 5),
        )
        style.map(
            "TButton",
            background=[("active", "#363e57"), ("pressed", "#1f2433")],
            foreground=[("active", self.ACCENT_PINK)],
        )

        style.configure(
            "Start.TButton",
            background="#166534",
            foreground="#ffffff",
            font=("Segoe UI", 9, "bold"),
            padding=(12, 5),
        )
        style.map("Start.TButton", background=[("active", "#15803d")])

        style.configure(
            "Pause.TButton",
            background="#854d0e",
            foreground="#ffffff",
            font=("Segoe UI", 9, "bold"),
            padding=(12, 5),
        )
        style.map("Pause.TButton", background=[("active", "#a16207")])

        style.configure(
            "Stop.TButton",
            background="#991b1b",
            foreground="#ffffff",
            font=("Segoe UI", 9, "bold"),
            padding=(12, 5),
        )
        style.map("Stop.TButton", background=[("active", "#b91c1c")])

        style.configure(
            "Accent.TButton",
            background="#831843",
            foreground="#fce7f3",
            font=("Segoe UI", 9, "bold"),
            padding=(11, 5),
        )
        style.map("Accent.TButton", background=[("active", "#9d174d")])

    def _build_header(self):
        hdr = tk.Frame(self.root, bg=self.BG_DARK, padx=14, pady=8)
        hdr.pack(side="top", fill="x")

        tk.Label(
            hdr,
            text="kaeProspect",
            font=("Segoe UI", 12, "bold"),
            bg=self.BG_DARK,
            fg=self.ACCENT_PINK,
        ).pack(side="left")

        tk.Label(
            hdr,
            text="Roblox Proespecting! Macro, Auto-Pan, Auto-Spade, Auto-Sell",
            font=("Segoe UI", 8),
            bg=self.BG_DARK,
            fg=self.FG_MUTED,
        ).pack(side="left", padx=(12, 0))

        self.lbl_status_badge = tk.Label(
            hdr,
            text="● IDLE",
            font=("Segoe UI", 9, "bold"),
            bg=self.BG_CARD,
            fg=self.ACCENT_BLUE,
            padx=12,
            pady=2,
        )
        self.lbl_status_badge.pack(side="right")

    def _build_tabs(self):
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(side="top", fill="both", expand=True, padx=10, pady=(2, 4))

        self.tab_info = ttk.Frame(self.notebook)
        self.tab_move = ttk.Frame(self.notebook)
        self.tab_dig = ttk.Frame(self.notebook)
        self.tab_sell = ttk.Frame(self.notebook)
        self.tab_webhook = ttk.Frame(self.notebook)

        self.notebook.add(self.tab_info, text="   Dashboard & Logs   ")
        self.notebook.add(self.tab_move, text="   Movement   ")
        self.notebook.add(self.tab_dig, text="   Dig & Pan   ")
        self.notebook.add(self.tab_sell, text="   Auto-Sell   ")
        self.notebook.add(self.tab_webhook, text="   Webhook   ")

        self._init_info_tab()
        self._init_move_tab()
        self._init_dig_tab()
        self._init_sell_tab()
        self._init_webhook_tab()

    def _init_info_tab(self):
        top_frame = ttk.Frame(self.tab_info, padding=(10, 10, 10, 8))
        top_frame.pack(fill="x")
        top_frame.grid_columnconfigure(0, weight=1, uniform="cards")
        top_frame.grid_columnconfigure(1, weight=1, uniform="cards")
        top_frame.grid_columnconfigure(2, weight=1, uniform="cards")

        col1 = ttk.LabelFrame(top_frame, text=" Runtime & Status ", padding=self.LABELFRAME_PAD)
        col1.grid(row=0, column=0, sticky="nsew", padx=(0, 6))

        self.lbl_gui_rt = ttk.Label(col1, text="GUI Time: 0h 00m 00s")
        self.lbl_gui_rt.pack(anchor="w", pady=1)
        self.lbl_macro_rt = ttk.Label(col1, text="Macro Time: 0h 00m 00s")
        self.lbl_macro_rt.pack(anchor="w", pady=1)
        self.lbl_task = ttk.Label(
            col1, text="Task: Idle", font=("Segoe UI", 9, "bold"), foreground=self.ACCENT_BLUE
        )
        self.lbl_task.pack(anchor="w", pady=(2, 0))

        col2 = ttk.LabelFrame(top_frame, text=" Session Telemetry ", padding=self.LABELFRAME_PAD)
        col2.grid(row=0, column=1, sticky="nsew", padx=6)

        self.lbl_cycles = ttk.Label(col2, text="Cycles: 0 (Recoveries: 0)")
        self.lbl_cycles.pack(anchor="w", pady=1)
        self.lbl_digs = ttk.Label(
            col2, text="Perfect: 0 / 0 (0.0%)", font=("Segoe UI", 9, "bold"), foreground=self.ACCENT_GOLD
        )
        self.lbl_digs.pack(anchor="w", pady=1)
        self.lbl_walks = ttk.Label(col2, text="Walk W / S: 0.00s / 0.00s")
        self.lbl_walks.pack(anchor="w", pady=(2, 0))

        col3 = ttk.LabelFrame(top_frame, text=" Cash & Earnings ", padding=self.LABELFRAME_PAD)
        col3.grid(row=0, column=2, sticky="nsew", padx=(6, 0))

        self.lbl_money_cur = ttk.Label(col3, text="Balance: --")
        self.lbl_money_cur.pack(anchor="w", pady=1)
        self.lbl_money_gain = ttk.Label(
            col3, text="Gained: +$0", font=("Segoe UI", 9, "bold"), foreground=self.ACCENT_GREEN
        )
        self.lbl_money_gain.pack(anchor="w", pady=1)
        self.lbl_money_rate = ttk.Label(col3, text="Rate: $0/hr", foreground=self.ACCENT_PINK)
        self.lbl_money_rate.pack(anchor="w", pady=(2, 0))

        log_outer = ttk.LabelFrame(self.tab_info, text=" Live Event Log ", padding=self.LABELFRAME_PAD)
        log_outer.pack(fill="both", expand=True, padx=10, pady=(0, 8))

        log_toolbar = ttk.Frame(log_outer, style="Card.TFrame")
        log_toolbar.pack(fill="x", pady=(0, 6))

        self.var_autoscroll = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            log_toolbar, text="Auto-Scroll Log", variable=self.var_autoscroll
        ).pack(side="left")

        ttk.Button(
            log_toolbar, text="Clear Log", command=self.clear_log
        ).pack(side="right", padx=(6, 0))
        ttk.Button(
            log_toolbar, text="Reset Cash", command=self.reset_cash_stats
        ).pack(side="right")

        txt_container = tk.Frame(log_outer, bg="#0d0f16", bd=1, relief="solid")
        txt_container.pack(fill="both", expand=True)

        self.log_scroll = ttk.Scrollbar(txt_container, orient="vertical")
        self.log_scroll.pack(side="right", fill="y")

        self.txt_log = tk.Text(
            txt_container,
            height=11,
            font=("Consolas", 9),
            state="disabled",
            bg="#0d0f16",
            fg="#e5e7eb",
            insertbackground="#e5e7eb",
            selectbackground="#374151",
            padx=8,
            pady=6,
            wrap="word",
            yscrollcommand=self.log_scroll.set,
            relief="flat",
            borderwidth=0,
        )
        self.txt_log.pack(side="left", fill="both", expand=True)
        self.log_scroll.config(command=self.txt_log.yview)

        self.txt_log.tag_config("TIME", foreground="#6b7280")
        self.txt_log.tag_config("HIT", foreground="#4ade80")
        self.txt_log.tag_config("MOVE", foreground="#60a5fa")
        self.txt_log.tag_config("WARN", foreground="#fbbf24")
        self.txt_log.tag_config("ERR", foreground="#f87171")
        self.txt_log.tag_config("SELL", foreground="#f472b6")
        self.txt_log.tag_config("SYS", foreground="#c084fc")
        self.txt_log.tag_config("INFO", foreground="#e5e7eb")

    def clear_log(self):
        self.txt_log.config(state="normal")
        self.txt_log.delete("1.0", "end")
        self.txt_log.config(state="disabled")

    def reset_cash_stats(self):
        STATS.reset_money()
        self.macro.vision.poll_money_async(force=True)
        log_event("[Cash] Session money gain counter reset.")

    def _init_move_tab(self):
        frame = ttk.Frame(self.tab_move, padding=12)
        frame.pack(fill="both", expand=True)

        grp = ttk.LabelFrame(
            frame, text=" Walk Synchronization & Drift Control ", padding=self.LABELFRAME_PAD
        )
        grp.pack(fill="x")
        grp.grid_columnconfigure(2, weight=1)

        ttk.Label(grp, text="Movement Mode:").grid(row=0, column=0, sticky="w", pady=6)
        self.var_move_mode = tk.StringVar(value=CONFIG.get("movement_mode", "Vision"))
        cb_mode = ttk.Combobox(
            grp,
            textvariable=self.var_move_mode,
            values=["Vision", "Mirror", "Fixed"],
            state="readonly",
            width=14,
        )
        cb_mode.grid(row=0, column=1, sticky="w", padx=12, pady=6)

        mode_tips_frame = ttk.Frame(grp, style="Card.TFrame")
        mode_tips_frame.grid(row=0, column=2, sticky="w")

        mode_tooltips = [
            ("Vision", "Relies on on-screen prompt changes. Most reliable."),
            ("Mirror", "Attempts to match forward walk duration perfectly on the way back."),
            ("Fixed", "Uses hardcoded static timings. Failsafe if screen detection breaks."),
        ]
        for idx, (mode_name, tip_text) in enumerate(mode_tooltips):
            lbl_name = ttk.Label(mode_tips_frame, text=mode_name, style="Muted.TLabel")
            lbl_name.pack(side="left", padx=(0 if idx == 0 else 12, 2))
            lbl_q = ttk.Label(mode_tips_frame, text="[?]", style="Help.TLabel", cursor="question_arrow")
            lbl_q.pack(side="left")
            self._tooltips.append(Hovertip(lbl_name, tip_text))
            self._tooltips.append(Hovertip(lbl_q, tip_text))

        ttk.Label(grp, text="Walk Forward (W) Sec:").grid(row=1, column=0, sticky="w", pady=6)
        self.var_walk_w = tk.StringVar(value=str(CONFIG.get("walk_to_water_time", 0.550)))
        ttk.Entry(grp, textvariable=self.var_walk_w, width=12).grid(row=1, column=1, sticky="w", padx=12, pady=6)
        ttk.Label(
            grp, text="Target / cap forward walk duration to water (seconds)", style="Muted.TLabel"
        ).grid(row=1, column=2, sticky="w")

        ttk.Label(grp, text="Walk Backward (S) Sec:").grid(row=2, column=0, sticky="w", pady=6)
        self.var_walk_s = tk.StringVar(value=str(CONFIG.get("walk_to_dirt_time", 0.550)))
        ttk.Entry(grp, textvariable=self.var_walk_s, width=12).grid(row=2, column=1, sticky="w", padx=12, pady=6)
        ttk.Label(
            grp, text="Backward walk duration to dirt (Fixed / Fallback)", style="Muted.TLabel"
        ).grid(row=2, column=2, sticky="w")

        ttk.Label(grp, text="S-Key Trim Offset (ms):").grid(row=3, column=0, sticky="w", pady=6)
        self.var_s_trim = tk.StringVar(value=str(CONFIG.get("s_key_trim_ms", 0.0)))
        ttk.Entry(grp, textvariable=self.var_s_trim, width=12).grid(row=3, column=1, sticky="w", padx=12, pady=6)
        ttk.Label(
            grp, text="+15ms if drifting toward water, -15ms if overshooting beach", style="Muted.TLabel"
        ).grid(row=3, column=2, sticky="w")

        btn_test = ttk.Button(
            frame, text="Test W/S Walk Parity (F6)", style="Accent.TButton", command=self.on_test_walk
        )
        btn_test.pack(anchor="w", pady=(14, 0))

    def _init_dig_tab(self):
        frame = ttk.Frame(self.tab_dig, padding=12)
        frame.pack(fill="both", expand=True)

        grp = ttk.LabelFrame(
            frame, text=" Spade Timing & Panning Settings ", padding=self.LABELFRAME_PAD
        )
        grp.pack(fill="x")
        grp.grid_columnconfigure(2, weight=1)

        ttk.Label(grp, text="Max Dig Hold (sec):").grid(row=0, column=0, sticky="w", pady=6)
        self.var_dig_max = tk.StringVar(value=str(CONFIG.get("dig_hold_max", 1.150)))
        ttk.Entry(grp, textvariable=self.var_dig_max, width=12).grid(row=0, column=1, sticky="w", padx=12, pady=6)
        ttk.Label(
            grp, text="Safety max hold duration while spade rises to green cap", style="Muted.TLabel"
        ).grid(row=0, column=2, sticky="w")

        ttk.Label(grp, text="Min Poll Delay (sec):").grid(row=1, column=0, sticky="w", pady=6)
        self.var_dig_min = tk.StringVar(value=str(CONFIG.get("dig_min_poll_delay", 0.050)))
        ttk.Entry(grp, textvariable=self.var_dig_min, width=12).grid(row=1, column=1, sticky="w", padx=12, pady=6)
        ttk.Label(
            grp, text="Initial settle delay after holding LMB down", style="Muted.TLabel"
        ).grid(row=1, column=2, sticky="w")

        ttk.Label(grp, text="Next Dig Wait (ms):").grid(row=2, column=0, sticky="w", pady=6)
        self.var_dig_wait = tk.StringVar(value=str(CONFIG.get("next_dig_wait", 1200)))
        ttk.Entry(grp, textvariable=self.var_dig_wait, width=12).grid(row=2, column=1, sticky="w", padx=12, pady=6)
        ttk.Label(
            grp, text="Scoop animation cooldown between digs (ms)", style="Muted.TLabel"
        ).grid(row=2, column=2, sticky="w")

        ttk.Label(grp, text="Max Digs Per Cycle:").grid(row=3, column=0, sticky="w", pady=6)
        self.var_max_digs = tk.StringVar(value=str(CONFIG.get("max_digs_per_cycle", 14)))
        ttk.Entry(grp, textvariable=self.var_max_digs, width=12).grid(row=3, column=1, sticky="w", padx=12, pady=6)
        ttk.Label(
            grp, text="Fallback scoop count before washing pan in water", style="Muted.TLabel"
        ).grid(row=3, column=2, sticky="w")

        ttk.Label(grp, text="Wash Timeout (sec):").grid(row=4, column=0, sticky="w", pady=6)
        self.var_wash_timeout = tk.StringVar(value=str(CONFIG.get("wash_timeout", 15.0)))
        ttk.Entry(grp, textvariable=self.var_wash_timeout, width=12).grid(row=4, column=1, sticky="w", padx=12, pady=6)
        ttk.Label(
            grp, text="Max seconds shaking pan in water before failsafe", style="Muted.TLabel"
        ).grid(row=4, column=2, sticky="w")

        btn_snap = ttk.Button(
            frame,
            text="Save Vision ROI Snapshot (F4)",
            style="Accent.TButton",
            command=self.macro.vision.save_debug_snapshot,
        )
        btn_snap.pack(anchor="w", pady=(14, 0))

    def _init_sell_tab(self):
        frame = ttk.Frame(self.tab_sell, padding=12)
        frame.pack(fill="both", expand=True)

        grp = ttk.LabelFrame(
            frame, text=" Auto-Sell Configuration ", padding=self.LABELFRAME_PAD
        )
        grp.pack(fill="x")
        grp.grid_columnconfigure(2, weight=1)

        self.var_autosell = tk.BooleanVar(value=bool(CONFIG.get("autosell_enabled", True)))
        ttk.Checkbutton(grp, text="Enable Periodic Auto-Sell", variable=self.var_autosell).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(2, 8)
        )

        ttk.Label(grp, text="Sell Every N Cycles:").grid(row=1, column=0, sticky="w", pady=6)
        self.var_sell_cycles = tk.StringVar(value=str(CONFIG.get("autosell_cycles", 5)))
        ttk.Entry(grp, textvariable=self.var_sell_cycles, width=10).grid(row=1, column=1, sticky="w", padx=12, pady=6)
        ttk.Label(grp, text="Number of full pan wash cycles before selling", style="Muted.TLabel").grid(
            row=1, column=2, sticky="w"
        )

        ttk.Label(grp, text="Scroll Out / In Notches:").grid(row=2, column=0, sticky="w", pady=6)
        scroll_frame = ttk.Frame(grp, style="Card.TFrame")
        scroll_frame.grid(row=2, column=1, sticky="w", padx=12, pady=6)
        self.var_scroll_out = tk.StringVar(value=str(CONFIG.get("scroll_out_notches", 1)))
        self.var_scroll_in = tk.StringVar(value=str(CONFIG.get("scroll_in_notches", 1)))
        ttk.Entry(scroll_frame, textvariable=self.var_scroll_out, width=5).pack(side="left")
        ttk.Label(scroll_frame, text=" / ").pack(side="left")
        ttk.Entry(scroll_frame, textvariable=self.var_scroll_in, width=5).pack(side="left")
        ttk.Label(grp, text="Camera zoom notches before & after Tilde (~)", style="Muted.TLabel").grid(
            row=2, column=2, sticky="w"
        )

        coords = CONFIG.get("sell_coords", [[0.4297, 0.5475]])
        first_pt = coords[0] if coords else [0.4297, 0.5475]
        ttk.Label(grp, text="Sell Button Ratio (X, Y):").grid(row=3, column=0, sticky="w", pady=6)
        pt_frame = ttk.Frame(grp, style="Card.TFrame")
        pt_frame.grid(row=3, column=1, columnspan=2, sticky="w", padx=12, pady=6)
        self.var_sell_x = tk.StringVar(value=f"{float(first_pt[0]):.4f}")
        self.var_sell_y = tk.StringVar(value=f"{float(first_pt[1]):.4f}")
        ttk.Entry(pt_frame, textvariable=self.var_sell_x, width=9).pack(side="left")
        ttk.Label(pt_frame, text=" , ").pack(side="left")
        ttk.Entry(pt_frame, textvariable=self.var_sell_y, width=9).pack(side="left")

        btn_row = ttk.Frame(frame)
        btn_row.pack(fill="x", pady=(14, 0))
        ttk.Button(btn_row, text="Pick Sell Coord (3s Delay)", command=self.pick_sell_coord).pack(
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

        grp = ttk.LabelFrame(
            frame, text=" Discord Webhook Telemetry ", padding=self.LABELFRAME_PAD
        )
        grp.pack(fill="x")
        grp.grid_columnconfigure(1, weight=1)

        self.var_webhook_enabled = tk.BooleanVar(value=bool(CONFIG.get("webhook_enabled", False)))
        ttk.Checkbutton(
            grp,
            text="Enable Periodic Discord Summary Embeds",
            variable=self.var_webhook_enabled,
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(2, 8))

        ttk.Label(grp, text="Webhook URL:").grid(row=1, column=0, sticky="w", pady=6)
        self.var_webhook_url = tk.StringVar(value=str(CONFIG.get("webhook_url", "")))
        ttk.Entry(grp, textvariable=self.var_webhook_url).grid(
            row=1, column=1, columnspan=2, sticky="ew", padx=12, pady=6
        )

        ttk.Label(grp, text="Send Every (Minutes):").grid(row=2, column=0, sticky="w", pady=6)
        interval_row = ttk.Frame(grp, style="Card.TFrame")
        interval_row.grid(row=2, column=1, columnspan=2, sticky="w", padx=12, pady=6)
        self.var_webhook_interval = tk.StringVar(value=str(CONFIG.get("webhook_interval_min", 5)))
        ttk.Entry(interval_row, textvariable=self.var_webhook_interval, width=8).pack(side="left")
        ttk.Label(
            interval_row,
            text="  Sends condensed embed with runtime, cycles, perfect % & money earned",
            style="Muted.TLabel",
        ).pack(side="left")

        btn_row = ttk.Frame(frame)
        btn_row.pack(fill="x", pady=(14, 0))
        ttk.Button(
            btn_row,
            text="Send Test Summary Embed",
            style="Accent.TButton",
            command=self.on_test_webhook,
        ).pack(side="left")

    def on_test_webhook(self):
        self.apply_and_save_gui(silent=True)
        self.macro.vision.poll_money_async(force=True)
        WEBHOOK.send_summary_async(url=self.var_webhook_url.get().strip(), is_test=True)

    def _build_bottom_bar(self):
        bar = ttk.Frame(self.root, style="Dark.TFrame", padding=(12, 8))
        bar.pack(side="bottom", fill="x")

        self.var_topmost = tk.BooleanVar(value=bool(CONFIG.get("always_on_top", False)))
        ttk.Checkbutton(
            bar,
            text="Always on Top",
            style="Bar.TCheckbutton",
            variable=self.var_topmost,
            command=self.toggle_topmost,
        ).pack(side="left")

        ttk.Button(bar, text="Save Config", command=self.apply_and_save_gui).pack(side="left", padx=10)

        tk.Label(
            bar,
            text="by kaelynn & Argon 4 <3",
            font=("Segoe UI", 8),
            bg=self.BG_DARK,
            fg=self.FG_MUTED,
        ).pack(side="left", padx=(6, 0))

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
                "movement_mode": self.var_move_mode.get(),
                "walk_to_water_time": float(self.var_walk_w.get()),
                "walk_to_dirt_time": float(self.var_walk_s.get()),
                "s_key_trim_ms": float(self.var_s_trim.get()),
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
                log_event("[Config] Settings saved to prospecting_settings.json.")
        except ValueError as e:
            messagebox.showerror("Invalid Input", f"Please check numeric fields:\n{e}")

    def on_start(self):
        self.apply_and_save_gui(silent=True)
        self.macro.start()

    def on_test_walk(self):
        self.apply_and_save_gui(silent=True)
        self.macro.test_walk_cycle()

    def pick_sell_coord(self):
        def _capture():
            log_event("[Picker] Hover mouse over Sell button in Roblox... Capturing in 3s!")
            time.sleep(3.0)
            if not self.macro.vision.find_roblox():
                log_event("[Picker] Error: Roblox window not found.")
                return
            cx, cy, cw, ch = self.macro.vision.update_client_rect()
            pt = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            rx = max(0.0, min(1.0, (pt.x - cx) / max(1, cw)))
            ry = max(0.0, min(1.0, (pt.y - cy) / max(1, ch)))
            self.var_sell_x.set(f"{rx:.4f}")
            self.var_sell_y.set(f"{ry:.4f}")
            self.apply_and_save_gui(silent=True)
            log_event(f"[Picker] Captured Sell Ratio: ({rx:.4f}, {ry:.4f}).")

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

    def _classify_log_tag(self, msg: str) -> str:
        low = msg.lower()
        if "perfect" in low or "confirmed empty" in low or "complete" in low:
            return "HIT"
        if "error" in low or "macro stopped" in low:
            return "ERR"
        if "fallback" in low or "failsafe" in low or "warning" in low or "paused" in low:
            return "WARN"
        if "auto-sell" in low or "cash" in low or "webhook" in low:
            return "SELL"
        if "[move]" in low or "[vision]" in low or "[action]" in low:
            return "MOVE"
        if "[system]" in low or "[config]" in low or "ready" in low:
            return "SYS"
        return "INFO"

    def _append_log_line(self, formatted_msg: str):
        self.txt_log.config(state="normal")
        if formatted_msg.startswith("[") and "]" in formatted_msg[:12]:
            idx = formatted_msg.find("]") + 1
            ts_part = formatted_msg[:idx]
            body_part = formatted_msg[idx:]
            tag = self._classify_log_tag(body_part)
            self.txt_log.insert("end", ts_part, "TIME")
            self.txt_log.insert("end", body_part + "\n", tag)
        else:
            tag = self._classify_log_tag(formatted_msg)
            self.txt_log.insert("end", formatted_msg + "\n", tag)

        lines = int(self.txt_log.index("end-1c").split(".")[0])
        if lines > 300:
            self.txt_log.delete("1.0", f"{lines - 300}.0")

        if self.var_autoscroll.get():
            self.txt_log.see("end")
        self.txt_log.config(state="disabled")

    def _update_ui_telemetry(self):
        gui_elapsed = int(time.perf_counter() - self.gui_start_time)
        gh, gm, gs = gui_elapsed // 3600, (gui_elapsed % 3600) // 60, gui_elapsed % 60
        self.lbl_gui_rt.config(text=f"GUI Time: {gh}h {gm:02d}m {gs:02d}s")
        self.lbl_macro_rt.config(text=f"Macro Time: {STATS.get_macro_runtime_str()}")
        self.lbl_task.config(text=f"Task: {STATS.current_task}")

        if self.macro.running and not self.macro.paused:
            self.lbl_status_badge.config(text="● RUNNING", fg=self.ACCENT_GREEN)
        elif self.macro.paused:
            self.lbl_status_badge.config(text="● PAUSED", fg=self.ACCENT_GOLD)
        else:
            self.lbl_status_badge.config(text="● IDLE", fg=self.ACCENT_BLUE)

        self.lbl_cycles.config(text=f"Cycles: {STATS.cycles} (Recoveries: {STATS.failsafes})")
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
    log_event("System Ready | F1=Start | F2=Pause | F3=Stop | F4=Snap | F5=Sell | F6=WalkTest")
    app_root.mainloop()
