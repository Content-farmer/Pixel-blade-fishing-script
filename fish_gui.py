"""
Fishing Mini-Game Automation
---------------------------
• Tkinter GUI
• Fast pixel sampling (MSS)
• Threaded automation
• Emergency stop (F8)
• Optional process-window targeting (Windows)
"""

import ctypes
import random
import threading
import time
import tkinter as tk
from tkinter import scrolledtext, ttk

import mss
from pynput import keyboard, mouse

# -------------------------
# Optional Windows window targeting
# -------------------------

WINDOWS_API_AVAILABLE = False
user32 = None
kernel32 = None

try:
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    WINDOWS_API_AVAILABLE = True
except Exception:
    WINDOWS_API_AVAILABLE = False


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


# -------------------------
# Controllers
# -------------------------

kb = keyboard.Controller()
ms = mouse.Controller()


# -------------------------
# Color Helpers
# -------------------------


def within_tolerance(pixel_rgb, target_rgb, tolerance):
    return all(abs(pixel_rgb[i] - target_rgb[i]) <= tolerance for i in range(3))


# -------------------------
# App
# -------------------------


class FishingBot:
    WAIT_COLOR = (0x4D, 0xC8, 0x6E)
    RELEASE_COLOR = (0x2D, 0xCE, 0x53)
    CIRCLE_COLOR = (0xC3, 0xDF, 0xE0)

    def __init__(self, root):
        self.root = root
        self.root.title("Fishing Automation")
        self.root.configure(bg="#1e1e1e")

        self.running = False
        self.stop_flag = False
        self.holding_key = False
        self.last_rgb_update = 0.0
        self.max_log_lines = 300
        self.last_log_message = ""
        self.last_log_time = 0.0
        self.last_target_warning = 0.0

        # Settings
        self.x_var = tk.IntVar(value=960)
        self.y_var = tk.IntVar(value=540)
        self.key_var = tk.StringVar(value="e")
        self.color_tol_var = tk.IntVar(value=12)
        self.sample_radius_var = tk.IntVar(value=1)
        self.circle_hits_var = tk.IntVar(value=3)
        self.tap_min_var = tk.IntVar(value=130)
        self.tap_max_var = tk.IntVar(value=170)
        self.phase_timeout_var = tk.IntVar(value=10_000)
        self.use_window_var = tk.BooleanVar(value=True)
        self.selected_window_var = tk.StringVar(value="")
        self.window_choices = []

        self.status_var = tk.StringVar(value="Idle")
        self.rgb_var = tk.StringVar(value="RGB: ---")

        self.apply_dark_mode()
        self.build_gui()
        self.refresh_window_list(log_refresh=False)

        # Emergency stop
        self.listener = keyboard.GlobalHotKeys({"<f8>": self.emergency_stop})
        self.listener.start()
        self.log("Ready. Set X/Y as coordinates relative to target window if window targeting is enabled.")

    # -------------------------
    # GUI
    # -------------------------

    def apply_dark_mode(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", background="#1e1e1e", foreground="#e6e6e6", fieldbackground="#2a2a2a")
        style.configure("TLabel", background="#1e1e1e", foreground="#e6e6e6")
        style.configure("TFrame", background="#1e1e1e")
        style.configure("TCheckbutton", background="#1e1e1e", foreground="#e6e6e6")
        style.configure("TButton", background="#2f2f2f", foreground="#f5f5f5")
        style.map("TButton", background=[("active", "#3b3b3b")])
        style.configure("TCombobox", fieldbackground="#2a2a2a", foreground="#e6e6e6")
        style.configure("TEntry", fieldbackground="#2a2a2a", foreground="#e6e6e6")

    def build_gui(self):
        frm = ttk.Frame(self.root, padding=10)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="Pixel X").grid(row=0, column=0, sticky="w")
        ttk.Entry(frm, textvariable=self.x_var, width=10).grid(row=0, column=1, sticky="w")

        ttk.Label(frm, text="Pixel Y").grid(row=0, column=2, sticky="w")
        ttk.Entry(frm, textvariable=self.y_var, width=10).grid(row=0, column=3, sticky="w")

        ttk.Label(frm, text="Action Key").grid(row=1, column=0, sticky="w")
        ttk.Entry(frm, textvariable=self.key_var, width=10).grid(row=1, column=1, sticky="w")

        ttk.Label(frm, text="Color Tol ±RGB").grid(row=1, column=2, sticky="w")
        ttk.Entry(frm, textvariable=self.color_tol_var, width=10).grid(row=1, column=3, sticky="w")

        ttk.Label(frm, text="Sample radius px").grid(row=2, column=0, sticky="w")
        ttk.Entry(frm, textvariable=self.sample_radius_var, width=10).grid(row=2, column=1, sticky="w")

        ttk.Label(frm, text="Circle hits").grid(row=2, column=2, sticky="w")
        ttk.Entry(frm, textvariable=self.circle_hits_var, width=10).grid(row=2, column=3, sticky="w")

        ttk.Label(frm, text="Tap min ms").grid(row=3, column=0, sticky="w")
        ttk.Entry(frm, textvariable=self.tap_min_var, width=10).grid(row=3, column=1, sticky="w")

        ttk.Label(frm, text="Tap max ms").grid(row=3, column=2, sticky="w")
        ttk.Entry(frm, textvariable=self.tap_max_var, width=10).grid(row=3, column=3, sticky="w")

        ttk.Label(frm, text="Phase timeout ms").grid(row=4, column=0, sticky="w")
        ttk.Entry(frm, textvariable=self.phase_timeout_var, width=10).grid(row=4, column=1, sticky="w")

        ttk.Checkbutton(
            frm,
            text="Target selected app window",
            variable=self.use_window_var,
        ).grid(row=4, column=2, sticky="w")

        ttk.Label(frm, text="Application").grid(row=5, column=0, sticky="w")
        self.window_combo = ttk.Combobox(
            frm,
            textvariable=self.selected_window_var,
            values=self.window_choices,
            width=45,
            state="readonly",
        )
        self.window_combo.grid(row=5, column=1, columnspan=2, sticky="we")
        ttk.Button(frm, text="Refresh Apps", command=self.refresh_window_list).grid(row=5, column=3, sticky="we")

        ttk.Button(frm, text="Start", command=self.start).grid(row=6, column=0, sticky="we")
        ttk.Button(frm, text="Stop", command=self.stop).grid(row=6, column=1, sticky="we")
        ttk.Button(frm, text="Clear Log", command=self.clear_log).grid(row=6, column=2, sticky="we")
        ttk.Button(frm, text="Capture Mouse Pos", command=self.capture_mouse).grid(
            row=6, column=3, sticky="we"
        )

        ttk.Label(frm, textvariable=self.status_var, font=("Arial", 11, "bold")).grid(
            row=7, column=0, columnspan=4, sticky="w"
        )

        ttk.Label(frm, textvariable=self.rgb_var).grid(row=8, column=0, columnspan=4, sticky="w")

        self.log_box = scrolledtext.ScrolledText(frm, width=80, height=12, state="disabled")
        self.log_box.grid(row=9, column=0, columnspan=4, pady=(8, 0), sticky="nsew")
        self.log_box.configure(bg="#151515", fg="#e6e6e6", insertbackground="#e6e6e6")

        frm.columnconfigure(0, weight=1)
        frm.columnconfigure(1, weight=1)
        frm.columnconfigure(2, weight=1)
        frm.columnconfigure(3, weight=1)
        frm.rowconfigure(9, weight=1)

    def log(self, message):
        now = time.monotonic()
        if message == self.last_log_message and now - self.last_log_time < 0.6:
            return
        self.last_log_message = message
        self.last_log_time = now

        ts = time.strftime("%H:%M:%S")
        line = f"[{ts}] {message}\n"
        self.log_box.configure(state="normal")
        self.log_box.insert("end", line)
        line_count = int(self.log_box.index("end-1c").split(".")[0])
        if line_count > self.max_log_lines:
            self.log_box.delete("1.0", f"{line_count - self.max_log_lines + 1}.0")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def clear_log(self):
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")
        self.log("Log cleared.")

    # -------------------------
    # Controls
    # -------------------------

    def start(self):
        if self.running:
            return

        self.running = True
        self.stop_flag = False
        self.log("Starting automation thread.")
        threading.Thread(target=self.automation_loop, daemon=True).start()

    def stop(self):
        self.stop_flag = True
        self.running = False
        self.cleanup()
        self.status_var.set("Stopped")
        self.log("Stopped.")

    def emergency_stop(self):
        self.stop()
        self.status_var.set("EMERGENCY STOP")
        self.log("Emergency stop triggered (F8).")

    def capture_mouse(self):
        pos = ms.position
        self.x_var.set(pos[0])
        self.y_var.set(pos[1])
        self.log(f"Captured mouse position: {pos[0]}, {pos[1]}")

    # -------------------------
    # Window targeting helpers
    # -------------------------

    def _window_rect_for_hwnd(self, hwnd):
        if not WINDOWS_API_AVAILABLE:
            return None

        if not hwnd:
            return None

        rect = RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None

        w = rect.right - rect.left
        h = rect.bottom - rect.top
        if w <= 0 or h <= 0:
            return None
        return {"left": rect.left, "top": rect.top, "width": w, "height": h}

    def _list_windows(self):
        if not WINDOWS_API_AVAILABLE:
            return []

        windows = []
        enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def callback(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length == 0:
                return True
            title_buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, title_buffer, length + 1)
            title = title_buffer.value.strip()
            if not title:
                return True

            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

            h_proc = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
            if not h_proc:
                return True

            try:
                exe_name_buffer = ctypes.create_unicode_buffer(260)
                size = ctypes.c_ulong(260)
                if not ctypes.windll.psapi.GetModuleBaseNameW(
                    h_proc,
                    None,
                    exe_name_buffer,
                    size.value,
                ):
                    return True
                exe_name = exe_name_buffer.value
                windows.append((hwnd, f"{exe_name} | {title}"))
                return True
            finally:
                kernel32.CloseHandle(h_proc)

        user32.EnumWindows(enum_proc(callback), 0)
        return windows

    def refresh_window_list(self, log_refresh=True):
        windows = self._list_windows()
        self.window_choices = [f"[{int(hwnd)}] {label}" for hwnd, label in windows]
        self.window_combo["values"] = self.window_choices
        if self.window_choices and self.selected_window_var.get() not in self.window_choices:
            self.selected_window_var.set(self.window_choices[0])
        if log_refresh:
            self.log(f"Application list refreshed ({len(self.window_choices)} windows).")

    def _selected_hwnd(self):
        selected = self.selected_window_var.get().strip()
        if not selected.startswith("["):
            return None
        try:
            end = selected.index("]")
            return int(selected[1:end])
        except (ValueError, IndexError):
            return None

    def _resolve_capture_origin(self):
        x = self.x_var.get()
        y = self.y_var.get()

        if not self.use_window_var.get():
            return x, y

        rect = self._window_rect_for_hwnd(self._selected_hwnd())
        if rect is None:
            now = time.monotonic()
            if now - self.last_target_warning >= 3:
                self.log("Window target unavailable. Falling back to absolute screen coordinates.")
                self.last_target_warning = now
            return x, y

        target_x = rect["left"] + x
        target_y = rect["top"] + y
        return target_x, target_y

    # -------------------------
    # Safety Cleanup
    # -------------------------

    def cleanup(self):
        if self.holding_key:
            kb.release(self.key_var.get())
            self.holding_key = False

    # -------------------------
    # Low-level helpers
    # -------------------------

    def read_pixel(self, sct):
        x, y = self._resolve_capture_origin()
        sample_radius = max(0, self.sample_radius_var.get())
        sample_size = (sample_radius * 2) + 1
        bbox = {
            "top": y - sample_radius,
            "left": x - sample_radius,
            "width": sample_size,
            "height": sample_size,
        }
        shot = sct.grab(bbox)
        sum_r = 0
        sum_g = 0
        sum_b = 0
        for sy in range(sample_size):
            for sx in range(sample_size):
                pixel = shot.pixel(sx, sy)
                sum_b += pixel[0]
                sum_g += pixel[1]
                sum_r += pixel[2]
        pixels = sample_size * sample_size
        rgb = (sum_r // pixels, sum_g // pixels, sum_b // pixels)

        now = time.monotonic()
        if now - self.last_rgb_update >= 0.1:  # throttle UI updates for speed
            self.rgb_var.set(f"RGB: {rgb[0]},{rgb[1]},{rgb[2]}")
            self.last_rgb_update = now

        return rgb

    def wait_for_color(self, sct, target_rgb, tol, timeout_s, status_text, loop_delay=0.001):
        self.status_var.set(status_text)
        self.log(f"{status_text} (target={target_rgb}, tol={tol}, timeout={timeout_s:.2f}s)")
        start = time.monotonic()

        while not self.stop_flag:
            if within_tolerance(self.read_pixel(sct), target_rgb, tol):
                self.log(f"Matched target color {target_rgb}.")
                return True

            if time.monotonic() - start > timeout_s:
                self.log(f"Timeout waiting for target color {target_rgb}.")
                return False

            if loop_delay > 0:
                time.sleep(loop_delay)

        return False

    def press_once(self):
        key = self.key_var.get()
        kb.press(key)
        kb.release(key)

    def hold_key(self):
        if not self.holding_key:
            kb.press(self.key_var.get())
            self.holding_key = True

    def release_key(self):
        if self.holding_key:
            kb.release(self.key_var.get())
            self.holding_key = False

    # -------------------------
    # Automation Loop
    # -------------------------

    def automation_loop(self):
        self.log("Automation loop entered.")
        with mss.mss() as sct:
            while not self.stop_flag:
                timeout_s = max(1.0, self.phase_timeout_var.get() / 1000)
                tol = max(0, self.color_tol_var.get())
                circle_hits_needed = max(1, self.circle_hits_var.get())
                tap_min_ms = max(1, self.tap_min_var.get())
                tap_max_ms = max(tap_min_ms, self.tap_max_var.get())

                # 1) Initial cast behavior: hold key for 500 ms then release.
                self.status_var.set("Init: hold key 500 ms")
                self.log("Phase 1: initial cast (hold 500ms)")
                self.hold_key()
                time.sleep(0.5)
                self.release_key()

                # 2) Wait for wait color.
                matched = self.wait_for_color(
                    sct,
                    self.WAIT_COLOR,
                    tol,
                    timeout_s,
                    "Waiting for #4dc86e",
                    loop_delay=0.001,
                )
                if self.stop_flag:
                    break
                if not matched:
                    self.status_var.set("Fail-safe: wait color timeout; restarting")
                    continue

                # 3) Hold key until release color.
                self.status_var.set("Holding until #2dce53")
                self.log("Phase 3: hold key until release color")
                self.hold_key()
                matched = self.wait_for_color(
                    sct,
                    self.RELEASE_COLOR,
                    tol,
                    timeout_s,
                    "Holding until #2dce53",
                    loop_delay=0.001,
                )
                self.release_key()
                if self.stop_flag:
                    break
                if not matched:
                    self.status_var.set("Fail-safe: hold timeout; force release")
                    continue

                # 4) Require N circle-color hits.
                self.status_var.set("Looking for #c3dfe0 circles")
                self.log(f"Phase 4: looking for circle color hits ({circle_hits_needed} needed)")
                hits = 0
                start = time.monotonic()

                while not self.stop_flag and hits < circle_hits_needed:
                    if within_tolerance(self.read_pixel(sct), self.CIRCLE_COLOR, tol):
                        hits += 1
                        self.log(f"Circle hit {hits}/{circle_hits_needed}")
                        time.sleep(0.02)
                    elif time.monotonic() - start > timeout_s:
                        self.status_var.set("Fail-safe: no circles; restarting cycle")
                        self.log("Circle detection timed out; restarting cycle.")
                        break
                    else:
                        time.sleep(0.001)

                if self.stop_flag:
                    break
                if hits < circle_hits_needed:
                    continue

                # 5) Tap key while circle color stays active.
                self.status_var.set("Tapping key for circles")
                self.log("Phase 5: tap key while circle color remains")
                tap_start = time.monotonic()
                while not self.stop_flag:
                    rgb = self.read_pixel(sct)
                    if not within_tolerance(rgb, self.CIRCLE_COLOR, tol):
                        self.log("Circle color ended; restarting cycle.")
                        break

                    self.press_once()
                    delay = random.randint(tap_min_ms, tap_max_ms) / 1000
                    time.sleep(delay)

                    if time.monotonic() - tap_start > timeout_s:
                        self.status_var.set("Fail-safe: tap phase timeout")
                        self.log("Tap phase timeout reached.")
                        break

                time.sleep(0.01)

        self.cleanup()
        self.running = False
        self.log("Automation loop exited.")


# -------------------------
# Run
# -------------------------

if __name__ == "__main__":
    root = tk.Tk()
    FishingBot(root)
    root.mainloop()
