'use strict';

// Whisper language codes. "auto" detects the language from the speech itself.
const LANGUAGES = [
  ['auto', 'Auto-detect'],
  ['ar', 'Arabic'],
  ['bn', 'Bengali'],
  ['zh', 'Chinese'],
  ['nl', 'Dutch'],
  ['fr', 'French'],
  ['de', 'German'],
  ['el', 'Greek'],
  ['gu', 'Gujarati'],
  ['he', 'Hebrew'],
  ['hi', 'Hindi'],
  ['id', 'Indonesian'],
  ['it', 'Italian'],
  ['ja', 'Japanese'],
  ['kn', 'Kannada'],
  ['ko', 'Korean'],
  ['ms', 'Malay'],
  ['ml', 'Malayalam'],
  ['mr', 'Marathi'],
  ['ne', 'Nepali'],
  ['fa', 'Persian'],
  ['pl', 'Polish'],
  ['pt', 'Portuguese'],
  ['pa', 'Punjabi'],
  ['ro', 'Romanian'],
  ['ru', 'Russian'],
  ['es', 'Spanish'],
  ['sw', 'Swahili'],
  ['sv', 'Swedish'],
  ['tl', 'Tagalog'],
  ['ta', 'Tamil'],
  ['te', 'Telugu'],
  ['th', 'Thai'],
  ['tr', 'Turkish'],
  ['uk', 'Ukrainian'],
  ['ur', 'Urdu'],
  ['vi', 'Vietnamese'],
];

const $ = (id) => document.getElementById(id);

const FAST = ['youtube', 'Fast — YouTube’s translation (recommended)'];
const MODEL_NOTES = {
  small: 'small (faster, less accurate)',
  'large-v3': 'large-v3 (best; needs a GPU or Apple silicon)',
};

// Whisper models run in the companion, on the user's computer.
function modelOption(model, defaultModel) {
  const note = model === defaultModel ? ' · default' : '';
  return [model, `On this computer — ${MODEL_NOTES[model] ?? model}${note}`];
}

// Kokoro voice ids look like "am_michael": accent, gender, name.
function voiceLabel(id) {
  const [, accent, gender, name] = id.match(/^([a-z])([fm])_(.+)$/) || [];
  if (!name) return id;
  const who = [{ a: 'US', b: 'UK' }[accent], gender === 'f' ? 'female' : 'male'];
  return `${name[0].toUpperCase()}${name.slice(1)} · ${who.filter(Boolean).join(' ')}`;
}

function fill(select, options, value) {
  select.replaceChildren(...options.map(([v, text]) => new Option(text, v)));
  select.value = value;
}

function save(key, value) {
  chrome.storage.sync.set({ [key]: value });
}

function showDuck(value) {
  $('duckValue').textContent = `${Math.round(value * 100)}%`;
  $('duck').style.setProperty('--fill', `${value * 100}%`);
}

async function init() {
  const settings = await chrome.storage.sync.get(DEFAULTS);

  fill($('engine'), settings.engine === FAST[0] ? [FAST] : [FAST, modelOption(settings.engine)], settings.engine);
  fill($('sourceLang'), LANGUAGES, settings.sourceLang);
  fill($('voice'), [[settings.voice, voiceLabel(settings.voice)]], settings.voice);
  $('duck').value = settings.duck * 100;
  showDuck(settings.duck);
  $('captions').checked = settings.captions;
  $('autoDub').checked = settings.autoDub;

  $('engine').onchange = (e) => save('engine', e.target.value);
  $('voice').onchange = (e) => save('voice', e.target.value);
  $('sourceLang').onchange = (e) => save('sourceLang', e.target.value);
  $('duck').oninput = (e) => showDuck(e.target.value / 100);
  $('duck').onchange = (e) => save('duck', e.target.value / 100);
  $('captions').onchange = (e) => save('captions', e.target.checked);
  $('autoDub').onchange = (e) => save('autoDub', e.target.checked);

  const health = await chrome.runtime.sendMessage({ type: 'health' });
  const status = $('status');
  if (health.error) {
    status.dataset.state = 'offline';
    status.textContent = health.error === 'offline' ? 'Companion offline' : 'Companion error';
    status.title = health.error;
    $('setup').hidden = false;
    return;
  }
  const { version, asr, voices, models, default_model: defaultModel } = health.data;
  status.dataset.state = 'online';
  status.textContent = 'Companion ready';
  $('model').textContent = `Local model: ${asr} · companion ${version}`;
  $('model').hidden = false;
  const engine = models.includes(settings.engine) ? settings.engine : FAST[0];
  fill($('engine'), [FAST, ...models.map((m) => modelOption(m, defaultModel))], engine);
  if (engine !== settings.engine) save('engine', engine);
  if (voices.length) {
    const voice = voices.includes(settings.voice) ? settings.voice : voices[0];
    fill($('voice'), voices.map((v) => [v, voiceLabel(v)]), voice);
    if (voice !== settings.voice) save('voice', voice);
  }
}

init();
