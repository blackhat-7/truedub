"""HTTP API for the TrueDub extension. The contract is docs/API.md."""

import argparse
import logging
import os
import re
import sys

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import __version__, asr, pipeline, tts

app = FastAPI(title="TrueDub companion", version=__version__)


class DubRequest(BaseModel):
    video_id: str
    source_lang: str = "auto"
    voice: str = "am_michael"


@app.get("/health")
def health():
    return {
        "ok": True,
        "version": __version__,
        "asr": f"{asr.NAME} {asr.model} ({asr.DEVICE})",
        "voices": tts.voices(),
    }


@app.post("/dub")
def dub(request: DubRequest):
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", request.video_id):
        raise HTTPException(400, f"Invalid video_id {request.video_id!r}")
    if request.source_lang != "auto" and request.source_lang not in asr.LANGUAGES:
        raise HTTPException(400, f"Unknown source_lang {request.source_lang!r}")
    if request.voice not in tts.voices():
        raise HTTPException(400, f"Unknown voice {request.voice!r}")
    job = pipeline.submit(request.video_id, request.source_lang, request.voice)
    return {"job": job.id}


@app.get("/dub/{job_id}")
def status(job_id: str, at: float = 0.0):
    state = pipeline.poll(job_id, at)
    if state is None:
        raise HTTPException(404, "Unknown job")
    return state


@app.get("/dub/{job_id}/audio/{seg_id}")
def audio(job_id: str, seg_id: int):
    path = pipeline.clip_path(job_id, seg_id)
    if path is None:
        raise HTTPException(404, "Unknown segment")
    return FileResponse(path, media_type="audio/wav")


def main() -> None:
    parser = argparse.ArgumentParser(description="TrueDub companion server")
    parser.add_argument(
        "--model",
        choices=asr.MODELS,
        default=os.environ.get("TRUEDUB_MODEL", asr.model),
        help=f"Whisper model (env TRUEDUB_MODEL; default for this machine: {asr.model})",
    )
    model = parser.parse_args().model
    if model not in asr.MODELS:  # argparse does not check defaults taken from the env
        parser.error(f"TRUEDUB_MODEL must be one of {', '.join(asr.MODELS)}")
    asr.model = model
    if sys.stderr is None:  # pythonw (Windows autostart) has no console: log to a file
        pipeline.CACHE.mkdir(parents=True, exist_ok=True)
        log = open(pipeline.CACHE / "truedub.log", "a", buffering=1, encoding="utf-8")  # noqa: SIM115
        sys.stdout = sys.stderr = log
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.getLogger("phonemizer").setLevel(logging.ERROR)  # noisy word-count warnings
    tts.download_models()
    pipeline.start_worker()
    uvicorn.run(app, host="127.0.0.1", port=7861)
