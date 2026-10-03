"""Kokoro text-to-speech, fitted into a time slot."""

import urllib.request
from pathlib import Path

import numpy as np

SR = 24000
MODELS = Path.home() / ".cache" / "truedub" / "models"
MODEL_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
FILES = ("kokoro-v1.0.onnx", "voices-v1.0.bin")
# Kokoro's default pace is slower than most YouTubers talk; a steady, slightly brisker pace
# sounds more natural than speeding up single lines to keep up.
PACE = 1.1

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
    audio, _ = _kokoro.create(text, voice=voice, speed=speed * PACE)
    return audio


def trim_silence(audio: np.ndarray, threshold: float = 0.01) -> np.ndarray:
    loud = np.flatnonzero(np.abs(audio) > threshold)
    if loud.size == 0:
        return audio[:0]
    return audio[loud[0] : loud[-1] + 1]


def speak(text: str, voice: str, speed: float = 1.0) -> np.ndarray | None:
    """Speech for `text`, silence trimmed, or None if nothing is speakable."""
    try:
        audio = synthesize(text, voice, speed)
    except ValueError:  # text with no speakable phonemes
        return None
    audio = trim_silence(audio)
    return audio if audio.size else None


def fit_clip(audio: np.ndarray, slot: float, fade: float = 0.05) -> np.ndarray:
    """Cut a clip that overruns `slot`: at its last pause in the second half of the slot, so no
    word is cut off, else with a short fade-out."""
    limit = max(int(slot * SR), 0)
    if len(audio) <= limit:
        return audio
    frame = SR // 50
    n = limit // frame
    rms = np.sqrt(np.mean(audio[: n * frame].reshape(n, frame) ** 2, axis=1))
    quiet = np.flatnonzero(rms[n // 2 :] < 0.01)
    if quiet.size:
        return trim_silence(audio[: (n // 2 + quiet[-1]) * frame])
    audio = audio[:limit].copy()
    k = min(int(fade * SR), limit)
    audio[limit - k :] *= np.linspace(1.0, 0.0, k, dtype=audio.dtype)
    return audio
