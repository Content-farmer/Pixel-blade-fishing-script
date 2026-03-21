import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox

import mss
import keyboard


class ColorWatcherApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Color Watcher Automation")
        self.root.geometry("420x460")
        self.root.resizable(False, False)

        self.running = False
        self.worker = None

        # Default watch region
        self.x_var = tk.IntVar(value=900)
        self.y_var = tk.IntVar(value=500)
        self.w_var = tk.IntVar(value=120)
        self.h_var = tk.IntVar(value=120)

        # Target colors (RGB)
        self.green_var = tk.StringVar(value="39,162,71")
        self.blue_var = tk.StringVar(value="195,223,224")

        self.tolerance_var = tk.IntVar(value=20)
        self.cast_hold_var = tk.DoubleVar(value=1.0)
        self.green_hold_var = tk.DoubleVar(value=1.5)
        self.followup_green_hold_var = tk.DoubleVar(value=1.0)
        self.tap_count_var = tk.IntVar(value=10)
        self.tap_interval_var = tk.DoubleVar(value=0.30)
        self.post_tap_wait_var = tk.DoubleVar(value=2.0)
        self.status_var = tk.StringVar(value="Idle")
        self.log_last_message = None
        self.log_last_time = 0.0
        self.log_min_interval = 0.8

        self.build_ui()
        keyboard.add_hotkey("f6", self.start)
        keyboard.add_hotkey("f7", self.stop)

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def build_ui(self):
        pad = {"padx": 8, "pady": 6}

        region = ttk.LabelFrame(self.root, text="Watch Region")
        region.pack(fill="x", padx=10, pady=10)

        ttk.Label(region, text="X").grid(row=0, column=0, **pad)
        ttk.Entry(region, textvariable=self.x_var, width=8).grid(row=0, column=1, **pad)

        ttk.Label(region, text="Y").grid(row=0, column=2, **pad)
        ttk.Entry(region, textvariable=self.y_var, width=8).grid(row=0, column=3, **pad)

        ttk.Label(region, text="W").grid(row=1, column=0, **pad)
        ttk.Entry(region, textvariable=self.w_var, width=8).grid(row=1, column=1, **pad)

        ttk.Label(region, text="H").grid(row=1, column=2, **pad)
        ttk.Entry(region, textvariable=self.h_var, width=8).grid(row=1, column=3, **pad)

        colors = ttk.LabelFrame(self.root, text="Target Colors")
        colors.pack(fill="x", padx=10, pady=4)

        ttk.Label(colors, text="Green RGB").grid(row=0, column=0, **pad)
        ttk.Entry(colors, textvariable=self.green_var, width=18).grid(row=0, column=1, **pad)

        ttk.Label(colors, text="Blue RGB").grid(row=1, column=0, **pad)
        ttk.Entry(colors, textvariable=self.blue_var, width=18).grid(row=1, column=1, **pad)

        ttk.Label(colors, text="Tolerance").grid(row=2, column=0, **pad)
        ttk.Entry(colors, textvariable=self.tolerance_var, width=8).grid(row=2, column=1, sticky="w", padx=8, pady=6)

        timings = ttk.LabelFrame(self.root, text="Sequence Timings")
        timings.pack(fill="x", padx=10, pady=4)

        ttk.Label(timings, text="Cast hold (s)").grid(row=0, column=0, **pad)
        ttk.Entry(timings, textvariable=self.cast_hold_var, width=8).grid(row=0, column=1, **pad)

        ttk.Label(timings, text="Green hold (s)").grid(row=0, column=2, **pad)
        ttk.Entry(timings, textvariable=self.green_hold_var, width=8).grid(row=0, column=3, **pad)

        ttk.Label(timings, text="Blue taps").grid(row=1, column=0, **pad)
        ttk.Entry(timings, textvariable=self.tap_count_var, width=8).grid(row=1, column=1, **pad)

        ttk.Label(timings, text="Tap interval (s)").grid(row=1, column=2, **pad)
        ttk.Entry(timings, textvariable=self.tap_interval_var, width=8).grid(row=1, column=3, **pad)

        ttk.Label(timings, text="Follow-up green hold (s)").grid(row=2, column=0, **pad)
        ttk.Entry(timings, textvariable=self.followup_green_hold_var, width=8).grid(row=2, column=1, **pad)

        ttk.Label(timings, text="Post-tap wait (s)").grid(row=2, column=2, **pad)
        ttk.Entry(timings, textvariable=self.post_tap_wait_var, width=8).grid(row=2, column=3, **pad)

        controls = ttk.Frame(self.root)
        controls.pack(fill="x", padx=10, pady=12)

        ttk.Button(controls, text="Start (F6)", command=self.start).pack(side="left", padx=6)
        ttk.Button(controls, text="Stop (F7)", command=self.stop).pack(side="left", padx=6)

        ttk.Label(self.root, textvariable=self.status_var, anchor="w").pack(fill="x", padx=12, pady=8)

        logs = ttk.LabelFrame(self.root, text="Activity Log")
        logs.pack(fill="both", expand=True, padx=10, pady=4)
        self.log_text = tk.Text(logs, height=5, wrap="word", state="disabled")
        self.log_text.pack(fill="both", expand=True, padx=6, pady=6)

        help_text = (
            "Fishing loop:\n"
            "1) Hold E to cast, release.\n"
            "2) On first green circle, hold E and release.\n"
            "3) On follow-up green circles, hold E using follow-up hold time.\n"
            "4) When blue center appears, tap E repeatedly, then wait post-tap.\n"
            "Use F6 to start and F7 to stop."
        )
        ttk.Label(self.root, text=help_text, justify="left").pack(fill="x", padx=12, pady=6)

    def parse_rgb(self, s):
        parts = [p.strip() for p in s.split(",")]
        if len(parts) != 3:
            raise ValueError("RGB must have 3 comma-separated values")
        return tuple(int(v) for v in parts)

    def color_match(self, r, g, b, target, tolerance):
        return (
            abs(r - target[0]) <= tolerance and
            abs(g - target[1]) <= tolerance and
            abs(b - target[2]) <= tolerance
        )

    def region_contains_color(self, sct, region, target, tolerance):
        shot = sct.grab(region)
        raw = shot.rgb  # RGB bytes
        for i in range(0, len(raw), 3):
            r = raw[i]
            g = raw[i + 1]
            b = raw[i + 2]
            if self.color_match(r, g, b, target, tolerance):
                return True
        return False

    def hold_key(self, key, duration):
        keyboard.press(key)
        end_time = time.time() + duration
        while self.running and time.time() < end_time:
            time.sleep(0.01)
        keyboard.release(key)

    def tap_key(self, key, count, interval):
        for _ in range(count):
            if not self.running:
                return
            keyboard.press(key)
            time.sleep(0.03)
            keyboard.release(key)
            time.sleep(interval)

    def action_cast(self, cast_hold):
        self.set_status("Casting rod (holding E)")
        self.log_event("Casting rod.")
        self.hold_key("e", cast_hold)

    def action_green(self, green_hold, label="Green circle detected -> hold E"):
        self.set_status(label)
        self.log_event("Green detected. Holding E.")
        self.hold_key("e", green_hold)

    def action_blue(self, tap_count, tap_interval, post_tap_wait):
        self.set_status("Blue circle detected -> tapping E")
        self.log_event("Blue detected. Tapping E sequence.")
        self.tap_key("e", tap_count, tap_interval)
        if self.running and post_tap_wait > 0:
            self.set_status("Blue sequence complete -> waiting")
            self.log_event(f"Blue sequence complete. Waiting {post_tap_wait:.1f}s.")
            end_time = time.time() + post_tap_wait
            while self.running and time.time() < end_time:
                time.sleep(0.01)

    def wait_until_color_clears(self, sct, region, target, tolerance, max_wait=1.0):
        start_time = time.time()
        while self.running and (time.time() - start_time) < max_wait:
            if not self.region_contains_color(sct, region, target, tolerance):
                return True
            time.sleep(0.02)
        return False

    def worker_loop(self):
        try:
            green = self.parse_rgb(self.green_var.get())
            blue = self.parse_rgb(self.blue_var.get())
            tolerance = self.tolerance_var.get()
            cast_hold = float(self.cast_hold_var.get())
            green_hold = float(self.green_hold_var.get())
            followup_green_hold = float(self.followup_green_hold_var.get())
            tap_count = int(self.tap_count_var.get())
            tap_interval = float(self.tap_interval_var.get())
            post_tap_wait = float(self.post_tap_wait_var.get())

            region = {
                "left": self.x_var.get(),
                "top": self.y_var.get(),
                "width": self.w_var.get(),
                "height": self.h_var.get(),
            }

            with mss.mss() as sct:
                while self.running:
                    self.action_cast(cast_hold)
                    if not self.running:
                        break

                    first_green_seen = False
                    self.log_event("Waiting for first green before blue checks.")

                    # Run green chain until blue center appears.
                    while self.running:
                        if not first_green_seen:
                            if self.region_contains_color(sct, region, green, tolerance):
                                first_green_seen = True
                                self.action_green(green_hold, label="First green circle detected -> hold E")
                                self.wait_until_color_clears(sct, region, green, tolerance, max_wait=1.0)
                            else:
                                time.sleep(0.02)
                            continue

                        if self.region_contains_color(sct, region, blue, tolerance):
                            self.action_blue(tap_count, tap_interval, post_tap_wait)
                            self.wait_until_color_clears(sct, region, blue, tolerance, max_wait=2.0)
                            break

                        if self.region_contains_color(sct, region, green, tolerance):
                            self.action_green(
                                followup_green_hold,
                                label="Follow-up green circle detected -> 1s hold E",
                            )
                            self.wait_until_color_clears(sct, region, green, tolerance, max_wait=1.0)
                            continue

                        time.sleep(0.02)

        except Exception as e:
            self.set_status(f"Error: {e}")
            self.log_event(f"Error: {e}", force=True)
            self.running = False

    def set_status(self, text):
        self.root.after(0, lambda: self.status_var.set(text))

    def log_event(self, message, force=False):
        now = time.time()
        if not force and message == self.log_last_message and (now - self.log_last_time) < self.log_min_interval:
            return
        self.log_last_message = message
        self.log_last_time = now
        stamp = time.strftime("%H:%M:%S")
        self.root.after(0, lambda: self.append_log_line(f"[{stamp}] {message}"))

    def append_log_line(self, line):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line + "\n")
        lines = int(self.log_text.index("end-1c").split(".")[0])
        if lines > 200:
            self.log_text.delete("1.0", "3.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def start(self):
        if self.running:
            return
        self.running = True
        self.worker = threading.Thread(target=self.worker_loop, daemon=True)
        self.worker.start()
        self.status_var.set("Started")
        self.log_event("Automation started.", force=True)

    def stop(self):
        self.running = False
        self.status_var.set("Stopped")
        self.log_event("Automation stopped.", force=True)

    def on_close(self):
        self.stop()
        try:
            keyboard.unhook_all_hotkeys()
        except Exception:
            pass
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = ColorWatcherApp(root)
    root.mainloop()
