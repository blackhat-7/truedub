"""A fake TrueDub companion for testing the extension without the real models.

Implements docs/API.md with made-up segments every 5 s. Each poll "finishes" a
few more segments, starting at the playhead, like the real server. Clips are
spoken with macOS `say`, so this runs on macOS only.

    python3 extension/dev/mock_companion.py
"""

import json
import re
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PORT = 7861
DURATION = 600.0
SEGMENTS = [
    {"id": i, "start": 1.0 + 5 * i, "end": 4.5 + 5 * i, "text": f"This is segment {i}, spoken in English."}
    for i in range(int(DURATION // 5))
]
PER_POLL = 8  # segments finished per poll
CLIPS = Path(tempfile.mkdtemp(prefix="truedub-mock-"))

jobs = {}  # job id -> set of finished segment ids
lock = threading.Lock()


def clip(seg_id):
    path = CLIPS / f"{seg_id}.wav"
    with lock:
        if not path.exists():
            text = SEGMENTS[seg_id]["text"]
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
            job, seg_id = m[1], int(m[2])
            if job not in jobs or seg_id not in jobs[job]:
                return self.send(404, {"detail": "clip not ready"})
            return self.send(200, clip(seg_id), "audio/wav")
        if m := re.fullmatch(r"/dub/([^/]+)", url.path):
            done = jobs.get(m[1])
            if done is None:
                return self.send(404, {"detail": "unknown job"})
            at = float(parse_qs(url.query).get("at", ["0"])[0])
            todo = [s["id"] for s in SEGMENTS if s["id"] not in done]
            todo.sort(key=lambda i: (SEGMENTS[i]["end"] < at, SEGMENTS[i]["start"]))  # playhead first
            done.update(todo[:PER_POLL])
            finished = [dict(s, audio=True) for s in SEGMENTS if s["id"] in done]
            status = "done" if len(done) == len(SEGMENTS) else "processing"
            return self.send(200, {"status": status, "error": None, "duration": DURATION,
                                   "progress": len(done) / len(SEGMENTS), "segments": finished})
        self.send(404, {"detail": "not found"})

    def do_POST(self):
        if self.path != "/dub":
            return self.send(404, {"detail": "not found"})
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        job = f"{body['video_id']}-{body['source_lang']}-{body['voice']}"
        jobs.setdefault(job, set())
        self.send(200, {"job": job})


if __name__ == "__main__":
    print(f"mock companion on http://127.0.0.1:{PORT}, clips in {CLIPS}")
    ThreadingHTTPServer.request_queue_size = 64  # the extension prefetches clips in parallel
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
