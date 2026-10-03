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
