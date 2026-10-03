// Shared by the content script and the popup. Stored in chrome.storage.sync.
const DEFAULTS = {
  voice: 'am_michael',
  sourceLang: 'auto',
  duck: 0.15, // original audio volume while dubbing, relative to the user's volume
  captions: false,
  autoDub: false,
};
