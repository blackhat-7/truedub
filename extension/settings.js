// Shared by the content script and the popup. Stored in chrome.storage.sync.
const DEFAULTS = {
  voice: 'am_michael',
  sourceLang: 'auto',
  engine: 'youtube', // 'youtube' voices YouTube's caption translation; a Whisper model name translates locally
  duck: 0.15, // original audio volume while dubbing, relative to the user's volume
  captions: false,
  autoDub: false,
};
