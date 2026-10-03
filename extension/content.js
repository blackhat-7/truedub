'use strict';

// DEFAULTS comes from settings.js, loaded before this file.

const TICK_MS = 50; // sync loop; keeps running in background tabs, unlike requestAnimationFrame
const POLL_MS = 1500;
const PREFETCH_AHEAD = 30; // seconds of dub audio fetched ahead of the playhead
const KEEP_BEHIND = 30; // cached clips outside [t - KEEP_BEHIND, t + KEEP_AHEAD] are released
const KEEP_AHEAD = 90;
const MAX_DRIFT = 0.15; // seconds a clip may drift from the video before it is re-seeked
const START_LEAD = 0.05; // seconds between play() and audible output; clips are started this early
const SVG_NS = 'http://www.w3.org/2000/svg';

const log = (...args) => console.debug('[TrueDub]', ...args);

let settings = { ...DEFAULTS };
let wanted = false; // the user turned the dub on in this tab
let dub = null; // the running Dub
let ui = null;

async function send(type, payload) {
  const res = await chrome.runtime.sendMessage({ type, ...payload });
  if (res.error) throw new Error(res.error);
  return res.data;
}

function currentVideoId() {
  return location.pathname === '/watch' ? new URLSearchParams(location.search).get('v') : null;
}

// One dubbing session for one video.
class Dub {
  constructor(videoId, player) {
    this.videoId = videoId;
    this.player = player;
    this.video = player.querySelector('video');
    this.job = null;
    this.status = null;
    this.segments = [];
    this.clips = new Map(); // segment id -> { start, end, url }; url is null while loading
    this.current = null; // { id, start, audio }
    this.userVolume = this.video.volume; // the volume the user picked in YouTube
    this.lastVolume = this.video.volume; // the volume the video had after our last tick
    this.at = this.video.currentTime; // last playhead outside ads
    this.blocked = false; // autoplay policy refused to play; waiting for a click
    this.stopped = false;
    this.ticker = setInterval(() => this.tick(), TICK_MS);
    this.connect();
  }

  async connect() {
    setPill('Connecting…');
    try {
      const { job } = await send('start', {
        videoId: this.videoId,
        sourceLang: settings.sourceLang,
        voice: settings.voice,
      });
      if (this.stopped) return;
      this.job = job;
      log('job', job, 'for', this.videoId);
      this.poll();
    } catch (err) {
      this.fail(err);
    }
  }

  async poll() {
    try {
      if (this.status !== 'done') {
        const r = await send('poll', { job: this.job, at: this.at });
        if (this.stopped) return;
        if (r.status === 'error') throw new Error(r.error || 'the companion reported an error');
        this.segments = r.segments;
        this.report(r);
      }
      this.prefetch();
    } catch (err) {
      this.fail(err);
      return;
    }
    if (!this.stopped) this.pollTimer = setTimeout(() => this.poll(), POLL_MS);
  }

  report({ status, progress }) {
    if (status === 'downloading') setPill('Downloading audio…');
    else if (status === 'processing') setPill(`Translating… ${Math.round(progress * 100)}%`);
    else if (status === 'done' && this.status !== 'done') setPill('English dub ready', 'ok', 3000);
    this.status = status;
  }

  fail(err) {
    if (this.stopped) return; // a late reply for a session that already ended
    log('failed', err);
    wanted = false;
    stopDub();
    setPill(
      err.message === 'offline'
        ? 'TrueDub companion isn’t running. Click the TrueDub icon for setup.'
        : `Dub failed: ${err.message}`,
      'error',
      10000,
    );
  }

  stop() {
    this.stopped = true;
    clearInterval(this.ticker);
    clearTimeout(this.pollTimer);
    this.stopClip();
    this.duck(false);
    for (const clip of this.clips.values()) if (clip.url) URL.revokeObjectURL(clip.url);
    this.clips.clear();
  }

  prefetch() {
    const t = this.at;
    for (const s of this.segments) {
      if (s.audio && s.end > t && s.start < t + PREFETCH_AHEAD) this.fetchClip(s);
    }
    for (const [id, clip] of this.clips) {
      if (id === this.current?.id) continue;
      if (clip.end < t - KEEP_BEHIND || clip.start > t + KEEP_AHEAD) {
        if (clip.url) URL.revokeObjectURL(clip.url);
        this.clips.delete(id);
      }
    }
  }

  async fetchClip(seg) {
    if (this.clips.has(seg.id)) return;
    const clip = { start: seg.start, end: seg.end, url: null };
    this.clips.set(seg.id, clip);
    try {
      const wav = Uint8Array.fromBase64(await send('audio', { job: this.job, id: seg.id }));
      if (this.stopped || this.clips.get(seg.id) !== clip) return; // torn down or evicted meanwhile
      clip.url = URL.createObjectURL(new Blob([wav], { type: 'audio/wav' }));
    } catch (err) {
      // A failed clip stays in the cache without a url: it is skipped, and retried once evicted.
      if (err.message === 'offline') this.fail(err);
      else log('clip', seg.id, 'failed:', err.message);
    }
  }

  // Keep the original audio at the user's volume, or ducked relative to it.
  duck(on) {
    const v = this.video;
    if (Math.abs(v.volume - this.lastVolume) > 1e-3) this.userVolume = v.volume; // user or YouTube changed it
    const target = on ? this.userVolume * settings.duck : this.userVolume;
    if (Math.abs(v.volume - target) > 1e-3) v.volume = target;
    this.lastVolume = v.volume;
  }

  tick() {
    const v = this.video;
    const ad = this.player.classList.contains('ad-showing');
    if (!ad) this.at = v.currentTime;
    this.duck(!ad && this.segments.length > 0);

    const t = v.currentTime;
    const seg = ad ? undefined : this.segments.findLast((s) => s.start - START_LEAD <= t);
    showCaption(settings.captions && seg && t < seg.end ? seg.text : '');

    // A clip may run until the next segment's start, so the active clip is the latest one started.
    if (this.current?.id !== seg?.id) {
      this.stopClip();
      if (seg?.audio) this.startClip(seg);
    }
    if (!this.current) return;

    const a = this.current.audio;
    if (a.volume !== this.userVolume) a.volume = this.userVolume;
    if (a.muted !== v.muted) a.muted = v.muted;
    if (a.playbackRate !== v.playbackRate) a.playbackRate = v.playbackRate;

    const offset = t - this.current.start;
    const running = !ad && !v.paused && !v.seeking && v.readyState >= HTMLMediaElement.HAVE_FUTURE_DATA;
    if (!running || a.readyState < HTMLMediaElement.HAVE_METADATA || offset >= a.duration) {
      if (!a.paused) a.pause();
      return;
    }
    if (a.paused) {
      if (this.blocked) return;
      a.currentTime = Math.max(0, offset + START_LEAD);
      this.play(a);
    } else if (Math.abs(a.currentTime - offset) > MAX_DRIFT) {
      a.currentTime = offset;
    }
  }

  startClip(seg) {
    const clip = this.clips.get(seg.id);
    if (!clip?.url) {
      this.fetchClip(seg); // the next tick starts it mid-clip once loaded
      return;
    }
    const audio = new Audio(clip.url);
    audio.preservesPitch = true;
    this.current = { id: seg.id, start: seg.start, audio };
    log('clip', seg.id, 'at', this.video.currentTime.toFixed(2), 'segment start', seg.start);
  }

  stopClip() {
    this.current?.audio.pause();
    this.current = null;
  }

  play(audio) {
    audio.play().catch((err) => {
      if (err.name !== 'NotAllowedError' || this.stopped) return;
      this.blocked = true;
      setPill('Click the video to hear the dub');
      const resume = () => {
        this.blocked = false;
        setPill('');
      };
      document.addEventListener('pointerdown', resume, { once: true, capture: true });
    });
  }
}

function startDub() {
  stopDub();
  const id = currentVideoId();
  if (id && ui) dub = new Dub(id, ui.player);
  updateButton();
}

function stopDub() {
  if (!dub) return;
  dub.stop();
  dub = null;
  showCaption('');
  updateButton();
}

function toggle() {
  wanted = !dub;
  if (wanted) {
    startDub();
  } else {
    stopDub();
    setPill('Dub off', 'ok', 1500);
  }
}

// --- UI --------------------------------------------------------------------

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

function icon() {
  const svg = document.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('width', '24');
  svg.setAttribute('height', '24');
  svg.setAttribute('fill', 'none');
  svg.setAttribute('stroke', 'white');
  svg.setAttribute('stroke-width', '2');
  svg.setAttribute('stroke-linecap', 'round');
  // A speaker with sound waves. Head and body fill in when on, like the modern player's CC icon.
  for (const [d, fills] of [
    ['M12.5 8a3.5 3.5 0 1 1-7 0a3.5 3.5 0 1 1 7 0Z', true],
    ['M2.5 20.5c0-3.3 2.9-5.5 6.5-5.5s6.5 2.2 6.5 5.5Z', true],
    ['M16.5 5.5a4 4 0 0 1 0 5', false],
    ['M19.5 3a8 8 0 0 1 0 10', false],
  ]) {
    const path = document.createElementNS(SVG_NS, 'path');
    path.setAttribute('d', d);
    if (fills) path.setAttribute('class', 'truedub-fill');
    svg.append(path);
  }
  return svg;
}

function buildUI(player) {
  const button = el('button', 'ytp-button truedub-button');
  button.setAttribute('aria-keyshortcuts', 'Shift+D');
  button.append(icon());
  button.addEventListener('click', toggle);

  // Same markup as YouTube's own tooltip so the player's CSS styles it.
  const tooltip = el('div', 'ytp-tooltip ytp-bottom truedub-tooltip');
  const tooltipText = el('span', 'ytp-tooltip-text');
  const bottom = el('div', 'ytp-tooltip-bottom-text');
  bottom.append(tooltipText, el('div', 'ytp-tooltip-keyboard-shortcut', 'Shift+D'));
  const wrapper = el('div', 'ytp-tooltip-text-wrapper');
  wrapper.append(bottom);
  tooltip.append(wrapper);
  tooltip.style.display = 'none';
  button.addEventListener('mouseenter', () => {
    tooltip.style.display = 'block';
    const p = player.getBoundingClientRect();
    const b = button.getBoundingClientRect();
    const left = b.left + b.width / 2 - tooltip.offsetWidth / 2 - p.left;
    tooltip.style.left = `${Math.max(12, Math.min(left, p.width - tooltip.offsetWidth - 12))}px`;
    tooltip.style.top = `${b.top - p.top - tooltip.offsetHeight - 12}px`;
  });
  button.addEventListener('mouseleave', () => (tooltip.style.display = 'none'));

  const pillText = el('span');
  const pill = el('div', 'truedub-pill');
  pill.append(el('span', 'truedub-pill-icon'), pillText);

  const captionText = el('span');
  const caption = el('div', 'truedub-caption');
  caption.append(captionText);
  caption.hidden = true;

  const layer = el('div', 'truedub-layer');
  layer.append(pill, caption);
  player.append(layer, tooltip);

  const cc = player.querySelector('.ytp-subtitles-button');
  if (cc) cc.before(button);
  else player.querySelector('.ytp-right-controls').prepend(button);

  ui = { player, button, tooltip, tooltipText, pill, pillText, caption, captionText, pillTimer: 0 };
  updateButton();
}

function updateButton() {
  if (!ui) return;
  const label = dub ? 'Turn off English dub' : 'Dub in English';
  ui.button.setAttribute('aria-pressed', String(!!dub));
  ui.button.setAttribute('aria-label', `${label} (shift+d)`);
  ui.tooltipText.textContent = label;
}

// state: 'busy' (spinner), 'ok' or 'error'. An empty text hides the pill.
function setPill(text, state = 'busy', hideAfter = 0) {
  if (!ui) return;
  clearTimeout(ui.pillTimer);
  if (text) {
    ui.pill.dataset.state = state;
    ui.pillText.textContent = text;
  }
  ui.pill.classList.toggle('truedub-visible', !!text);
  if (hideAfter) ui.pillTimer = setTimeout(() => setPill(''), hideAfter);
}

function showCaption(text) {
  if (!ui || ui.captionText.textContent === text) return;
  ui.captionText.textContent = text;
  ui.caption.hidden = !text;
}

async function waitForPlayer() {
  while (currentVideoId()) {
    const player = document.querySelector('#movie_player');
    if (player?.querySelector('.ytp-right-controls') && player.querySelector('video')) return player;
    await new Promise((r) => setTimeout(r, 250));
  }
  return null;
}

// --- Wiring ----------------------------------------------------------------

async function onNavigate() {
  const id = currentVideoId();
  if (dub?.videoId === id) return;
  stopDub();
  setPill('');
  if (!id) return;
  const player = await waitForPlayer();
  if (!player || id !== currentVideoId() || dub) return; // navigated again meanwhile
  if (!ui?.button.isConnected || ui.player !== player) buildUI(player);
  if (wanted || settings.autoDub) startDub();
}

document.addEventListener('yt-navigate-finish', onNavigate);

document.addEventListener(
  'keydown',
  (e) => {
    if (e.code !== 'KeyD' || !e.shiftKey || e.ctrlKey || e.metaKey || e.altKey || e.repeat) return;
    const target = e.composedPath()[0];
    if (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName)) return;
    if (!ui?.button.isConnected || !currentVideoId()) return;
    e.preventDefault();
    e.stopPropagation();
    toggle();
  },
  true,
);

chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== 'sync') return;
  for (const [key, { newValue }] of Object.entries(changes)) settings[key] = newValue ?? DEFAULTS[key];
  if (dub && (changes.voice || changes.sourceLang)) startDub(); // a new job for the new voice/language
  if (changes.autoDub && settings.autoDub && !dub && ui?.button.isConnected) startDub();
});

chrome.storage.sync.get(DEFAULTS).then((stored) => {
  settings = stored;
  onNavigate(); // the first yt-navigate-finish may have fired before this script ran
});
