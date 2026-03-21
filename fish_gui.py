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
        self.root.geometry("420x320")
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
        self.status_var = tk.StringVar(value="Idle")

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

        controls = ttk.Frame(self.root)
        controls.pack(fill="x", padx=10, pady=12)

        ttk.Button(controls, text="Start (F6)", command=self.start).pack(side="left", padx=6)
        ttk.Button(controls, text="Stop (F7)", command=self.stop).pack(side="left", padx=6)

        ttk.Label(self.root, textvariable=self.status_var, anchor="w").pack(fill="x", padx=12, pady=8)

        help_text = (
            "This template watches a small screen region for target colors and\n"
            "runs a local demo sequence when found. Replace demo_action_* with\n"
            "your own non-game workflow."
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

    def demo_action_green(self):
        self.set_status("Green detected -> running demo sequence")
        keyboard.press("e")
        time.sleep(0.15)
        keyboard.release("e")

    def demo_action_blue(self):
        self.set_status("Blue detected -> running interval demo")
        for _ in range(10):
            keyboard.press("e")
            time.sleep(0.03)
            keyboard.release("e")
            time.sleep(0.30)

    def worker_loop(self):
        try:
            green = self.parse_rgb(self.green_var.get())
            blue = self.parse_rgb(self.blue_var.get())
            tolerance = self.tolerance_var.get()

            region = {
                "left": self.x_var.get(),
                "top": self.y_var.get(),
                "width": self.w_var.get(),
                "height": self.h_var.get(),
            }

            with mss.mss() as sct:
                self.set_status("Watching region...")
                green_cooldown = 0.0
                blue_cooldown = 0.0

                while self.running:
                    now = time.time()

                    if now >= green_cooldown and self.region_contains_color(sct, region, green, tolerance):
                        self.demo_action_green()
                        green_cooldown = now + 1.0

                    elif now >= blue_cooldown and self.region_contains_color(sct, region, blue, tolerance):
                        self.demo_action_blue()
                        blue_cooldown = now + 2.5

                    time.sleep(0.03)

        except Exception as e:
            self.set_status(f"Error: {e}")
            self.running = False

    def set_status(self, text):
        self.root.after(0, lambda: self.status_var.set(text))

    def start(self):
        if self.running:
            return
        self.running = True
        self.worker = threading.Thread(target=self.worker_loop, daemon=True)
        self.worker.start()
        self.status_var.set("Started")

    def stop(self):
        self.running = False
        self.status_var.set("Stopped")

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
