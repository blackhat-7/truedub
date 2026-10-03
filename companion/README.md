# TrueDub companion

Local server for the TrueDub extension. It downloads a video's audio, detects
the spoken language, translates the speech to English with Whisper and voices
it with Kokoro. It listens on `http://127.0.0.1:7861` only. The API is in
[../docs/API.md](../docs/API.md).

## Install and run

Everything comes in through [uv](https://docs.astral.sh/uv/): Python 3.12, the
Python dependencies, a JavaScript runtime for yt-dlp (`deno`) and audio
decoding (PyAV). No system ffmpeg is needed.

macOS / Linux:

```sh
cd companion && uv run truedub
```

Windows: run `install.ps1` once. It installs uv if missing, installs the
dependencies and starts the server hidden at every login (logs go to
`%USERPROFILE%\.cache\truedub\truedub.log`):

```powershell
powershell -ExecutionPolicy Bypass -File companion\install.ps1
```

Or run it in a console window with `companion\start.bat`.

## Whisper model

The default depends on the machine:

| Machine | Backend | Default model |
| --- | --- | --- |
| Apple silicon | mlx-whisper | `large-v3` |
| NVIDIA GPU (CUDA) | faster-whisper, float16 | `large-v3` |
| CPU only | faster-whisper, int8 | `medium` |

Override with `uv run truedub --model small` or `TRUEDUB_MODEL=small`. Choices:
`tiny`, `base`, `small`, `medium`, `large-v2`, `large-v3`. Turbo is not
offered: it was not trained to translate. `/health` reports the active model.

On a slow CPU, `small` keeps up with playback better than `medium`, at a clear
cost in translation quality. When the dub is not ready, the extension pauses the
video until it is.

## First run downloads

- Kokoro TTS (`kokoro-v1.0.onnx`, `voices-v1.0.bin`, about 350 MB) downloads at
  startup into `~/.cache/truedub/models`.
- The Whisper model downloads into the Hugging Face cache on the first dub
  (`large-v3` about 3 GB, `medium` about 1.5 GB), so the first job waits for it.

Dubs are cached in `~/.cache/truedub/<video_id>/`, so revisits are instant and
survive restarts. A new voice reuses the cached translation. Delete the folder
to free space.

## Keep yt-dlp updated

YouTube changes often and old yt-dlp versions break. Update it with:

```sh
cd companion && uv lock --upgrade-package yt-dlp --upgrade-package yt-dlp-ejs && uv sync
```

## Development

```sh
uv run pytest
uv run ruff check . && uv run ruff format --check .
```
