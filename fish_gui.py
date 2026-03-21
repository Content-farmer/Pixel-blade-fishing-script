import json
import math
import os
import threading
import time
import tkinter as tk
from dataclasses import dataclass, asdict
from queue import Empty, Queue
from tkinter import messagebox, scrolledtext, ttk

import keyboard
import mss


@dataclass
class TrainingStats:
    """Aggregate counters persisted between sessions."""

    total_samples: int = 0
    correct_predictions: int = 0
    incorrect_predictions: int = 0
    reinforcement_count: int = 0
    penalty_count: int = 0
    total_hold_seconds: float = 0.0


class Logger:
    """
    Human-readable logger.

    - Writes log lines to an in-memory queue so the GUI can display them safely from
      the main Tkinter thread.
    - Optionally appends the same lines to a plain text file for later inspection.
    """

    def __init__(self, log_file: str = "fishing_trainer.log"):
        self.log_file = log_file
        self.queue: Queue[str] = Queue()

    def log(self, message: str) -> None:
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{timestamp}] {message}"
        self.queue.put(line)
        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            # Logging should never crash training; if disk write fails, GUI still works.
            pass


class ModelStorage:
    """Save/load model weights and training stats so learning continues next run."""

    def __init__(self, path: str = "fishing_model_state.json"):
        self.path = path

    def load(self) -> dict:
        if not os.path.exists(self.path):
            return {}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}

    def save(self, model_data: dict, stats: TrainingStats) -> None:
        payload = {
            "model": model_data,
            "stats": asdict(stats),
            "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)


class OnlineLearner:
    """
    Lightweight online logistic regression.

    The model predicts probability that player will press E for current state.
    We convert this into:
      - prediction 1 => "press E now"
      - prediction 0 => "do not press E now"

    Online reinforcement logic:
      - If prediction matches player's true behavior, reward (+1) and reinforce.
      - If prediction mismatches, penalty (-1) and push weights away from this pattern.
    """

    def __init__(self, num_features: int, learning_rate: float = 0.08):
        self.num_features = num_features
        self.learning_rate = learning_rate
        self.weights = [0.0] * num_features
        self.bias = 0.0

    @staticmethod
    def _sigmoid(z: float) -> float:
        z = max(-30.0, min(30.0, z))
        return 1.0 / (1.0 + math.exp(-z))

    def predict_probability(self, features: list[float]) -> float:
        z = self.bias
        for w, x in zip(self.weights, features):
            z += w * x
        return self._sigmoid(z)

    def predict(self, features: list[float], threshold: float) -> tuple[int, float]:
        p = self.predict_probability(features)
        prediction = 1 if p >= threshold else 0
        return prediction, p

    def update(
        self,
        features: list[float],
        true_label: int,
        predicted_label: int,
        hold_ratio: float = 0.0,
    ) -> tuple[bool, str]:
        """
        Update the model immediately after each labeled sample.

        We use logistic gradient: grad ~ (y - p) * x
        and multiply by reward sign:
          +1 for match, -1 for mismatch.
        This keeps the reinforcement behavior explicit and easy to read.
        """
        p = self.predict_probability(features)
        matched = predicted_label == true_label
        reward_sign = 1.0 if matched else -1.0

        error = true_label - p
        hold_boost = 1.0 + max(0.0, min(1.0, hold_ratio))
        step = self.learning_rate * reward_sign * error * hold_boost

        for i in range(self.num_features):
            self.weights[i] += step * features[i]
        self.bias += step

        feedback = "positive reinforcement" if matched else "penalty reinforcement"
        return matched, feedback

    def to_dict(self) -> dict:
        return {
            "num_features": self.num_features,
            "learning_rate": self.learning_rate,
            "weights": self.weights,
            "bias": self.bias,
        }

    def load_dict(self, data: dict) -> bool:
        if not data:
            return False
        saved_num_features = int(data.get("num_features", -1))
        if saved_num_features > self.num_features or saved_num_features <= 0:
            return False

        weights = data.get("weights", [])
        if not isinstance(weights, list) or len(weights) != saved_num_features:
            return False

        self.learning_rate = float(data.get("learning_rate", self.learning_rate))
        self.weights = [float(v) for v in weights]
        if saved_num_features < self.num_features:
            self.weights.extend([0.0] * (self.num_features - saved_num_features))
        self.bias = float(data.get("bias", 0.0))
        return True


class GameStateSampler:
    """
    Lightweight state extraction only (no heavy CV/OCR):
      - single pixel color (R,G,B)
      - color-change magnitude
      - timing since last notable color change
      - simple boolean flags
    """

    def __init__(self, x: int, y: int, change_threshold: int = 20):
        self.x = x
        self.y = y
        self.change_threshold = change_threshold

        self.initialized = False
        self.last_rgb = (0, 0, 0)
        self.last_change_time = time.time()
        self.last_sample_time = time.time()

    def set_point(self, x: int, y: int) -> None:
        self.x = x
        self.y = y

    def _read_pixel(self, sct: mss.mss) -> tuple[int, int, int]:
        shot = sct.grab({"left": self.x, "top": self.y, "width": 1, "height": 1})
        b, g, r, _ = shot.raw[0:4]
        return r, g, b

    def sample(self, sct: mss.mss) -> tuple[list[float], dict]:
        now = time.time()
        r, g, b = self._read_pixel(sct)

        if not self.initialized:
            self.last_rgb = (r, g, b)
            self.last_change_time = now
            self.last_sample_time = now
            self.initialized = True

        lr, lg, lb = self.last_rgb
        delta = abs(r - lr) + abs(g - lg) + abs(b - lb)

        if delta >= self.change_threshold:
            self.last_change_time = now

        elapsed_since_change = min(5.0, now - self.last_change_time)
        elapsed_since_sample = min(1.0, now - self.last_sample_time)

        bright_flag = 1.0 if (r + g + b) > 420 else 0.0
        green_dominant_flag = 1.0 if g > (r + 8) and g > (b + 8) else 0.0

        features = [
            r / 255.0,
            g / 255.0,
            b / 255.0,
            min(1.0, delta / 255.0),
            elapsed_since_change / 5.0,
            elapsed_since_sample,
            bright_flag,
            green_dominant_flag,
        ]

        state_info = {
            "r": r,
            "g": g,
            "b": b,
            "delta": delta,
            "green_flag": bool(green_dominant_flag),
            "bright_flag": bool(bright_flag),
        }

        self.last_rgb = (r, g, b)
        self.last_sample_time = now
        return features, state_info


class AppGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Fishing Mini-Game E Prediction Trainer")
        self.root.geometry("820x680")

        self.logger = Logger()
        self.storage = ModelStorage()
        self.stats = TrainingStats()

        self.running = False
        self.mode = "idle"
        self.worker_thread: threading.Thread | None = None

        self.sample_interval_var = tk.DoubleVar(value=0.10)
        self.response_window_var = tk.DoubleVar(value=0.20)
        self.threshold_var = tk.DoubleVar(value=0.50)
        self.learning_rate_var = tk.DoubleVar(value=0.08)
        self.pixel_x_var = tk.IntVar(value=960)
        self.pixel_y_var = tk.IntVar(value=540)

        self.status_var = tk.StringVar(value="Idle")
        self.prediction_var = tk.StringVar(value="Prediction: waiting")
        self.confidence_var = tk.StringVar(value="Confidence: 0.00")

        self.total_var = tk.StringVar(value="Total samples: 0")
        self.correct_var = tk.StringVar(value="Correct predictions: 0")
        self.incorrect_var = tk.StringVar(value="Incorrect predictions: 0")
        self.reinforce_var = tk.StringVar(value="Reinforcement count: 0")
        self.penalty_var = tk.StringVar(value="Penalty count: 0")

        self.sampler = GameStateSampler(self.pixel_x_var.get(), self.pixel_y_var.get())
        self.last_hold_ratio = 0.0
        self.learner = OnlineLearner(num_features=9, learning_rate=self.learning_rate_var.get())

        self._build_layout()
        self._load_existing_state()
        self._pump_log_queue()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_layout(self) -> None:
        config = ttk.LabelFrame(self.root, text="Configuration")
        config.pack(fill="x", padx=10, pady=8)

        pad = {"padx": 6, "pady": 4}
        ttk.Label(config, text="Pixel X").grid(row=0, column=0, **pad)
        ttk.Entry(config, textvariable=self.pixel_x_var, width=8).grid(row=0, column=1, **pad)
        ttk.Label(config, text="Pixel Y").grid(row=0, column=2, **pad)
        ttk.Entry(config, textvariable=self.pixel_y_var, width=8).grid(row=0, column=3, **pad)

        ttk.Label(config, text="Sample interval (s)").grid(row=1, column=0, **pad)
        ttk.Entry(config, textvariable=self.sample_interval_var, width=8).grid(row=1, column=1, **pad)
        ttk.Label(config, text="Response window (s)").grid(row=1, column=2, **pad)
        ttk.Entry(config, textvariable=self.response_window_var, width=8).grid(row=1, column=3, **pad)

        ttk.Label(config, text="Decision threshold").grid(row=2, column=0, **pad)
        ttk.Entry(config, textvariable=self.threshold_var, width=8).grid(row=2, column=1, **pad)
        ttk.Label(config, text="Learning rate").grid(row=2, column=2, **pad)
        ttk.Entry(config, textvariable=self.learning_rate_var, width=8).grid(row=2, column=3, **pad)

        controls = ttk.Frame(self.root)
        controls.pack(fill="x", padx=10, pady=5)
        ttk.Button(controls, text="Start Training", command=self.start_training).pack(side="left", padx=5)
        ttk.Button(controls, text="Use Model", command=self.start_use_model).pack(side="left", padx=5)
        ttk.Button(controls, text="Stop", command=self.stop_training).pack(side="left", padx=5)
        ttk.Button(controls, text="Save Model Now", command=self.save_model).pack(side="left", padx=5)
        ttk.Button(controls, text="Reset Model", command=self.reset_model).pack(side="left", padx=5)

        live = ttk.LabelFrame(self.root, text="Live Prediction")
        live.pack(fill="x", padx=10, pady=6)
        ttk.Label(live, textvariable=self.status_var).pack(anchor="w", padx=8, pady=2)
        ttk.Label(live, textvariable=self.prediction_var, font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=8, pady=2)
        ttk.Label(live, textvariable=self.confidence_var).pack(anchor="w", padx=8, pady=2)

        stats = ttk.LabelFrame(self.root, text="Training Counters")
        stats.pack(fill="x", padx=10, pady=6)
        ttk.Label(stats, textvariable=self.total_var).pack(anchor="w", padx=8)
        ttk.Label(stats, textvariable=self.correct_var).pack(anchor="w", padx=8)
        ttk.Label(stats, textvariable=self.incorrect_var).pack(anchor="w", padx=8)
        ttk.Label(stats, textvariable=self.reinforce_var).pack(anchor="w", padx=8)
        ttk.Label(stats, textvariable=self.penalty_var).pack(anchor="w", padx=8)

        logs = ttk.LabelFrame(self.root, text="Live Log")
        logs.pack(fill="both", expand=True, padx=10, pady=8)
        self.log_text = scrolledtext.ScrolledText(logs, height=16, wrap=tk.WORD, state=tk.DISABLED)
        self.log_text.pack(fill="both", expand=True, padx=6, pady=6)

        note = (
            "Start Training: learns from your E key behavior.\n"
            "Use Model: waits 2 seconds so you can switch screens, then presses E automatically when predicted."
        )
        ttk.Label(self.root, text=note, justify="left").pack(fill="x", padx=10, pady=4)

    def _append_log_to_gui(self, line: str) -> None:
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, line + "\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _pump_log_queue(self) -> None:
        try:
            while True:
                line = self.logger.queue.get_nowait()
                self._append_log_to_gui(line)
        except Empty:
            pass
        self.root.after(120, self._pump_log_queue)

    def _refresh_stats_labels(self) -> None:
        self.total_var.set(f"Total samples: {self.stats.total_samples}")
        self.correct_var.set(f"Correct predictions: {self.stats.correct_predictions}")
        self.incorrect_var.set(f"Incorrect predictions: {self.stats.incorrect_predictions}")
        self.reinforce_var.set(f"Reinforcement count: {self.stats.reinforcement_count}")
        self.penalty_var.set(f"Penalty count: {self.stats.penalty_count}")

    def _load_existing_state(self) -> None:
        state = self.storage.load()
        model_loaded = self.learner.load_dict(state.get("model", {}))

        stats_blob = state.get("stats", {})
        self.stats = TrainingStats(
            total_samples=int(stats_blob.get("total_samples", 0)),
            correct_predictions=int(stats_blob.get("correct_predictions", 0)),
            incorrect_predictions=int(stats_blob.get("incorrect_predictions", 0)),
            reinforcement_count=int(stats_blob.get("reinforcement_count", 0)),
            penalty_count=int(stats_blob.get("penalty_count", 0)),
            total_hold_seconds=float(stats_blob.get("total_hold_seconds", 0.0)),
        )
        self._refresh_stats_labels()

        if model_loaded:
            self.logger.log("Loaded existing model from disk.")
        else:
            self.logger.log("No prior model found; initialized new model with default values.")

    def _validate_settings(self) -> None:
        interval = float(self.sample_interval_var.get())
        response_window = float(self.response_window_var.get())
        lr = float(self.learning_rate_var.get())
        threshold = float(self.threshold_var.get())

        if interval <= 0.01:
            raise ValueError("Sample interval must be > 0.01 seconds.")
        if response_window <= 0.0:
            raise ValueError("Response window must be positive.")
        if not 0.0 < lr <= 1.0:
            raise ValueError("Learning rate must be in (0, 1].")
        if not 0.0 < threshold < 1.0:
            raise ValueError("Decision threshold must be in (0, 1).")

    def start_training(self) -> None:
        if self.running:
            return

        try:
            self._validate_settings()
        except Exception as exc:
            messagebox.showerror("Invalid settings", str(exc))
            return

        self.sampler.set_point(self.pixel_x_var.get(), self.pixel_y_var.get())
        self.learner.learning_rate = float(self.learning_rate_var.get())

        self.running = True
        self.mode = "training"
        self.status_var.set("Training running")
        self.logger.log("Training started.")

        self.worker_thread = threading.Thread(target=self._training_loop, daemon=True)
        self.worker_thread.start()

    def start_use_model(self) -> None:
        if self.running:
            return

        try:
            self._validate_settings()
        except Exception as exc:
            messagebox.showerror("Invalid settings", str(exc))
            return

        self.sampler.set_point(self.pixel_x_var.get(), self.pixel_y_var.get())
        self.running = True
        self.mode = "inference"
        self.status_var.set("Use model running")
        self.logger.log("Use model selected. Waiting 2 seconds so you can switch screens...")

        self.worker_thread = threading.Thread(target=self._inference_loop, daemon=True)
        self.worker_thread.start()

    def stop_training(self) -> None:
        if not self.running:
            return
        self.running = False
        self.mode = "idle"
        self.status_var.set("Stopped")
        self.save_model()
        self.logger.log("Worker stopped.")

    def save_model(self) -> None:
        self.storage.save(self.learner.to_dict(), self.stats)
        self.logger.log("Saved updated model.")

    def reset_model(self) -> None:
        if self.running:
            messagebox.showwarning("Busy", "Stop training before resetting model.")
            return

        self.last_hold_ratio = 0.0
        self.learner = OnlineLearner(num_features=9, learning_rate=float(self.learning_rate_var.get()))
        self.stats = TrainingStats()
        self._refresh_stats_labels()
        self.save_model()
        self.prediction_var.set("Prediction: waiting")
        self.confidence_var.set("Confidence: 0.00")
        self.logger.log("Model and training statistics were reset.")

    def _observe_player_response(self, response_window: float) -> tuple[int, float]:
        """
        Observe whether player presses E during a short response window and for how long.
        Returns (pressed_flag, held_seconds).
        """
        start = time.time()
        pressed = 0
        held_seconds = 0.0
        press_started_at: float | None = None

        while time.time() - start < response_window and self.running:
            is_down = keyboard.is_pressed("e")
            if is_down:
                pressed = 1
                if press_started_at is None:
                    press_started_at = time.time()
            elif press_started_at is not None:
                held_seconds += time.time() - press_started_at
                press_started_at = None
            time.sleep(0.01)

        if press_started_at is not None:
            held_seconds += time.time() - press_started_at

        held_seconds = min(response_window, held_seconds)
        return pressed, held_seconds

    def _training_loop(self) -> None:
        try:
            with mss.mss() as sct:
                while self.running:
                    features, state_info = self.sampler.sample(sct)
                    features.append(self.last_hold_ratio)

                    threshold = float(self.threshold_var.get())
                    prediction, prob_press = self.learner.predict(features, threshold)
                    confidence = max(prob_press, 1.0 - prob_press)
                    predicted_text = "PRESS E NOW" if prediction == 1 else "DO NOT PRESS E"

                    response_window = float(self.response_window_var.get())
                    player_pressed, hold_seconds = self._observe_player_response(response_window)
                    hold_ratio = hold_seconds / response_window if response_window > 0 else 0.0

                    matched, reinforcement_label = self.learner.update(
                        features,
                        player_pressed,
                        prediction,
                        hold_ratio=hold_ratio,
                    )
                    self.last_hold_ratio = hold_ratio

                    self.stats.total_samples += 1
                    self.stats.total_hold_seconds += hold_seconds
                    if matched:
                        self.stats.correct_predictions += 1
                        self.stats.reinforcement_count += 1
                    else:
                        self.stats.incorrect_predictions += 1
                        self.stats.penalty_count += 1

                    player_text = "pressed E" if player_pressed else "did not press E"
                    correctness_text = "matched" if matched else "failed"

                    state_name = (
                        "green circle-like state"
                        if state_info["green_flag"]
                        else "non-green state"
                    )
                    self.logger.log(
                        f"Detected {state_name}, rgb=({state_info['r']},{state_info['g']},{state_info['b']}), "
                        f"delta={state_info['delta']}, confidence {confidence:.2f}, predicted {predicted_text}."
                    )
                    self.logger.log(
                        f"Player {player_text}, prediction {correctness_text}, applying {reinforcement_label}."
                    )
                    self.logger.log(
                        f"E hold duration {hold_seconds:.3f}s in {response_window:.3f}s window "
                        f"(ratio={hold_ratio:.2f}) used in learning."
                    )

                    self.root.after(
                        0,
                        lambda p=predicted_text, c=confidence: self.prediction_var.set(f"Prediction: {p}"),
                    )
                    self.root.after(
                        0,
                        lambda c=confidence: self.confidence_var.set(f"Confidence: {c:.2f}"),
                    )
                    self.root.after(0, self._refresh_stats_labels)

                    if self.stats.total_samples % 40 == 0:
                        self.save_model()

                    time.sleep(float(self.sample_interval_var.get()))
        except Exception as exc:
            self.running = False
            self.root.after(0, lambda: self.status_var.set(f"Error: {exc}"))
            self.logger.log(f"Training loop error: {exc}")
        finally:
            self.save_model()

    def _inference_loop(self) -> None:
        try:
            time.sleep(2.0)
            if not self.running:
                return

            self.logger.log("Use model started: making automatic E decisions now.")

            with mss.mss() as sct:
                while self.running:
                    features, state_info = self.sampler.sample(sct)
                    features.append(self.last_hold_ratio)
                    threshold = float(self.threshold_var.get())
                    prediction, prob_press = self.learner.predict(features, threshold)
                    confidence = max(prob_press, 1.0 - prob_press)

                    if prediction == 1:
                        keyboard.press_and_release("e")
                        action_text = "pressed E"
                    else:
                        action_text = "did not press E"

                    predicted_text = "PRESS E NOW" if prediction == 1 else "DO NOT PRESS E"
                    self.logger.log(
                        f"Use model detected rgb=({state_info['r']},{state_info['g']},{state_info['b']}), "
                        f"delta={state_info['delta']}, confidence {confidence:.2f}, prediction {predicted_text}; {action_text}."
                    )

                    self.root.after(
                        0,
                        lambda p=predicted_text: self.prediction_var.set(f"Prediction: {p}"),
                    )
                    self.root.after(
                        0,
                        lambda c=confidence: self.confidence_var.set(f"Confidence: {c:.2f}"),
                    )

                    time.sleep(float(self.sample_interval_var.get()))
        except Exception as exc:
            self.running = False
            self.mode = "idle"
            self.root.after(0, lambda: self.status_var.set(f"Error: {exc}"))
            self.logger.log(f"Use model loop error: {exc}")
        finally:
            self.mode = "idle"

    def on_close(self) -> None:
        self.running = False
        self.save_model()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = AppGUI(root)
    root.mainloop()
