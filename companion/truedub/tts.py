"""Kokoro text-to-speech, fitted into a time slot."""

import urllib.request
from pathlib import Path

import numpy as np

SR = 24000
MAX_SPEED = 1.6
MODELS = Path.home() / ".cache" / "truedub" / "models"
MODEL_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
FILES = ("kokoro-v1.0.onnx", "voices-v1.0.bin")

_kokoro = None


def download_models() -> None:
    MODELS.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        path = MODELS / name
        if not path.exists():
            print(f"Downloading {name} ...", flush=True)
            part = path.with_suffix(".part")
            urllib.request.urlretrieve(MODEL_URL + name, part)
            part.rename(path)


def voices() -> list[str]:
    """English voices: American (a*) and British (b*)."""
    with np.load(MODELS / "voices-v1.0.bin") as data:
        return sorted(v for v in data.files if v[0] in "ab")


def synthesize(text: str, voice: str, speed: float = 1.0) -> np.ndarray:
    global _kokoro
    if _kokoro is None:
        from kokoro_onnx import Kokoro

        _kokoro = Kokoro(str(MODELS / FILES[0]), str(MODELS / FILES[1]))
    audio, _ = _kokoro.create(text, voice=voice, speed=speed)
    return audio


def next_speed(speed: float, duration: float, slot: float) -> float:
    """Kokoro speed that should make a clip of `duration` (made at `speed`) fit `slot`."""
    if duration <= slot:
        return speed
    return min(speed * duration / slot * 1.05, MAX_SPEED)


def trim_silence(audio: np.ndarray, threshold: float = 0.01) -> np.ndarray:
    loud = np.flatnonzero(np.abs(audio) > threshold)
    if loud.size == 0:
        return audio[:0]
    return audio[loud[0] : loud[-1] + 1]


def fit_clip(audio: np.ndarray, slot: float, fade: float = 0.05) -> np.ndarray:
    """Trim silence, then truncate with a short fade-out if the clip still overruns `slot`."""
    audio = trim_silence(audio)
    limit = max(int(slot * SR), 0)
    if len(audio) <= limit:
        return audio
    audio = audio[:limit].copy()
    n = min(int(fade * SR), limit)
    audio[limit - n :] *= np.linspace(1.0, 0.0, n, dtype=audio.dtype)
    return audio


def dub(text: str, voice: str, slot: float) -> np.ndarray | None:
    """Speech for `text` that ends within `slot` seconds, or None if nothing is speakable."""
    speed = 1.0
    try:
        audio = synthesize(text, voice)
        # Speed does not scale duration linearly, so retry until it fits or hits the cap.
        for _ in range(3):
            faster = next_speed(speed, len(trim_silence(audio)) / SR, slot)
            if faster == speed:
                break
            speed = faster
            audio = synthesize(text, voice, speed)
    except ValueError:  # text with no speakable phonemes
        return None
    audio = fit_clip(audio, slot)
    return audio if audio.size else None
