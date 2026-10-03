// The only part of the extension that talks to the companion. Content scripts on
// youtube.com cannot reach 127.0.0.1 themselves (CORS and Private Network Access).
const API = 'http://127.0.0.1:7861';

async function request(path, options) {
  let res;
  try {
    res = await fetch(API + path, options);
  } catch {
    throw new Error('offline');
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Companion error ${res.status}`);
  }
  return res;
}

const handlers = {
  health: () => request('/health').then((r) => r.json()),

  // The companion uses the cookies only if YouTube asks it to prove it isn't a bot.
  start: async ({ videoId, sourceLang, voice }) => {
    const cookies = (await chrome.cookies.getAll({ domain: 'youtube.com' })).map((c) => ({
      name: c.name,
      value: c.value,
      domain: c.domain,
      path: c.path,
      secure: c.secure,
      expires: c.expirationDate ?? 0,
    }));
    const res = await request('/dub', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ video_id: videoId, source_lang: sourceLang, voice, cookies }),
    });
    return res.json();
  },

  poll: ({ job, at }) =>
    request(`/dub/${encodeURIComponent(job)}?at=${at.toFixed(2)}`).then((r) => r.json()),

  // Messages are JSON, so the wav travels as base64.
  audio: async ({ job, id }) => {
    const res = await request(`/dub/${encodeURIComponent(job)}/audio/${id}`);
    return new Uint8Array(await res.arrayBuffer()).toBase64();
  },
};

chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  handlers[msg.type](msg).then(
    (data) => reply({ data }),
    (err) => reply({ error: err.message }),
  );
  return true; // reply asynchronously
});
