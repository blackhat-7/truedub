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

- The first run downloads about 2 GB of models: the 350 MB voice when the
  companion starts and Whisper `medium` (1.5 GB) on the first dub. That first
  dub waits for the download.
- On a CPU-only laptop the `medium` model runs at about half of realtime. The
  extension pauses the video and shows "Dubbing ahead…" until enough is ready,
  then plays. Pausing the video for a minute lets it get further ahead.
- For more speed and less accuracy, use the `small` model. Run
  `setx TRUEDUB_MODEL small` (Windows), then sign out and back in. On macOS or
  Linux run `uv run truedub --model small`.
- Finished dubs are cached, so watching a video again is instant.
