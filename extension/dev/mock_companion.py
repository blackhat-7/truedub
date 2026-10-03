"""A fake TrueDub companion for testing the extension without the real models.

Implements docs/API.md with made-up segments every 5 s and a speech-free gap
from 120 s to 150 s. Work is finished at SPEED seconds of video per second,
starting at the playhead, like the real server. Clips are spoken with macOS
`say`, so this runs on macOS only.

    python3 extension/dev/mock_companion.py [SPEED]   # default 20; below 1 is slower than realtime
"""

import json
import re
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PORT = 7861
DURATION = 600.0
SEGMENTS = [
    {"id": i, "start": 1.0 + 5 * i, "end": 4.5 + 5 * i, "text": f"This is segment {i}, spoken in English."}
    for i in range(int(DURATION // 5))
    if not 24 <= i < 30
]
SPEED = float(sys.argv[1]) if len(sys.argv) > 1 else 20.0
CLIPS = Path(tempfile.mkdtemp(prefix="truedub-mock-"))

jobs = {}  # job id -> {"done": set of finished indexes into SEGMENTS, "credit": seconds, "t": last poll}
lock = threading.Lock()


def span(i):
    """The timeline a finished segment covers: itself plus the silence up to the next one."""
    end = SEGMENTS[i + 1]["start"] if i + 1 < len(SEGMENTS) else DURATION
    return [0.0 if i == 0 else SEGMENTS[i]["start"], end]


def merged(spans):
    out = []
    for start, end in sorted(spans):
        if out and start <= out[-1][1]:
            out[-1][1] = max(out[-1][1], end)
        else:
            out.append([start, end])
    return out


def clip(i):
    path = CLIPS / f"{i}.wav"
    with lock:
        if not path.exists():
            text = SEGMENTS[i]["text"]
            subprocess.run(
                ["say", "-r", "210", "-o", str(path), "--file-format=WAVE", "--data-format=LEI16@24000", text],
                check=True,
            )
    return path.read_bytes()


class Handler(BaseHTTPRequestHandler):
    def send(self, status, body, content_type="application/json"):
        if content_type == "application/json":
            body = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/health":
            return self.send(200, {"ok": True, "version": "mock", "asr": "mock",
                                   "voices": ["af_heart", "af_bella", "am_michael", "bf_emma", "bm_george"]})
        if m := re.fullmatch(r"/dub/([^/]+)/audio/(\d+)", url.path):
            job, seg_id = jobs.get(m[1]), int(m[2])
            i = next((i for i, s in enumerate(SEGMENTS) if s["id"] == seg_id), None)
            if job is None or i not in job["done"]:
                return self.send(404, {"detail": "clip not ready"})
            return self.send(200, clip(i), "audio/wav")
        if m := re.fullmatch(r"/dub/([^/]+)", url.path):
            job = jobs.get(m[1])
            if job is None:
                return self.send(404, {"detail": "unknown job"})
            at = float(parse_qs(url.query).get("at", ["0"])[0])
            now = time.monotonic()
            job["credit"] += SPEED * (now - job["t"])
            job["t"] = now
            todo = [i for i in range(len(SEGMENTS)) if i not in job["done"]]
            todo.sort(key=lambda i: (span(i)[1] <= at, span(i)[0]))  # playhead first
            for i in todo:
                cost = span(i)[1] - span(i)[0]
                if job["credit"] < cost:
                    break
                job["credit"] -= cost
                job["done"].add(i)
            done = sorted(job["done"])
            status = "done" if len(done) == len(SEGMENTS) else "processing"
            return self.send(200, {
                "status": status, "error": None, "duration": DURATION,
                "progress": sum(span(i)[1] - span(i)[0] for i in done) / DURATION,
                "segments": [dict(SEGMENTS[i], audio=True) for i in done],
                "processed": merged(span(i) for i in done),
            })
        self.send(404, {"detail": "not found"})

    def do_POST(self):
        if self.path != "/dub":
            return self.send(404, {"detail": "not found"})
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        job = f"{body['video_id']}-{body['source_lang']}-{body['voice']}"
        jobs.setdefault(job, {"done": set(), "credit": 0.0, "t": time.monotonic()})
        self.send(200, {"job": job})


if __name__ == "__main__":
    print(f"mock companion on http://127.0.0.1:{PORT}, {SPEED}x realtime, clips in {CLIPS}")
    ThreadingHTTPServer.request_queue_size = 64  # the extension prefetches clips in parallel
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
