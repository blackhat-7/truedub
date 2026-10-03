'use strict';

// Runs in the page's MAIN world, where the YouTube player's API is. content.js asks
// it for YouTube's English translation of the captions in the spoken language.
// timedtext only answers with a proof-of-origin token that the player adds, so the
// player itself must request the track. We switch the player to the translated
// track, read the request's URL from resource timing, fetch it again, and put the
// user's caption state back.

(() => {
  const TIMEOUT_MS = 8000;
  const ENGLISH = { languageCode: 'en', languageName: 'English' };
  // The player's caption preferences; switching tracks would overwrite them.
  const PREFS = /^yt-player-caption/;

  const base = (lang) => lang.split('-')[0];

  // Prefer the user's choice, then the original audio track's language, then any
  // non-English auto-generated track. An English ASR track on non-English speech is
  // the bug TrueDub exists for, so English is never the spoken language here.
  function pickTrack(response, sourceLang) {
    const tracks = response.captions?.playerCaptionsTracklistRenderer?.captionTracks ?? [];
    const asr = tracks.filter((t) => t.kind === 'asr' && base(t.languageCode) !== 'en');
    const audio = (response.streamingData?.adaptiveFormats ?? []).map((f) => f.audioTrack).filter(Boolean);
    const original =
      audio.find((a) => /original/i.test(a.displayName)) ?? audio.find((a) => a.audioIsDefault && !a.isAutoDubbed);
    const inLanguage = (lang) => tracks.filter((t) => base(t.languageCode) === base(lang));
    let spoken = sourceLang;
    if (spoken === 'auto') {
      const lang = original?.id.split('.')[0];
      spoken = lang && base(lang) !== 'en' && inLanguage(lang).length ? lang : asr[0]?.languageCode;
    }
    if (!spoken) return null;
    const same = inLanguage(spoken); // manual captions beat auto-generated ones
    return same.find((t) => t.kind !== 'asr') ?? same[0] ?? null;
  }

  function toTranscript(json, language, duration) {
    const segments = [];
    for (const e of json.events ?? []) {
      const text = (e.segs ?? []).map((s) => s.utf8).join('');
      if (!text.trim()) continue;
      const start = e.tStartMs / 1000;
      segments.push({ start, end: start + (e.dDurationMs || 0) / 1000, text });
    }
    return segments.length ? { language, duration, segments } : null;
  }

  // Resolves with the URL of the player's next translated timedtext request.
  function nextCaptionUrl(videoId, signal) {
    return new Promise((resolve, reject) => {
      const observer = new PerformanceObserver((list) => {
        for (const { name } of list.getEntries()) {
          const url = new URL(name);
          const q = url.searchParams;
          if (url.pathname === '/api/timedtext' && q.get('v') === videoId && q.get('tlang') === 'en') {
            observer.disconnect();
            resolve(name);
          }
        }
      });
      observer.observe({ type: 'resource' });
      signal.addEventListener('abort', () => {
        observer.disconnect();
        reject(signal.reason);
      });
    });
  }

  async function capture(player, videoId, sourceLang, signal) {
    let response = player.getPlayerResponse();
    while (response?.videoDetails?.videoId !== videoId) {
      // The player may still hold the previous video right after a navigation.
      await new Promise((r) => setTimeout(r, 200));
      signal.throwIfAborted();
      response = player.getPlayerResponse();
    }
    const track = pickTrack(response, sourceLang);
    if (!track) return null;

    const wasOn = player.isSubtitlesOn();
    const previous = player.getOption('captions', 'track');
    const saved = Object.fromEntries(
      Object.keys(localStorage)
        .filter((k) => PREFS.test(k))
        .map((k) => [k, localStorage.getItem(k)]),
    );
    player.classList.add('truedub-capturing'); // hides the captions while we borrow them
    try {
      const url = nextCaptionUrl(videoId, signal);
      player.unloadModule('captions'); // a reload, so the player requests the track even if it has it
      player.loadModule('captions');
      player.setOption('captions', 'track', {
        languageCode: track.languageCode,
        kind: track.kind,
        vss_id: track.vssId,
        translationLanguage: ENGLISH,
      });
      const res = await fetch(await url, { signal });
      if (!res.ok) throw new Error(`timedtext answered ${res.status}`);
      const duration = player.classList.contains('ad-showing')
        ? Number(response.videoDetails.lengthSeconds)
        : player.getDuration();
      return toTranscript(await res.json(), track.languageCode, duration);
    } finally {
      if (wasOn && previous?.languageCode) player.setOption('captions', 'track', previous);
      else player.unloadModule('captions');
      for (const k of Object.keys(localStorage)) if (PREFS.test(k) && !(k in saved)) localStorage.removeItem(k);
      for (const [k, v] of Object.entries(saved)) localStorage.setItem(k, v);
      player.classList.remove('truedub-capturing');
    }
  }

  // Requests come from content.js: { truedub: 'transcript', id, videoId, sourceLang }.
  // The reply carries the transcript, or null when YouTube has none for the spoken
  // language or getting it failed. Captures run one at a time, so each restores the
  // user's own state.
  let queue = Promise.resolve();
  window.addEventListener('message', (e) => {
    if (e.source !== window || e.data?.truedub !== 'transcript') return;
    const { id, videoId, sourceLang } = e.data;
    queue = queue.then(async () => {
      let transcript = null;
      let failed = false;
      try {
        const player = document.querySelector('#movie_player');
        transcript = await capture(player, videoId, sourceLang, AbortSignal.timeout(TIMEOUT_MS));
      } catch (err) {
        console.debug('[TrueDub] captions:', err);
        failed = true;
      }
      window.postMessage({ truedub: 'transcript-reply', id, transcript, failed }, location.origin);
    });
  });
})();
