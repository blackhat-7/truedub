# TrueDub

Real dubbing for YouTube videos that YouTube's auto-dub silently skips.

YouTube's auto-dub trusts the language the uploader declared. Many videos are
tagged English but spoken in another language, or a mix like Hinglish,
Spanglish or Taglish. Auto-dub shows "on" and plays the original audio.
TrueDub listens to the speech itself, translates it, and dubs it in a natural
English voice. Everything runs on your machine.

## How it works

- **Companion** (`companion/`): a local Python server. Downloads the audio,
  detects the spoken language, translates speech to English with Whisper, and
  voices it with Kokoro TTS.
- **Extension** (`extension/`): a Chrome extension. Adds a Dub button to the
  YouTube player and plays the dub in sync, with optional captions.

See [docs/API.md](docs/API.md) for the contract between them.

## Install

You need Google Chrome (or another Chromium browser) and about 3 GB of disk.

### Windows

1. Download the repo: **Code → Download ZIP** on
   [GitHub](https://github.com/blackhat-7/truedub), then unzip it. You can also
   `git clone https://github.com/blackhat-7/truedub`.
2. In the unzipped folder, right-click `companion\install.ps1` → **Run with
   PowerShell**. Or run this from PowerShell:
   `powershell -ExecutionPolicy Bypass -File companion\install.ps1`.
   It installs everything and starts the companion now and at every login.
3. Open `chrome://extensions`, turn on **Developer mode**, click **Load
   unpacked** and pick the `extension` folder.
4. Open a YouTube video and press the Dub button in the player, or
   **Shift+D**.

### macOS / Linux

Install [uv](https://docs.astral.sh/uv/), then run `cd companion && uv run truedub`
and keep it running while you watch. Load the extension as in step 3.

### What to expect on a laptop without a GPU

- **Fast mode (default):** TrueDub voices YouTube's own English translation of
  the video's captions. The dub starts within seconds and keeps up with
  playback. The first run downloads the 350 MB voice model.
- **On this computer (small / medium / large-v3):** pick it in the popup under
  Translation. Whisper translates the audio locally: often more accurate, but
  on a CPU-only laptop `medium` runs at about half of realtime, so the video
  pauses with "Dubbing ahead…" to wait. The model downloads on first use
  (about 1.5 GB for medium).
- TrueDub switches to local Whisper by itself when YouTube has no captions in
  the language actually spoken, e.g. a Hindi video tagged as English.
- Finished dubs are cached, so watching a video again is instant.
