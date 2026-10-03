# Anuvad

Real English dubbing for YouTube videos that YouTube's auto-dub silently skips.

YouTube's auto-dub trusts the video's declared language. Most Indian dev videos
are tagged English but spoken in Hindi or Hinglish, so auto-dub shows "on" and
plays the original audio. Anuvad listens to the actual speech, translates it,
and dubs it in a natural English voice. Everything runs on your machine.

## How it works

- **Companion** (`companion/`): a local Python server. Downloads the audio,
  translates speech to English with Whisper, voices it with Kokoro TTS.
- **Extension** (`extension/`): a Chrome extension. Adds a Dub button to the
  YouTube player and plays the dub in sync, with optional English captions.

See [docs/API.md](docs/API.md) for the contract between them.
