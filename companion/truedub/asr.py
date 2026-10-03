"""Whisper speech-to-English: mlx-whisper on Apple silicon, faster-whisper elsewhere.

The model loads on first use, or on load(). Only the pipeline worker thread calls into this
module, except loaded().
"""

import platform
import sys

import numpy as np

MLX = sys.platform == "darwin" and platform.machine() == "arm64"
NAME = "mlx-whisper" if MLX else "faster-whisper"
# Multilingual models that can translate. Never turbo (not trained for translation) or *.en.
MODELS = ("tiny", "base", "small", "medium", "large-v2", "large-v3")

if MLX:
    import mlx.core as mx
    import mlx_whisper
    from mlx_whisper.audio import N_FRAMES, N_SAMPLES, log_mel_spectrogram, pad_or_trim
    from mlx_whisper.tokenizer import LANGUAGES
    from mlx_whisper.transcribe import ModelHolder

    DEVICE = "mlx"
    model = "large-v3"

    def _repo() -> str:
        return f"mlx-community/whisper-{model}-mlx"

    def load():
        return ModelHolder.get_model(_repo(), mx.float16)

    def loaded() -> bool:
        return ModelHolder.model_path == _repo()

    def language_probs(audio: np.ndarray) -> dict[str, float]:
        """Language probabilities for the first 30 s of 16 kHz mono audio."""
        whisper = load()
        mel = log_mel_spectrogram(audio, n_mels=whisper.dims.n_mels, padding=N_SAMPLES)
        _, probs = whisper.detect_language(pad_or_trim(mel, N_FRAMES, axis=-2).astype(mx.float16))
        return probs

    def translate(audio: np.ndarray, language: str, prompt: str) -> list[dict]:
        result = mlx_whisper.transcribe(
            audio,
            path_or_hf_repo=_repo(),
            task="translate",
            language=language,
            initial_prompt=prompt,
        )
        return result["segments"]

else:
    import ctranslate2
    import psutil
    from faster_whisper import WhisperModel
    from faster_whisper.tokenizer import _LANGUAGE_CODES

    LANGUAGES = _LANGUAGE_CODES
    DEVICE = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
    # large-v3 is far slower than realtime on a laptop CPU; medium keeps up better.
    model = "large-v3" if DEVICE == "cuda" else "medium"
    _whisper = None

    def loaded() -> bool:
        return _whisper is not None

    def load() -> WhisperModel:
        global _whisper
        if _whisper is None:
            if DEVICE == "cuda":
                _whisper = WhisperModel(model, device="cuda", compute_type="float16")
            else:
                threads = psutil.cpu_count(logical=False) or 0
                _whisper = WhisperModel(
                    model, device="cpu", compute_type="int8", cpu_threads=threads
                )
        return _whisper

    def language_probs(audio: np.ndarray) -> dict[str, float]:
        """Language probabilities for the first 30 s of 16 kHz mono audio."""
        _, _, probs = load().detect_language(audio)
        return dict(probs)

    def translate(audio: np.ndarray, language: str, prompt: str) -> list[dict]:
        segments, _ = load().transcribe(
            audio,
            task="translate",
            language=language,
            initial_prompt=prompt,
        )
        return [
            {
                "start": s.start,
                "end": s.end,
                "text": s.text,
                "avg_logprob": s.avg_logprob,
                "no_speech_prob": s.no_speech_prob,
                "compression_ratio": s.compression_ratio,
            }
            for s in segments
        ]
