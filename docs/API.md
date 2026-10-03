# Companion API

The companion is a local HTTP server on `http://127.0.0.1:7861`. It binds to
loopback only. The extension's service worker is the only client.

All times are seconds on the original video's timeline.

## `GET /health`

```json
{ "ok": true, "version": "0.1.0", "asr": "mlx-whisper large-v3 (mlx)", "voices": ["af_heart", "am_michael", "..."] }
```

`asr` names the backend, Whisper model and device. It is informational.

## `POST /dub`

Start (or resume) dubbing a video. Idempotent: the same inputs return the same
job, and finished work is served from the on-disk cache.

Request:

```json
{ "video_id": "dQw4w9WgXcQ", "source_lang": "auto", "voice": "am_michael" }
```

`source_lang` is a Whisper language code, or `"auto"`. Auto detects the language
from the audio itself and never picks the target language (English): the user
only presses Dub on speech they cannot understand. Mixed speech such as Hinglish
often scores as English, which is exactly the bug YouTube's auto-dub has.

Response: `{ "job": "<job id>" }`

## `GET /dub/{job}?at=<seconds>`

Poll job state. `at` is the current playhead. The server processes the part of
the video at or after the playhead first, so seeking reprioritises work.

```json
{
  "status": "downloading | processing | done | error",
  "error": null,
  "duration": 812.4,
  "progress": 0.37,
  "processed": [[0.0, 61.4], [298.2, 340.0]],
  "segments": [
    { "id": 0, "start": 3.2, "end": 7.9, "text": "So today we will build a REST API.", "audio": true }
  ]
}
```

- `duration` is `null` until the audio is downloaded.
- `processed` lists the sorted, merged time ranges that are fully done (ASR and
  TTS, silent parts included). When the playhead reaches a time outside these
  ranges, the dub there is not ready yet.
- `segments` is sorted by `start` and only contains finished segments.
- `audio: true` means the clip is ready at the audio endpoint.
- A clip never runs past the next segment's `start`. The server speeds up
  speech (pitch preserved) to fit; the client plays each clip at `start`.

## `GET /dub/{job}/audio/{id}`

The segment's dubbed speech as `audio/wav`.

Errors use HTTP status codes with `{ "detail": "..." }`.
