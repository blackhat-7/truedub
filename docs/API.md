# Companion API

The companion is a local HTTP server on `http://127.0.0.1:7861`. It binds to
loopback only. The extension's service worker is the only client.

All times are seconds on the original video's timeline.

## `GET /health`

```json
{ "ok": true, "version": "0.1.0", "asr": "mlx-whisper", "voices": ["af_heart", "am_michael", "..."] }
```

## `POST /dub`

Start (or resume) dubbing a video. Idempotent: the same inputs return the same
job, and finished work is served from the on-disk cache.

Request:

```json
{ "video_id": "dQw4w9WgXcQ", "source_lang": "hi", "voice": "am_michael" }
```

`source_lang` is a Whisper language code, or `"auto"`. Auto never picks English:
the user only dubs videos they cannot understand, and "detected English" is
exactly the bug YouTube has with Hinglish.

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
  "segments": [
    { "id": 0, "start": 3.2, "end": 7.9, "text": "So today we will build a REST API.", "audio": true }
  ]
}
```

- `segments` is sorted by `start` and only contains finished segments.
- `audio: true` means the clip is ready at the audio endpoint.
- A clip never runs past the next segment's `start`. The server speeds up
  speech (pitch preserved) to fit; the client plays each clip at `start`.

## `GET /dub/{job}/audio/{id}`

The segment's dubbed speech as `audio/wav`.

Errors use HTTP status codes with `{ "detail": "..." }`.
