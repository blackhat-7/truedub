"""HTTP API for the TrueDub extension. The contract is docs/API.md."""

import argparse
import logging
import os
import re
import sys

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field, model_validator

from . import __version__, asr, pipeline, tts

app = FastAPI(title="TrueDub companion", version=__version__)


class Cookie(BaseModel):
    name: str
    value: str
    domain: str
    path: str = "/"
    secure: bool = False
    expires: float = 0  # Unix time; 0 for a session cookie


class Caption(BaseModel):
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(ge=0, allow_inf_nan=False)
    text: str = Field(max_length=1000)


class Transcript(BaseModel):
    language: str = Field("", max_length=32)  # informational
    duration: float = Field(gt=0, le=24 * 3600, allow_inf_nan=False)
    segments: list[Caption] = Field(min_length=1, max_length=50_000)

    @model_validator(mode="after")
    def check(self):
        if any(c.end < c.start for c in self.segments):
            raise ValueError("a segment ends before it starts")
        if not any(c.text.strip() for c in self.segments):
            raise ValueError("the transcript has no text")
        return self


class DubRequest(BaseModel):
    video_id: str
    source_lang: str = "auto"
    model: str = "auto"
    voice: str = "am_michael"
    transcript: Transcript | None = None
    cookies: list[Cookie] = []


@app.exception_handler(RequestValidationError)
async def invalid_request(request, exc: RequestValidationError):
    detail = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:3])
    return JSONResponse({"detail": detail}, status_code=400)


@app.get("/health")
def health():
    return {
        "ok": True,
        "version": __version__,
        "asr": f"{asr.NAME} {asr.default_model} ({asr.DEVICE})",
        "models": asr.OFFERED,
        "default_model": asr.default_model,
        "voices": tts.voices(),
    }


@app.post("/dub")
def dub(request: DubRequest):
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", request.video_id):
        raise HTTPException(400, f"Invalid video_id {request.video_id!r}")
    if request.voice not in tts.voices():
        raise HTTPException(400, f"Unknown voice {request.voice!r}")
    if request.transcript:
        job = pipeline.Job(request.video_id, request.voice, transcript=True)
        return {"job": pipeline.submit(job, request.transcript.model_dump()).id}
    if request.source_lang != "auto" and request.source_lang not in asr.LANGUAGES:
        raise HTTPException(400, f"Unknown source_lang {request.source_lang!r}")
    model = asr.default_model if request.model == "auto" else request.model
    if model not in asr.MODELS:
        raise HTTPException(400, f"Unknown model {request.model!r}")
    cookies = [c.model_dump() for c in request.cookies]
    job = pipeline.Job(request.video_id, request.voice, request.source_lang, model, cookies=cookies)
    return {"job": pipeline.submit(job).id}


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
        default=os.environ.get("TRUEDUB_MODEL", asr.default_model),
        help=f"Whisper model (env TRUEDUB_MODEL; default for this machine: {asr.default_model})",
    )
    model = parser.parse_args().model
    if model not in asr.MODELS:  # argparse does not check defaults taken from the env
        parser.error(f"TRUEDUB_MODEL must be one of {', '.join(asr.MODELS)}")
    asr.default_model = model
    if sys.stderr is None:  # pythonw (Windows autostart) has no console: log to a file
        pipeline.CACHE.mkdir(parents=True, exist_ok=True)
        log = open(pipeline.CACHE / "truedub.log", "a", buffering=1, encoding="utf-8")  # noqa: SIM115
        sys.stdout = sys.stderr = log
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    # Noisy word-count warnings. phonemizer resets its logger's level on every call, so
    # stop them reaching the root handler instead.
    logging.getLogger("phonemizer").propagate = False
    tts.download_models()
    pipeline.start_worker()
    uvicorn.run(app, host="127.0.0.1", port=7861)
