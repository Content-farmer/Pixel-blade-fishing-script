"""
Fishing Mini-Game Automation with Freeze-Screen Color Picker
-----------------------------------------------------------
• Tkinter GUI
• Fast pixel sampling (MSS)
• Full-screen frozen overlay color picker
• Threaded automation
• Emergency stop (F8)
"""

import random
import threading
import time
import tkinter as tk
from tkinter import ttk

import mss
from pynput import keyboard, mouse

# -------------------------
# Controllers
# -------------------------

kb = keyboard.Controller()
ms = mouse.Controller()

# -------------------------
# Color Helpers
# -------------------------


def hex_to_rgb(code):
    code = code.strip().lstrip("#")
    return tuple(int(code[i : i + 2], 16) for i in (0, 2, 4))


def within_tolerance(pixel_rgb, target_rgb, tolerance):
    return all(abs(pixel_rgb[i] - target_rgb[i]) <= tolerance for i in range(3))


# -------------------------
# App
# -------------------------


class FishingBot:
    WAIT_COLOR = hex_to_rgb("#4dc86e")
    RELEASE_COLOR = hex_to_rgb("#2dce53")
    CIRCLE_COLOR = hex_to_rgb("#c3dfe0")

    def __init__(self, root):
        self.root = root
        self.root.title("Fishing Automation")

        self.running = False
        self.stop_flag = False
        self.holding_key = False

        # Settings
        self.x_var = tk.IntVar(value=960)
        self.y_var = tk.IntVar(value=540)
        self.key_var = tk.StringVar(value="e")
        self.color_tol_var = tk.IntVar(value=12)
        self.circle_hits_var = tk.IntVar(value=3)
        self.tap_min_var = tk.IntVar(value=130)
        self.tap_max_var = tk.IntVar(value=170)
        self.phase_timeout_var = tk.IntVar(value=10_000)

        self.status_var = tk.StringVar(value="Idle")
        self.rgb_var = tk.StringVar(value="RGB: ---")
        self.pick_rgb_var = tk.StringVar(value="Picked: ---")

        self.build_gui()

        # Emergency stop
        self.listener = keyboard.GlobalHotKeys({"<f8>": self.emergency_stop})
        self.listener.start()

    # -------------------------
    # GUI
    # -------------------------

    def build_gui(self):
        frm = ttk.Frame(self.root, padding=10)
        frm.pack()

        ttk.Label(frm, text="Pixel X").grid(row=0, column=0)
        ttk.Entry(frm, textvariable=self.x_var, width=8).grid(row=0, column=1)

        ttk.Label(frm, text="Pixel Y").grid(row=0, column=2)
        ttk.Entry(frm, textvariable=self.y_var, width=8).grid(row=0, column=3)

        ttk.Label(frm, text="Action Key").grid(row=1, column=0)
        ttk.Entry(frm, textvariable=self.key_var, width=8).grid(row=1, column=1)

        ttk.Label(frm, text="Color Tol ±RGB").grid(row=1, column=2)
        ttk.Entry(frm, textvariable=self.color_tol_var, width=8).grid(row=1, column=3)

        ttk.Label(frm, text="Circle hits").grid(row=2, column=0)
        ttk.Entry(frm, textvariable=self.circle_hits_var, width=8).grid(row=2, column=1)

        ttk.Label(frm, text="Tap min ms").grid(row=2, column=2)
        ttk.Entry(frm, textvariable=self.tap_min_var, width=8).grid(row=2, column=3)

        ttk.Label(frm, text="Tap max ms").grid(row=3, column=0)
        ttk.Entry(frm, textvariable=self.tap_max_var, width=8).grid(row=3, column=1)

        ttk.Label(frm, text="Phase timeout ms").grid(row=3, column=2)
        ttk.Entry(frm, textvariable=self.phase_timeout_var, width=8).grid(row=3, column=3)

        ttk.Button(frm, text="Start", command=self.start).grid(row=4, column=0)
        ttk.Button(frm, text="Stop", command=self.stop).grid(row=4, column=1)

        ttk.Button(frm, text="Capture Mouse Pos", command=self.capture_mouse).grid(
            row=4, column=2
        )

        ttk.Button(frm, text="Pick Color", command=self.pick_color).grid(row=4, column=3)

        ttk.Label(frm, textvariable=self.status_var, font=("Arial", 11, "bold")).grid(
            row=5, column=0, columnspan=4
        )

        ttk.Label(frm, textvariable=self.rgb_var).grid(row=6, column=0, columnspan=4)
        ttk.Label(frm, textvariable=self.pick_rgb_var).grid(row=7, column=0, columnspan=4)

    # -------------------------
    # Controls
    # -------------------------

    def start(self):
        if self.running:
            return

        self.running = True
        self.stop_flag = False
        threading.Thread(target=self.automation_loop, daemon=True).start()

    def stop(self):
        self.stop_flag = True
        self.running = False
        self.cleanup()
        self.status_var.set("Stopped")

    def emergency_stop(self):
        self.stop()
        self.status_var.set("EMERGENCY STOP")

    def capture_mouse(self):
        pos = ms.position
        self.x_var.set(pos[0])
        self.y_var.set(pos[1])

    # -------------------------
    # FREEZE SCREEN COLOR PICKER
    # -------------------------

    def pick_color(self):
        self.status_var.set("Click anywhere to pick color")

        with mss.mss() as sct:
            monitor = sct.monitors[0]
            screenshot = sct.grab(monitor)

        overlay = tk.Toplevel(self.root)
        overlay.attributes("-fullscreen", True)
        overlay.attributes("-topmost", True)

        img = tk.PhotoImage(width=screenshot.width, height=screenshot.height)

        pixels = screenshot.rgb
        for y in range(screenshot.height):
            row = pixels[y * screenshot.width * 3 : (y + 1) * screenshot.width * 3]
            colors = [
                "#%02x%02x%02x" % (row[i], row[i + 1], row[i + 2])
                for i in range(0, len(row), 3)
            ]
            img.put("{" + " ".join(colors) + "}", to=(0, y))

        canvas = tk.Canvas(
            overlay,
            width=screenshot.width,
            height=screenshot.height,
            highlightthickness=0,
        )
        canvas.pack()
        canvas.create_image(0, 0, anchor="nw", image=img)

        def on_click(event):
            x = event.x
            y = event.y

            idx = (y * screenshot.width + x) * 3
            r = pixels[idx]
            g = pixels[idx + 1]
            b = pixels[idx + 2]

            self.pick_rgb_var.set(f"Picked: {r},{g},{b} | Hex: #{r:02x}{g:02x}{b:02x}")

            overlay.destroy()
            self.status_var.set("Color selected")

        overlay.bind("<Button-1>", on_click)

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
        x = self.x_var.get()
        y = self.y_var.get()
        bbox = {"top": y, "left": x, "width": 1, "height": 1}
        pixel = sct.grab(bbox).pixel(0, 0)
        rgb = (pixel[2], pixel[1], pixel[0])
        self.rgb_var.set(f"RGB: {rgb[0]},{rgb[1]},{rgb[2]}")
        return rgb

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
        with mss.mss() as sct:
            while not self.stop_flag:
                timeout_s = max(1.0, self.phase_timeout_var.get() / 1000)
                tol = max(0, self.color_tol_var.get())
                circle_hits_needed = max(1, self.circle_hits_var.get())
                tap_min_ms = max(1, self.tap_min_var.get())
                tap_max_ms = max(tap_min_ms, self.tap_max_var.get())

                # 1) Initial cast behavior: hold E for 500 ms then release.
                self.status_var.set("Init: hold key 500 ms")
                self.hold_key()
                time.sleep(0.5)
                self.release_key()

                # 2) Wait for #4dc86e.
                self.status_var.set("Waiting for #4dc86e")
                start = time.monotonic()
                while not self.stop_flag:
                    if within_tolerance(self.read_pixel(sct), self.WAIT_COLOR, tol):
                        break
                    if time.monotonic() - start > timeout_s:
                        self.status_var.set("Fail-safe: wait color timeout; restarting")
                        break
                    time.sleep(0.01)
                if self.stop_flag:
                    break
                if time.monotonic() - start > timeout_s:
                    continue

                # 3) Hold E until #2dce53.
                self.status_var.set("Holding until #2dce53")
                self.hold_key()
                start = time.monotonic()
                while not self.stop_flag:
                    if within_tolerance(self.read_pixel(sct), self.RELEASE_COLOR, tol):
                        break
                    if time.monotonic() - start > timeout_s:
                        self.status_var.set("Fail-safe: hold timeout; force release")
                        break
                    time.sleep(0.005)
                self.release_key()
                if self.stop_flag:
                    break

                # 4) After a few #c3dfe0 hits, tap E every 130-170ms.
                self.status_var.set("Looking for #c3dfe0 circles")
                hits = 0
                start = time.monotonic()
                while not self.stop_flag and hits < circle_hits_needed:
                    if within_tolerance(self.read_pixel(sct), self.CIRCLE_COLOR, tol):
                        hits += 1
                        time.sleep(0.05)  # debounce repeated reads of same frame
                    elif time.monotonic() - start > timeout_s:
                        self.status_var.set("Fail-safe: no circles; restarting cycle")
                        break
                    else:
                        time.sleep(0.01)
                if self.stop_flag:
                    break
                if hits < circle_hits_needed:
                    continue

                self.status_var.set("Tapping key for circles")
                tap_start = time.monotonic()
                while not self.stop_flag:
                    rgb = self.read_pixel(sct)
                    if not within_tolerance(rgb, self.CIRCLE_COLOR, tol):
                        break
                    self.press_once()
                    delay = random.randint(tap_min_ms, tap_max_ms) / 1000
                    time.sleep(delay)

                    if time.monotonic() - tap_start > timeout_s:
                        self.status_var.set("Fail-safe: tap phase timeout")
                        break

                time.sleep(0.02)

        self.cleanup()
        self.running = False


# -------------------------
# Run
# -------------------------

if __name__ == "__main__":
    root = tk.Tk()
    FishingBot(root)
    root.mainloop()
