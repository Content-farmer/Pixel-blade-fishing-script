"""
Fishing Mini-Game Automation with Freeze-Screen Color Picker
-----------------------------------------------------------
• Tkinter GUI
• Fast pixel sampling (MSS)
• Full-screen frozen overlay color picker
• Threaded automation
• Emergency stop (F8)
"""

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

def is_green(r, g, b, tol):
    return g > r + tol and g > b + tol

def brightness(r, g, b):
    return 0.2126*r + 0.7152*g + 0.0722*b

# -------------------------
# App
# -------------------------

class FishingBot:

    def __init__(self, root):

        self.root = root
        self.root.title("Fishing Automation")

        self.running = False
        self.stop_flag = False
        self.holding_key = False
        self.mouse_down = False

        # Settings
        self.x_var = tk.IntVar(value=960)
        self.y_var = tk.IntVar(value=540)
        self.tol_var = tk.IntVar(value=40)
        self.bright_var = tk.IntVar(value=40)
        self.key_var = tk.StringVar(value="e")
        self.cast_var = tk.IntVar(value=300)

        self.status_var = tk.StringVar(value="Idle")
        self.rgb_var = tk.StringVar(value="RGB: ---")
        self.pick_rgb_var = tk.StringVar(value="Picked: ---")

        self.build_gui()

        # Emergency stop
        self.listener = keyboard.GlobalHotKeys({
            '<f8>': self.emergency_stop
        })
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

        ttk.Label(frm, text="Green Tolerance").grid(row=1, column=0)
        ttk.Entry(frm, textvariable=self.tol_var, width=8).grid(row=1, column=1)

        ttk.Label(frm, text="Brightness Δ").grid(row=1, column=2)
        ttk.Entry(frm, textvariable=self.bright_var, width=8).grid(row=1, column=3)

        ttk.Label(frm, text="Action Key").grid(row=2, column=0)
        ttk.Entry(frm, textvariable=self.key_var, width=8).grid(row=2, column=1)

        ttk.Label(frm, text="Mouse Hold ms").grid(row=2, column=2)
        ttk.Entry(frm, textvariable=self.cast_var, width=8).grid(row=2, column=3)

        # Buttons
        ttk.Button(frm, text="Start", command=self.start).grid(row=3, column=0)
        ttk.Button(frm, text="Stop", command=self.stop).grid(row=3, column=1)

        ttk.Button(frm, text="Capture Mouse Pos",
                   command=self.capture_mouse).grid(row=3, column=2)

        ttk.Button(frm, text="Pick Green Color",
                   command=self.pick_color).grid(row=3, column=3)

        # Status
        ttk.Label(frm, textvariable=self.status_var,
                  font=("Arial", 11, "bold")).grid(row=4, column=0, columnspan=4)

        ttk.Label(frm, textvariable=self.rgb_var).grid(row=5, column=0, columnspan=4)
        ttk.Label(frm, textvariable=self.pick_rgb_var).grid(row=6, column=0, columnspan=4)

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

        # Convert screenshot to Tk image
        pixels = screenshot.rgb
        for y in range(screenshot.height):
            row = pixels[y*screenshot.width*3:(y+1)*screenshot.width*3]
            colors = [
                "#%02x%02x%02x" % (row[i], row[i+1], row[i+2])
                for i in range(0, len(row), 3)
            ]
            img.put("{" + " ".join(colors) + "}", to=(0, y))

        canvas = tk.Canvas(overlay, width=screenshot.width,
                           height=screenshot.height, highlightthickness=0)
        canvas.pack()
        canvas.create_image(0, 0, anchor="nw", image=img)

        def on_click(event):

            x = event.x
            y = event.y

            idx = (y * screenshot.width + x) * 3
            r = pixels[idx]
            g = pixels[idx+1]
            b = pixels[idx+2]

            self.pick_rgb_var.set(f"Picked: {r},{g},{b}")

            # Auto-set tolerance suggestion
            self.tol_var.set(max(10, abs(g - max(r, b)) // 2))

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

        if self.mouse_down:
            ms.release(mouse.Button.left)
            self.mouse_down = False

    # -------------------------
    # Automation Loop
    # -------------------------

    def automation_loop(self):

        with mss.mss() as sct:

            while not self.stop_flag:

                x = self.x_var.get()
                y = self.y_var.get()

                bbox = {"top": y, "left": x, "width": 1, "height": 1}
                pixel = sct.grab(bbox).pixel(0, 0)

                r, g, b = pixel[2], pixel[1], pixel[0]
                self.rgb_var.set(f"RGB: {r},{g},{b}")

                if is_green(r, g, b, self.tol_var.get()):

                    self.status_var.set("Green detected — Holding")

                    base_b = brightness(r, g, b)

                    kb.press(self.key_var.get())
                    self.holding_key = True

                    while not self.stop_flag:

                        p = sct.grab(bbox).pixel(0, 0)
                        r2, g2, b2 = p[2], p[1], p[0]

                        if brightness(r2, g2, b2) > base_b + self.bright_var.get():
                            break

                        time.sleep(0.01)

                    kb.release(self.key_var.get())
                    self.holding_key = False

                    self.status_var.set("Casting")

                    ms.press(mouse.Button.left)
                    self.mouse_down = True

                    time.sleep(self.cast_var.get() / 1000)

                    ms.release(mouse.Button.left)
                    self.mouse_down = False

                else:
                    self.status_var.set("Waiting for green")

                time.sleep(0.01)

        self.cleanup()
        self.running = False


# -------------------------
# Run
# -------------------------

if __name__ == "__main__":
    root = tk.Tk()
    FishingBot(root)
    root.mainloop()
