"""Dubbing jobs: download, chunking, ASR cleanup, TTS, scheduling and the on-disk cache.

Work happens in chunks of the timeline cut at pauses. The first chunk after a seek is short
so audio starts quickly; chunks that continue the previous one are longer.

A transcript job voices YouTube's English captions instead: no download and no Whisper.
Its chunks are cut at sentence starts.

Cache layout under ~/.cache/truedub/<video_id>/:
    audio.wav                                   16 kHz mono source audio
    context.txt                                 title and description line, prompted to Whisper
    language.txt                                language detected for source_lang "auto"
    <model>/<lang>/asr/<start>_<end>.json       cleaned English segments, shared by voices
    <model>/<lang>/<voice>-v2/<start>_<end>.json  finished segments of a chunk
    <model>/<lang>/<voice>-v2/<id>.wav          dubbed clips
    youtube/transcript.json                     sentences built from the first transcript sent
    youtube/<voice>-v2/...                      as above, for the transcript
"""

import io
import json
import logging
import math
import re
import textwrap
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import av
import deno
import numpy as np
import soundfile as sf
import yt_dlp

from . import asr, tts

log = logging.getLogger("truedub")

CACHE = Path.home() / ".cache" / "truedub"
SR = 16000
FPS = 50  # energy frames per second
TARGET = "en"
SHORT = 15.0  # first chunk after a seek, for a fast start
LONG = 45.0  # chunks continuing the previous one, for context and throughput
TEXT_LONG = 30.0  # LONG for a transcript, which needs no context
CLIPS = "v4"  # version of the dubbed clips: bump to remake cached ones
# Clips play one after another and may start late, like real dubbing, instead of being
# rushed into their segment's slot.
GAP = 0.15  # seconds between clips
MAX_LAG = 3.0  # how late a clip may start
MILD = 1.15  # speed-up used freely to stay on time
MAX_SPEED = 1.35  # speed-up allowed only to keep within MAX_LAG; still better than cutting words
# Whisper copies the prompt's style: a punctuated prompt gives punctuated sentences,
# which Kokoro voices with natural pauses.
PROMPT = "Okay, let's continue."
IDLE = 120.0  # seconds without a poll before a job's work pauses (the tab was closed)

Range = tuple[float, float]

# ---------------------------------------------------------------- pure logic


def frame_db(audio: np.ndarray) -> np.ndarray:
    """Loudness in dBFS per 20 ms frame (the last one zero-padded), smoothed over 300 ms."""
    size = SR // FPS
    n = math.ceil(len(audio) / size)
    frames = np.zeros(n * size, dtype=np.float32)
    frames[: len(audio)] = audio
    rms = np.sqrt(np.mean(frames.reshape(n, size) ** 2, axis=1))
    smooth = np.convolve(rms, np.ones(15) / 15, mode="same")
    return 20 * np.log10(smooth + 1e-10)


def quietest(db: np.ndarray, lo: float, hi: float) -> float:
    """Time of the quietest frame in [lo, hi)."""
    a, b = int(lo * FPS), max(int(hi * FPS), int(lo * FPS) + 1)
    return round((a + int(np.argmin(db[a:b]))) / FPS, 2)


def merge_ranges(ranges) -> list[Range]:
    out: list[list[float]] = []
    for a, b in sorted(ranges):
        if out and a <= out[-1][1] + 1e-6:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def holes(done: list[Range], duration: float) -> list[Range]:
    """Unprocessed parts of the timeline, given merged processed ranges."""
    out, t = [], 0.0
    for a, b in done:
        if a > t + 1e-6:
            out.append((t, a))
        t = max(t, b)
    if duration > t + 1e-6:
        out.append((t, duration))
    return out


def next_chunk(
    db: np.ndarray, done: list[Range], at: float, prev_end: float, known: dict[float, float]
) -> Range | None:
    """The next chunk to process: at or after the playhead first, then earlier holes.

    `known` maps start -> end of chunks whose ASR is cached; those are reused when they fit.
    """
    gaps = holes(done, round(len(db) / FPS, 2))
    if not gaps:
        return None
    ahead = [g for g in gaps if g[1] > at]
    start, end = ahead[0] if ahead else gaps[0]
    if ahead and start < at - 2:  # playhead inside a hole: start at a pause just before it
        start = quietest(db, at - 2, at)
    if known.get(start, math.inf) <= end:
        return start, known[start]
    size = LONG if abs(start - prev_end) < 1e-6 else SHORT
    if end - start <= size:
        return start, end
    return start, quietest(db, start + size * 2 / 3, start + size)


def next_text_chunk(
    starts: list[float], duration: float, done: list[Range], at: float, prev_end: float
) -> Range | None:
    """next_chunk for a transcript: chunks cut at sentence `starts`, so none is split."""
    gaps = holes(done, duration)
    if not gaps:
        return None
    ahead = [g for g in gaps if g[1] > at]
    start, end = ahead[0] if ahead else gaps[0]
    cuts = [t for t in starts if start < t < end]
    if ahead and any(t <= at for t in cuts):  # playhead inside a hole: start at its sentence
        start = max(t for t in cuts if t <= at)
    size = TEXT_LONG if abs(start - prev_end) < 1e-6 else SHORT
    later = [t for t in cuts if t > start]
    if end - start <= size or not later:
        return start, end
    return start, max([t for t in later if t <= start + size], default=later[0])


def place(
    start: float, free: float, length: float, next_start: float | None, end: float
) -> tuple[float, float, float]:
    """Start, speed-up and latest end for a clip `length` seconds long of a segment at `start`.

    The clip starts at `start`, or at `free` (GAP after the previous clip) if later. It speeds
    up to MILD to end in time for the next segment, and up to MAX_SPEED so the next clip starts
    at most MAX_LAG late, and the last clip ends by `end`.
    """
    start = round(max(start, free), 2)
    if next_start is None:
        on_time = latest = end
    else:
        on_time = next_start - GAP
        latest = min(next_start + MAX_LAG, end) - GAP
    speed = max(
        1.0,
        min(MILD, length / max(on_time - start, 1e-3)),
        length / max(latest - start, 1e-3),
    )
    return start, round(min(speed, MAX_SPEED), 3), latest


def pick_language(probs: list[dict[str, float]]) -> str:
    """Most likely spoken language across samples, never the target language."""
    total: dict[str, float] = {}
    for p in probs:
        for lang, prob in p.items():
            total[lang] = total.get(lang, 0.0) + prob
    total.pop(TARGET, None)
    return max(total, key=total.get)


JUNK = re.compile(
    r"(thanks?( you)?( so much| very much)? for watching( this video)?"
    r"|(please )?(like (and|&) )?subscribe( to (my|our|the) channel)?"
    r"|(subtitles|captions|transcription|translated|subtitled) by .*|.*amara\.org.*|you)"
)


def is_junk(seg: dict, peak_db: float) -> bool:
    """Whisper hallucinations: unconfident, repetitive, silent, boilerplate or not English."""
    # no_speech_prob is not used: real speech often scores 0.7-0.9 when translating.
    if seg["compression_ratio"] > 2.4 or seg["avg_logprob"] < -1.0:
        return True
    if peak_db < -45:
        return True
    text = seg["text"].strip()
    letters = [c for c in text if c.isalpha()]
    if sum(c.isascii() for c in letters) < max(1, len(letters) / 2):
        return True
    return bool(JUNK.fullmatch(re.sub(r"[^\w&.' ]+|\.$", "", text.lower()).strip(" .")))


def _short(seg: dict) -> bool:
    return seg["end"] - seg["start"] < 1.5 or len(seg["text"].split()) < 4


def merge_segments(segs: list[dict], max_gap: float = 0.6, max_s: float = 12) -> list[dict]:
    """Join fragments into sentence-sized segments."""
    out: list[dict] = []
    for s in segs:
        if out:
            p = out[-1]
            unfinished = not p["text"].endswith((".", "!", "?"))
            if (
                s["start"] - p["end"] <= max_gap
                and s["end"] - p["start"] <= max_s
                and (unfinished or _short(p) or _short(s))
            ):
                p["end"] = s["end"]
                p["text"] = f"{p['text']} {s['text']}"
                continue
        out.append(dict(s))
    return out


def split_long(seg: dict, max_s: float = 12) -> list[dict]:
    """Split a long segment at sentence ends, timing the parts by text length."""
    duration = seg["end"] - seg["start"]
    sentences = re.split(r"(?<=[.!?])\s+", seg["text"])
    if duration <= max_s or len(sentences) < 2:
        return [seg]
    target = sum(map(len, sentences)) / math.ceil(duration / max_s)
    groups, current = [], []
    for sentence in sentences:
        current.append(sentence)
        if sum(map(len, current)) >= target:
            groups.append(" ".join(current))
            current = []
    if current:
        groups.append(" ".join(current))
    total = sum(map(len, groups))
    out, start, done = [], seg["start"], 0
    for text in groups:
        done += len(text)
        end = round(seg["start"] + duration * done / total, 2)
        out.append({"start": start, "end": end, "text": text})
        start = end
    return out


SENTENCE_END = re.compile(r"[.!?][\"'”’)]*$")
NOT_SPOKEN = re.compile(r"\[[^\]]*\]|[♪♫\u200b]")  # tags like [Music], notes, zero-width spaces


def build_sentences(
    captions: list[dict],
    duration: float,
    max_gap: float = 0.7,
    max_s: float = 12,
    max_chars: int = 200,
) -> list[dict]:
    """Sentences from YouTube caption events, which are overlapping fragments of sentences.

    A fragment ends where the next one starts, if sooner, and its time is shared among its
    words by length. A sentence ends at sentence punctuation or a pause over `max_gap`, or
    before it would pass `max_s` or `max_chars`, after a comma in its second half if any.
    Then tiny sentences ("Ok?") join a neighbour, as Whisper's do.
    """
    events = sorted(
        (c for c in captions if c["text"].strip() and c["start"] < duration),
        key=lambda c: c["start"],
    )
    words = []  # (start, end, word)
    for i, c in enumerate(events):
        start, end = c["start"], min(c["end"], duration)
        if i + 1 < len(events):
            end = min(end, events[i + 1]["start"])
        text = NOT_SPOKEN.sub("", c["text"]).split()
        if not text:
            continue
        total, done = sum(len(w) + 1 for w in text), 0
        for w in text:
            a = start + (end - start) * done / total
            done += len(w) + 1
            words.append((a, start + (end - start) * done / total, w))

    sentences, current = [], []
    for word in words:
        if current and word[0] - current[-1][1] > max_gap:
            sentences.append(current)
            current = []
        elif current and (
            word[1] - current[0][0] > max_s
            or sum(len(w) + 1 for _, _, w in current) + len(word[2]) > max_chars
        ):
            half = len(current) // 2
            commas = [i for i, w in enumerate(current) if i >= half and w[2].endswith(",")]
            cut = commas[-1] + 1 if commas else len(current)
            sentences.append(current[:cut])
            current = current[cut:]
        current.append(word)
        if SENTENCE_END.search(word[2]):
            sentences.append(current)
            current = []
    if current:
        sentences.append(current)
    return merge_segments(
        [
            {
                "start": round(s[0][0], 2),
                "end": round(s[-1][1], 2),
                "text": re.sub(r" (?=[.,!?])", "", " ".join(w for *_, w in s)),
            }
            for s in sentences
        ]
    )


def video_context(info: dict) -> str:
    """The title and first description line: names and terms for Whisper to spell right."""
    title = info.get("title") or ""
    lines = (info.get("description") or "").strip().splitlines()
    return f"{title}. {textwrap.shorten(lines[0], 150, placeholder='')}" if lines else title


def netscape_cookies(cookies: list[dict]) -> str:
    """Browser cookies as a Netscape cookies.txt, the format yt-dlp reads. Expiry 0: session."""
    lines = ["# Netscape HTTP Cookie File"]
    for c in cookies:
        subdomains = "TRUE" if c["domain"].startswith(".") else "FALSE"
        secure = "TRUE" if c["secure"] else "FALSE"
        expires = str(int(c["expires"]))
        fields = (c["domain"], subdomains, c["path"], secure, expires, c["name"], c["value"])
        lines.append("\t".join(fields))
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- jobs


def _chunk_name(chunk: Range) -> str:
    return f"{chunk[0]:.2f}_{chunk[1]:.2f}"


def _chunk_files(folder: Path) -> dict[Range, Path]:
    out = {}
    for path in folder.glob("*.json"):
        a, b = path.stem.split("_")
        out[(float(a), float(b))] = path
    return out


@dataclass
class Job:
    video_id: str
    voice: str
    source_lang: str = "auto"
    model: str = ""  # Whisper model; unused by a transcript job
    transcript: bool = False  # voices YouTube's English captions instead of Whisper's
    # The browser's YouTube cookies, for a download that hits the bot check. Never written to disk.
    cookies: list[dict] = field(default_factory=list, repr=False)
    status: str = "downloading"
    error: str | None = None
    duration: float | None = None
    db: np.ndarray | None = None  # loudness per frame, see frame_db
    language: str | None = None  # resolved Whisper language
    sentences: list[dict] = field(default_factory=list)  # a transcript job's English text
    chunks: dict[Range, list[dict]] = field(default_factory=dict)  # finished segments
    at: float = 0.0
    prev_end: float = -1.0  # end of the last chunk processed
    polled: float = field(default_factory=time.monotonic)

    @property
    def id(self) -> str:
        if self.transcript:
            return f"{self.video_id}.yt.{self.voice}"
        return f"{self.video_id}.{self.source_lang}.{self.model}.{self.voice}"

    @property
    def dir(self) -> Path:
        return CACHE / self.video_id

    @property
    def asr_dir(self) -> Path:
        return self.dir / self.model / self.language / "asr"

    @property
    def voice_dir(self) -> Path:
        if self.transcript:
            return self.dir / "youtube" / f"{self.voice}-{CLIPS}"
        return self.dir / self.model / self.language / f"{self.voice}-{CLIPS}"

    def processed(self) -> list[Range]:
        return merge_ranges(self.chunks)

    def load_cache(self) -> None:
        for chunk, path in _chunk_files(self.voice_dir).items():
            self.chunks[chunk] = json.loads(path.read_text())
        self.status = "processing" if holes(self.processed(), self.duration) else "done"

    def state(self) -> dict:
        processed = self.processed()
        status = self.status
        if status == "processing" and not self.transcript:
            if not asr.loaded(self.model):
                status = "loading"
            elif self.language is None:
                status = "detecting"
        return {
            "status": status,
            "error": self.error,
            "duration": self.duration,
            "progress": round(sum(b - a for a, b in processed) / self.duration, 4)
            if self.duration
            else 0.0,
            "processed": processed,
            "segments": sorted(
                (s for segs in self.chunks.values() for s in segs), key=lambda s: s["start"]
            ),
        }


jobs: dict[str, Job] = {}
lock = threading.Condition()


def submit(job: Job, transcript: dict | None = None) -> Job:
    """Get or start `job`. A failed job is retried.

    `transcript` ({duration, segments}) is required for a transcript job.
    """
    with lock:
        existing = jobs.get(job.id)
        if existing and existing.status != "error":
            existing.polled = time.monotonic()
            lock.notify()
            return existing
        if job.transcript:  # nothing to download: ready to work now
            _load_transcript(job, transcript)
        jobs[job.id] = job
        lock.notify()
    if not job.transcript:
        threading.Thread(target=_prepare, args=(job,), daemon=True).start()
    return job


def poll(job_id: str, at: float) -> dict | None:
    with lock:
        job = jobs.get(job_id)
        if job is None:
            return None
        job.at, job.polled = at, time.monotonic()
        lock.notify()
        return job.state()


def clip_path(job_id: str, seg_id: int) -> Path | None:
    with lock:
        job = jobs.get(job_id)
        if job is None or not any(s["id"] == seg_id for c in job.chunks.values() for s in c):
            return None
        return job.voice_dir / f"{seg_id}.wav"


def start_worker() -> None:
    threading.Thread(target=_work, daemon=True).start()


# ---------------------------------------------------------------- work


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(path)


def _read(job: Job, start: float, end: float) -> np.ndarray:
    audio, _ = sf.read(
        job.dir / "audio.wav", start=int(start * SR), stop=int(end * SR), dtype="float32"
    )
    return audio


def _message(e: Exception) -> str:
    text = re.sub(r"\x1b\[[0-9;]*m", "", str(e)).removeprefix("ERROR: ")
    return text or type(e).__name__


def _decode(path: Path) -> np.ndarray:
    """Any audio file as 16 kHz mono float32, via PyAV (no system ffmpeg needed)."""
    resampler = av.AudioResampler(format="flt", layout="mono", rate=SR)
    parts = []
    with av.open(str(path)) as container:
        for frame in container.decode(audio=0):
            parts += [f.to_ndarray()[0] for f in resampler.resample(frame)]
    parts += [f.to_ndarray()[0] for f in resampler.resample(None)]
    return np.concatenate(parts)


def _fetch(video_id: str, folder: Path, cookies: io.StringIO | None = None) -> tuple[Path, dict]:
    options = {
        "format": "bestaudio/best",
        "outtmpl": str(folder / "source.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        # YouTube needs a JS runtime; the deno package ships one with the Python deps.
        "js_runtimes": {"deno": {"path": deno.find_deno_bin()}},
        "cookiefile": cookies,
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}")
        return Path(ydl.prepare_filename(info)), info


def _download(video_id: str, folder: Path, cookies: list[dict]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    try:
        source, info = _fetch(video_id, folder)
    except yt_dlp.utils.DownloadError as e:
        if "not a bot" not in str(e):
            # Often transient (HTTP 403 on a media URL, a dropped connection); a second
            # attempt fetches fresh URLs.
            log.info("%s: download failed, retrying: %s", video_id, _message(e))
            source, info = _fetch(video_id, folder)
        elif cookies:
            # The cookies stay in memory: yt-dlp reads them from a stream, never a file.
            log.info("%s: YouTube bot check, retrying with the browser's cookies", video_id)
            source, info = _fetch(video_id, folder, io.StringIO(netscape_cookies(cookies)))
        else:
            raise
    (folder / "context.txt").write_text(video_context(info), encoding="utf-8")
    tmp = folder / "audio.tmp.wav"
    sf.write(tmp, _decode(source), SR, subtype="PCM_16")
    tmp.replace(folder / "audio.wav")
    source.unlink()


def _prepare(job: Job) -> None:
    """Download the audio (cached), then hand the job to the worker."""
    cookies, job.cookies = job.cookies, []  # only this download may use them
    try:
        if not (job.dir / "audio.wav").exists():
            _download(job.video_id, job.dir, cookies)
        audio, _ = sf.read(job.dir / "audio.wav", dtype="float32")
        db = frame_db(audio)
        detected = job.dir / "language.txt"
        with lock:
            if job.status == "error":  # the model failed to load during the download
                return
            job.db, job.duration = db, round(len(db) / FPS, 2)
            if job.source_lang != "auto":
                job.language = job.source_lang
            elif detected.exists():
                job.language = detected.read_text().strip()
            if job.language:
                job.load_cache()
            else:
                job.status = "processing"
            lock.notify()
    except Exception as e:
        log.exception("preparing %s failed", job.id)
        with lock:
            job.status, job.error = "error", _message(e)


def _load_transcript(job: Job, transcript: dict) -> None:
    """The sentences built from the first transcript sent: cached clips were timed by them."""
    path = job.dir / "youtube" / "transcript.json"
    if not path.exists():
        sentences = build_sentences(transcript["segments"], transcript["duration"])
        _write_json(path, {"duration": transcript["duration"], "sentences": sentences})
    saved = json.loads(path.read_text())
    job.duration, job.sentences = saved["duration"], saved["sentences"]
    job.load_cache()


def _detect_language(job: Job) -> str:
    """Sum language probabilities over three 30 s samples spread across the video.

    Each sample costs an encoder pass, seconds on a laptop CPU. Three keep one odd sample
    (music, a quote in another language) from deciding.
    """
    t = time.monotonic()
    starts = [job.duration * (k + 0.5) / 3 for k in range(3)]
    probs = [asr.language_probs(job.model, _read(job, s, s + 30)) for s in starts]
    language = pick_language(probs)
    (job.dir / "language.txt").write_text(language)
    log.info("%s: detected language %s in %.1fs", job.video_id, language, time.monotonic() - t)
    return language


def _translate(job: Job, chunk: Range) -> list[dict]:
    """Cleaned English segments of a chunk, on the video timeline."""
    files = _chunk_files(job.asr_dir)
    if chunk in files:
        return json.loads(files[chunk].read_text())
    start, end = chunk
    audio = _read(job, start, end)
    db = frame_db(audio)
    # The text just before the chunk, when known, carries context across the cut.
    previous = [p for (a, b), p in files.items() if abs(b - start) < 1e-6]
    prompt = PROMPT
    if previous:
        prompt = " ".join(s["text"] for s in json.loads(previous[0].read_text()))[-200:] or PROMPT
    # The video's title and description spell out its names and jargon (RAG, not "rack").
    # Whisper cuts a prompt over 223 tokens from the start, so the recent text goes last.
    context = job.dir / "context.txt"
    if context.exists():
        prompt = f"{context.read_text(encoding='utf-8')} {prompt}"
    kept = []
    for seg in asr.translate(job.model, audio, job.language, prompt):
        a, b = seg["start"], min(seg["end"], end - start)
        peak = db[int(a * FPS) : int(b * FPS) + 1].max(initial=-100.0)
        if b > a and not is_junk(seg, peak):
            kept.append(
                {
                    "start": round(start + a, 2),
                    "end": round(start + b, 2),
                    "text": seg["text"].strip(),
                }
            )
    segs = [part for s in merge_segments(kept) for part in split_long(s)]
    _write_json(job.asr_dir / f"{_chunk_name(chunk)}.json", segs)
    return segs


def _process(job: Job, chunk: Range) -> list[dict]:
    if job.transcript:
        segs = [s for s in job.sentences if chunk[0] <= s["start"] < chunk[1]]
    else:
        segs = _translate(job, chunk)
    job.voice_dir.mkdir(parents=True, exist_ok=True)
    # Speech may run on past the chunk: continue after the previous chunk's last clip, and
    # end before the next chunk's first clip (or within MAX_LAG if it isn't done yet).
    with lock:
        before = [ss for (a, b), ss in job.chunks.items() if abs(b - chunk[0]) < 1e-6 and ss]
        after = [ss for (a, b), ss in job.chunks.items() if abs(a - chunk[1]) < 1e-6 and ss]
    free = before[0][-1]["end"] + GAP if before else -math.inf
    limit = after[0][0]["start"] if after else chunk[1] + MAX_LAG
    out = []
    for i, seg in enumerate(segs):
        clip = tts.speak(seg["text"], job.voice)
        if clip is None:
            continue
        next_start = segs[i + 1]["start"] if i + 1 < len(segs) else None
        start, speed, latest = place(seg["start"], free, len(clip) / tts.SR, next_start, limit)
        if speed > 1:
            clip = tts.speak(seg["text"], job.voice, speed)
        if clip is None:
            continue
        clip = tts.fit_clip(clip, latest - start)  # the last resort
        if not clip.size:
            continue
        seg_id = round(start * 1000)
        sf.write(job.voice_dir / f"{seg_id}.wav", clip, tts.SR)
        end = round(start + len(clip) / tts.SR, 2)
        out.append({"id": seg_id, "start": start, "end": end, "text": seg["text"], "audio": True})
        free = end + GAP
    _write_json(job.voice_dir / f"{_chunk_name(chunk)}.json", out)
    return out


def _pick() -> tuple[Job, Range | None] | None:
    """The most recently polled job with work left, and its next chunk.

    None instead of a chunk: load the model (while the audio downloads) and detect the language.
    """
    for job in sorted(jobs.values(), key=lambda j: j.polled, reverse=True):
        if time.monotonic() - job.polled > IDLE:
            continue
        if job.transcript:
            if job.status == "processing":
                starts = [s["start"] for s in job.sentences]
                chunk = next_text_chunk(starts, job.duration, job.processed(), job.at, job.prev_end)
                if chunk:
                    return job, chunk
            continue
        if job.status == "downloading" and not asr.loaded(job.model):
            return job, None
        if job.status != "processing":
            continue
        if job.language is None:
            return job, None
        known = {a: b for a, b in _chunk_files(job.asr_dir)}
        chunk = next_chunk(job.db, job.processed(), job.at, job.prev_end, known)
        if chunk:
            return job, chunk
    return None


def _work() -> None:
    while True:
        with lock:
            while (picked := _pick()) is None:
                lock.wait()
        job, chunk = picked
        try:
            if chunk is None:
                asr.load(job.model)
                with lock:
                    detect = job.status == "processing" and job.language is None
                if detect:
                    language = _detect_language(job)
                    with lock:
                        job.language = language
                        job.load_cache()
                continue
            t = time.monotonic()
            segs = _process(job, chunk)
            log.info("%s %.1f-%.1fs in %.1fs", job.id, *chunk, time.monotonic() - t)
            with lock:
                job.chunks[chunk] = segs
                job.prev_end = chunk[1]
                if not holes(job.processed(), job.duration):
                    job.status = "done"
        except Exception as e:
            log.exception("dubbing %s failed", job.id)
            with lock:
                job.status, job.error = "error", f"Dubbing failed: {_message(e)}"
