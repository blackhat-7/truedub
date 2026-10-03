# Companion API

The companion is a local HTTP server on `http://127.0.0.1:7861`. It binds to
loopback only. The extension's service worker is the only client.

All times are seconds on the original video's timeline.

## `GET /health`

```json
{
  "ok": true,
  "version": "0.1.0",
  "asr": "mlx-whisper large-v3 (mlx)",
  "models": ["small", "medium", "large-v3"],
  "default_model": "large-v3",
  "voices": ["af_heart", "am_michael", "..."]
}
```

`asr` names the backend, default Whisper model and device. It is informational.
`models` are the Whisper models a request may pick; `default_model` is what
`"auto"` means on this machine (large-v3 with a GPU or Apple silicon, else medium).

## `POST /dub`

Start (or resume) dubbing a video. Idempotent: the same inputs return the same
job, and finished work is served from the on-disk cache.

There are two ways to get the English text:

- **Transcript** (fast): the extension sends YouTube's own English translation of
  the video's captions in the spoken language. The companion skips the download
  and Whisper and only voices the text. Seconds, even on a laptop CPU.
- **Whisper** (no `transcript`): the companion downloads the audio and translates
  it locally with Whisper. Used when YouTube has no captions in the spoken
  language (e.g. a Hindi video tagged English), or when the user picks a model.

Request:

```json
{
  "video_id": "dQw4w9WgXcQ",
  "source_lang": "auto",
  "model": "auto",
  "voice": "am_michael",
  "transcript": {
    "language": "hi",
    "duration": 1024.8,
    "segments": [{ "start": 0.0, "end": 3.8, "text": "Hello everyone, in this video I am going to" }]
  },
  "cookies": [
    { "name": "SID", "value": "...", "domain": ".youtube.com", "path": "/", "secure": true, "expires": 1893456000 }
  ]
}
```

`source_lang` is a Whisper language code, or `"auto"`. Auto detects the language
from the audio itself and never picks the target language (English): the user
only presses Dub on speech they cannot understand. Mixed speech such as Hinglish
often scores as English, which is exactly the bug YouTube's auto-dub has.

`model` is one of `/health`'s `models`, or `"auto"` (the default). Whisper only.

`transcript` is optional. `language` is the spoken language YouTube captioned
(informational), `duration` the video length. `segments` are YouTube's caption
events as they come: fragments of sentences, not sorted or merged. The
companion joins fragments into sentences (by punctuation and pauses, with a cap
on length) before voicing them. With a transcript, `source_lang`, `model` and
`cookies` are ignored. The job id differs from the Whisper job for the same
video, so both can be cached side by side.

`cookies` is optional: the browser's youtube.com cookies (`expires` is Unix time,
0 for a session cookie). The companion uses them only as a fallback, when
downloading the audio fails with YouTube's "Sign in to confirm you're not a
bot" check, and retries once with them. They are kept in memory for that one
download, never written to disk and never logged.

Response: `{ "job": "<job id>" }`

## `GET /dub/{job}?at=<seconds>`

Poll job state. `at` is the current playhead. The server processes the part of
the video at or after the playhead first, so seeking reprioritises work. Work on
a job pauses after two minutes without a poll and resumes on the next one.

```json
{
  "status": "downloading | loading | detecting | processing | done | error",
  "error": null,
  "duration": 812.4,
  "progress": 0.37,
  "processed": [[0.0, 61.4], [298.2, 340.0]],
  "segments": [
    { "id": 0, "start": 3.2, "end": 7.9, "text": "So today we will build a REST API.", "audio": true }
  ]
}
```

- `status`: `downloading` the audio, `loading` the Whisper model (it downloads on
  first use), `detecting` the spoken language, `processing` the dub, `done`, or
  `error` (then `error` says why).
- `duration` is `null` until the audio is downloaded. Transcript jobs skip
  straight to `processing`.
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
