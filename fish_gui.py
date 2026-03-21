import json
import math
import os
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from tkinter import messagebox, ttk

import keyboard
import mss


@dataclass
class Sample:
    """Single online training sample."""

    features: list[float]
    player_pressed_e: int


class PersistenceManager:
    """Loads and saves model parameters + aggregate stats."""

    def __init__(self, path: str = "fishing_model_state.json"):
        self.path = path

    def load(self) -> dict:
        if not os.path.exists(self.path):
            return {}
        with open(self.path, "r", encoding="utf-8") as f:
            return json.load(f)

    def save(self, data: dict) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)


class OnlineLogisticLearner:
    """
    Tiny online logistic model.

    Positive reinforcement idea used here:
    - If prediction matches player behavior, reward = +1.
    - If prediction mismatches player behavior, reward = -1.
    - Gradient update is scaled by reward so matching patterns become more likely,
      and mismatching patterns become less likely in similar states.
    """

    def __init__(self, num_features: int, learning_rate: float = 0.08):
        self.num_features = num_features
        self.learning_rate = learning_rate
        self.weights = [0.0 for _ in range(num_features)]
        self.bias = 0.0

    @staticmethod
    def _sigmoid(z: float) -> float:
        z = max(-30.0, min(30.0, z))
        return 1.0 / (1.0 + math.exp(-z))

    def predict_proba(self, features: list[float]) -> float:
        z = self.bias
        for w, x in zip(self.weights, features):
            z += w * x
        return self._sigmoid(z)

    def predict_action(self, features: list[float], threshold: float = 0.5) -> int:
        return 1 if self.predict_proba(features) >= threshold else 0

    def update(self, sample: Sample, prediction: int) -> tuple[bool, float]:
        y = sample.player_pressed_e
        p = self.predict_proba(sample.features)
        matched = prediction == y
        reward = 1.0 if matched else -1.0

        # Logistic gradient with reward modulation.
        error = y - p
        step = self.learning_rate * reward * error

        for i in range(self.num_features):
            self.weights[i] += step * sample.features[i]
        self.bias += step
        return matched, p

    def to_dict(self) -> dict:
        return {
            "num_features": self.num_features,
            "learning_rate": self.learning_rate,
            "weights": self.weights,
            "bias": self.bias,
        }

    def load_dict(self, data: dict) -> None:
        if not data:
            return
        if data.get("num_features") != self.num_features:
            return
        self.learning_rate = float(data.get("learning_rate", self.learning_rate))
        weights = data.get("weights", self.weights)
        if isinstance(weights, list) and len(weights) == self.num_features:
            self.weights = [float(v) for v in weights]
        self.bias = float(data.get("bias", self.bias))


class GameStateSampler:
    """
    Lightweight feature extractor:
    - Pixel RGB at one coordinate
    - elapsed time since notable color change
    - tiny state flags for simple timing/context clues
    """

    def __init__(self, x: int, y: int, change_threshold: int = 18):
        self.x = x
        self.y = y
        self.change_threshold = change_threshold
        self.last_r = 0
        self.last_g = 0
        self.last_b = 0
        self.last_change_time = time.time()
        self.last_sample_time = time.time()
        self.initialized = False

    def set_point(self, x: int, y: int) -> None:
        self.x = x
        self.y = y

    def _capture_pixel(self, sct: mss.mss) -> tuple[int, int, int]:
        region = {"left": self.x, "top": self.y, "width": 1, "height": 1}
        shot = sct.grab(region)
        # MSS returns BGRA bytes in .raw
        b, g, r, _ = shot.raw[0:4]
        return r, g, b

    def read_features(self, sct: mss.mss) -> list[float]:
        now = time.time()
        r, g, b = self._capture_pixel(sct)

        if not self.initialized:
            self.last_r, self.last_g, self.last_b = r, g, b
            self.last_sample_time = now
            self.last_change_time = now
            self.initialized = True

        delta = abs(r - self.last_r) + abs(g - self.last_g) + abs(b - self.last_b)
        if delta >= self.change_threshold:
            self.last_change_time = now

        elapsed_since_change = min(5.0, now - self.last_change_time)
        elapsed_since_sample = min(1.0, now - self.last_sample_time)

        # tiny flags from simple pixel state (no CV)
        bright_flag = 1.0 if (r + g + b) > 400 else 0.0
        green_dominant_flag = 1.0 if g > r + 10 and g > b + 10 else 0.0

        self.last_r, self.last_g, self.last_b = r, g, b
        self.last_sample_time = now

        # Normalized feature vector
        return [
            r / 255.0,
            g / 255.0,
            b / 255.0,
            min(1.0, delta / 255.0),
            elapsed_since_change / 5.0,
            elapsed_since_sample,
            bright_flag,
            green_dominant_flag,
        ]


class FishingTrainerGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Fishing E-Key Learning Assistant")
        self.root.geometry("520x470")
        self.root.resizable(False, False)

        self.running = False
        self.worker_thread = None

        # Defaults: lightweight sampling and learning
        self.sample_interval_var = tk.DoubleVar(value=0.10)
        self.learning_rate_var = tk.DoubleVar(value=0.08)
        self.threshold_var = tk.DoubleVar(value=0.50)
        self.x_var = tk.IntVar(value=960)
        self.y_var = tk.IntVar(value=540)

        self.status_var = tk.StringVar(value="Idle")
        self.prediction_var = tk.StringVar(value="Prediction: waiting")
        self.correct_var = tk.StringVar(value="Correct: 0")
        self.incorrect_var = tk.StringVar(value="Incorrect: 0")
        self.confidence_var = tk.StringVar(value="Confidence: 0.00")
        self.samples_var = tk.StringVar(value="Samples learned: 0")

        self.correct = 0
        self.incorrect = 0
        self.samples = 0
        self.latest_confidence = 0.0

        self.persistence = PersistenceManager()
        self.learner = OnlineLogisticLearner(num_features=8, learning_rate=self.learning_rate_var.get())
        self.sampler = GameStateSampler(self.x_var.get(), self.y_var.get())

        self._load_state()
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_ui(self) -> None:
        pad = {"padx": 8, "pady": 6}

        cfg = ttk.LabelFrame(self.root, text="Sampling & Model")
        cfg.pack(fill="x", padx=10, pady=8)

        ttk.Label(cfg, text="Pixel X").grid(row=0, column=0, **pad)
        ttk.Entry(cfg, textvariable=self.x_var, width=8).grid(row=0, column=1, **pad)

        ttk.Label(cfg, text="Pixel Y").grid(row=0, column=2, **pad)
        ttk.Entry(cfg, textvariable=self.y_var, width=8).grid(row=0, column=3, **pad)

        ttk.Label(cfg, text="Sample interval (s)").grid(row=1, column=0, **pad)
        ttk.Entry(cfg, textvariable=self.sample_interval_var, width=8).grid(row=1, column=1, **pad)

        ttk.Label(cfg, text="Learning rate").grid(row=1, column=2, **pad)
        ttk.Entry(cfg, textvariable=self.learning_rate_var, width=8).grid(row=1, column=3, **pad)

        ttk.Label(cfg, text="Decision threshold").grid(row=2, column=0, **pad)
        ttk.Entry(cfg, textvariable=self.threshold_var, width=8).grid(row=2, column=1, **pad)

        controls = ttk.Frame(self.root)
        controls.pack(fill="x", padx=10, pady=8)
        ttk.Button(controls, text="Start training", command=self.start_training).pack(side="left", padx=6)
        ttk.Button(controls, text="Stop", command=self.stop_training).pack(side="left", padx=6)

        status = ttk.LabelFrame(self.root, text="Live Status")
        status.pack(fill="x", padx=10, pady=6)
        ttk.Label(status, textvariable=self.status_var).pack(anchor="w", padx=8, pady=4)
        ttk.Label(status, textvariable=self.prediction_var, font=("Segoe UI", 11, "bold")).pack(
            anchor="w", padx=8, pady=4
        )

        stats = ttk.LabelFrame(self.root, text="Training Stats")
        stats.pack(fill="x", padx=10, pady=6)
        ttk.Label(stats, textvariable=self.correct_var).pack(anchor="w", padx=8, pady=2)
        ttk.Label(stats, textvariable=self.incorrect_var).pack(anchor="w", padx=8, pady=2)
        ttk.Label(stats, textvariable=self.confidence_var).pack(anchor="w", padx=8, pady=2)
        ttk.Label(stats, textvariable=self.samples_var).pack(anchor="w", padx=8, pady=2)

        note = (
            "This assistant only recommends whether you should press E.\n"
            "It never sends key input automatically.\n"
            "Learning signal: if prediction matches your real E behavior => reward; otherwise penalty."
        )
        ttk.Label(self.root, text=note, justify="left").pack(fill="x", padx=12, pady=10)

    def _load_state(self) -> None:
        state = self.persistence.load()
        self.learner.load_dict(state.get("model", {}))
        stats = state.get("stats", {})
        self.correct = int(stats.get("correct", 0))
        self.incorrect = int(stats.get("incorrect", 0))
        self.samples = int(stats.get("samples", 0))
        self.latest_confidence = float(stats.get("latest_confidence", 0.0))
        self._refresh_stats()

    def _save_state(self) -> None:
        self.persistence.save(
            {
                "model": self.learner.to_dict(),
                "stats": {
                    "correct": self.correct,
                    "incorrect": self.incorrect,
                    "samples": self.samples,
                    "latest_confidence": self.latest_confidence,
                },
            }
        )

    def _refresh_stats(self) -> None:
        self.correct_var.set(f"Correct: {self.correct}")
        self.incorrect_var.set(f"Incorrect: {self.incorrect}")
        self.confidence_var.set(f"Confidence: {self.latest_confidence:.2f}")
        self.samples_var.set(f"Samples learned: {self.samples}")

    def _set_status(self, text: str) -> None:
        self.root.after(0, lambda: self.status_var.set(text))

    def _set_prediction(self, text: str) -> None:
        self.root.after(0, lambda: self.prediction_var.set(text))

    def start_training(self) -> None:
        if self.running:
            return
        try:
            interval = float(self.sample_interval_var.get())
            lr = float(self.learning_rate_var.get())
            thr = float(self.threshold_var.get())
            if interval <= 0.01:
                raise ValueError("Sample interval must be > 0.01")
            if not 0.0 < lr <= 1.0:
                raise ValueError("Learning rate must be in (0, 1]")
            if not 0.0 < thr < 1.0:
                raise ValueError("Decision threshold must be in (0, 1)")
        except Exception as exc:
            messagebox.showerror("Invalid settings", str(exc))
            return

        self.learner.learning_rate = lr
        self.sampler.set_point(self.x_var.get(), self.y_var.get())
        self.running = True
        self.worker_thread = threading.Thread(target=self._training_loop, daemon=True)
        self.worker_thread.start()
        self._set_status("Training running")

    def stop_training(self) -> None:
        self.running = False
        self._set_status("Stopped")
        self._save_state()

    def _training_loop(self) -> None:
        try:
            with mss.mss() as sct:
                while self.running:
                    features = self.sampler.read_features(sct)
                    threshold = float(self.threshold_var.get())
                    prediction = self.learner.predict_action(features, threshold=threshold)
                    proba = self.learner.predict_proba(features)

                    # Player behavior is the live supervision signal.
                    player_pressed = 1 if keyboard.is_pressed("e") else 0

                    sample = Sample(features=features, player_pressed_e=player_pressed)
                    matched, confidence = self.learner.update(sample, prediction)

                    self.samples += 1
                    self.latest_confidence = confidence
                    if matched:
                        self.correct += 1
                    else:
                        self.incorrect += 1

                    action_text = "PRESS E NOW" if prediction == 1 else "DO NOT PRESS E"
                    self._set_prediction(f"Prediction: {action_text} (p={proba:.2f})")
                    self.root.after(0, self._refresh_stats)

                    # Save periodically so session progress survives crashes.
                    if self.samples % 50 == 0:
                        self._save_state()

                    time.sleep(float(self.sample_interval_var.get()))
        except Exception as exc:
            self.running = False
            self._set_status(f"Error: {exc}")
        finally:
            self._save_state()

    def on_close(self) -> None:
        self.stop_training()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = FishingTrainerGUI(root)
    root.mainloop()
