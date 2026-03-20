"""Fishing automation driven by PNG template matching.

This script keeps the existing `E` key processing behavior:
1. Wait for `circle_popup.png` then press/hold `E`.
2. Wait for `circle_maxe.png` then release `E`.
3. Wait for reel frames `Reel_10.png` down to `Reel_1.png`.
4. Restart from step 1.

PNG assets are loaded from `image_templates/` (created automatically).
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Optional

import cv2
import mss
import numpy as np
from pynput import keyboard


TEMPLATES_DIR = Path(__file__).resolve().parent / "image_templates"
MATCH_THRESHOLD = 0.87
SCAN_DELAY = 0.05


class FishingAutomation:
    def __init__(self) -> None:
        self.running = False
        self.stop_flag = False
        self.keyboard_controller = keyboard.Controller()

        self.circle_popup = None
        self.circle_maxe = None
        self.reel_templates: list[tuple[str, np.ndarray]] = []

        self._ensure_template_folder()
        self._load_templates()

    def _ensure_template_folder(self) -> None:
        TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)

    def _read_template(self, name: str) -> Optional[np.ndarray]:
        path = TEMPLATES_DIR / name
        if not path.exists():
            return None
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        return image

    def _load_templates(self) -> None:
        self.circle_popup = self._read_template("circle_popup.png")
        self.circle_maxe = self._read_template("circle_maxe.png")

        self.reel_templates.clear()
        for i in range(10, 0, -1):
            name = f"Reel_{i}.png"
            template = self._read_template(name)
            if template is not None:
                self.reel_templates.append((name, template))

        missing = []
        if self.circle_popup is None:
            missing.append("circle_popup.png")
        if self.circle_maxe is None:
            missing.append("circle_maxe.png")
        for i in range(10, 0, -1):
            expected = f"Reel_{i}.png"
            if not any(expected == loaded_name for loaded_name, _ in self.reel_templates):
                missing.append(expected)

        if missing:
            print("Missing templates in image_templates/:")
            for item in missing:
                print(f" - {item}")
            print("Add the PNG files, then restart the script.")

    def _screen_bgr(self) -> np.ndarray:
        with mss.mss() as sct:
            monitor = sct.monitors[1]
            shot = np.array(sct.grab(monitor))
        return cv2.cvtColor(shot, cv2.COLOR_BGRA2BGR)

    def _find_template(self, frame: np.ndarray, template: np.ndarray) -> bool:
        if template is None:
            return False
        result = cv2.matchTemplate(frame, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, _ = cv2.minMaxLoc(result)
        return max_val >= MATCH_THRESHOLD

    def _wait_for_template(self, template: np.ndarray, name: str, timeout: float = 20.0) -> bool:
        start = time.monotonic()
        while not self.stop_flag:
            frame = self._screen_bgr()
            if self._find_template(frame, template):
                print(f"Detected {name}")
                return True

            if time.monotonic() - start >= timeout:
                print(f"Timeout waiting for {name}; restarting cycle")
                return False
            time.sleep(SCAN_DELAY)
        return False

    def _wait_for_reel_sequence(self) -> bool:
        for name, template in self.reel_templates:
            if not self._wait_for_template(template, name, timeout=10.0):
                return False
        return True

    def _press_e(self) -> None:
        self.keyboard_controller.press("e")
        print("Pressed E")

    def _release_e(self) -> None:
        self.keyboard_controller.release("e")
        print("Released E")

    def start(self) -> None:
        if self.running:
            return
        self.stop_flag = False
        self.running = True
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self) -> None:
        self.stop_flag = True
        self.running = False
        self._release_e()

    def _run(self) -> None:
        if self.circle_popup is None or self.circle_maxe is None or len(self.reel_templates) != 10:
            print("Templates are incomplete. Populate image_templates and restart.")
            self.running = False
            return

        print("Fishing automation started. Press Ctrl+C to stop.")
        while not self.stop_flag:
            if not self._wait_for_template(self.circle_popup, "circle_popup.png"):
                continue
            self._press_e()

            if not self._wait_for_template(self.circle_maxe, "circle_maxe.png"):
                self._release_e()
                continue
            self._release_e()

            self._wait_for_reel_sequence()

        self.running = False


def main() -> None:
    bot = FishingAutomation()
    bot.start()
    try:
        while bot.running:
            time.sleep(0.2)
    except KeyboardInterrupt:
        print("Stopping...")
    finally:
        bot.stop()


if __name__ == "__main__":
    main()
