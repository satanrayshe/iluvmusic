'use strict';

const $ = (sel) => document.querySelector(sel);

const state = {
  settings: { ...ILUV.DEFAULTS },
  tracks: {},
  companion: null,
  now: null,
  tab: 'saved',
};

const REASONS = {
  ad: 'an ad is playing',
  shorts: 'Shorts are not counted',
  podcast: 'podcasts are not counted',
  'not-music': "YouTube doesn't list this as music",
  'too-short': 'shorter than your minimum',
  'too-long': 'longer than your maximum',
  'unknown-length': 'live or unknown length',
  'other-sites-off': 'other websites are turned off',
};

const IN_FLIGHT = ['pending', 'queued', 'downloading'];
const SAVED_SIDE = ['done', 'missing', 'error', ...IN_FLIGHT];

// ---- helpers ----------------------------------------------------------------

function h(tag, props, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'text') el.textContent = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? '' : String(v));
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

async function send(type, extra) {
  const res = await chrome.runtime.sendMessage({ type, ...(extra || {}) });
  if (!res || res.ok === false) throw new Error((res && res.error) || 'Something went wrong');
  return res;
}

let toastTimer = 0;
function toast(message) {
  const el = $('#toast');
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 3500);
}

async function act(type, extra, done) {
  try {
    await send(type, extra);
    if (done) toast(done);
  } catch (e) {
    toast(e.message);
  }
}

function artFor(t) {
  let src = t.artwork || '';
  if (!src && t.key && t.key.startsWith('yt:')) src = `https://i.ytimg.com/vi/${t.key.slice(3)}/mqdefault.jpg`;
  if (!src) return h('div', { class: 'art-empty', 'aria-hidden': 'true' }, '♪');
  const img = h('img', { class: 'art', src, alt: '', loading: 'lazy', referrerpolicy: 'no-referrer' });
  img.addEventListener('error', () => img.replaceWith(h('div', { class: 'art-empty', 'aria-hidden': 'true' }, '♪')), { once: true });
  return img;
}

function subline(t) {
  return [t.artist, ILUV.siteLabel(t)].filter(Boolean).join(' · ');
}

function meter(plays, threshold) {
  const pct = Math.min(100, Math.round((plays / threshold) * 100));
  return h('div', { class: 'meter' },
    h('div', { class: 'track' }, h('div', { class: 'fill', style: `width:${pct}%` })),
    `${plays} of ${threshold} plays`);
}

const companionOnline = () => !!(state.companion && state.companion.online);

function statusLine(t) {
  switch (t.status) {
    case 'pending':
      return h('div', { class: 'state' }, companionOnline() ? 'Starting…' : 'Waiting for the companion app');
    case 'queued':
      return h('div', { class: 'state' }, 'In line to save');
    case 'downloading':
      return h('div', { class: 'state' }, 'Saving as MP3…');
    case 'error':
      return h('div', { class: 'state bad', title: t.error || '' }, t.error || "Couldn't save this one");
    case 'missing':
      return h('div', { class: 'state bad' }, 'The file was moved or deleted');
    default:
      return null;
  }
}

// ---- rendering ----------------------------------------------------------------

function render() {
  renderTop();
  renderCompanion();
  renderNow();
  renderLists();
  renderSettings();
}

function renderTop() {
  const { enabled, threshold } = state.settings;
  $('#enabled').checked = !!enabled;
  $('#enabledLabel').textContent = enabled ? 'On' : 'Paused';
  $('#threshold').textContent = threshold;
  $('#playsWord').textContent = threshold === 1 ? 'play' : 'plays';
  $('#less').disabled = threshold <= 1;
}

function renderCompanion() {
  const box = $('#companion');
  const c = state.companion;
  box.replaceChildren();
  if (!c) {
    box.append(h('div', { class: 'status' }, h('span', { class: 'dot' }), 'Looking for the companion app…'));
    return;
  }
  if (c.online) {
    box.append(
      h('div', { class: 'head' },
        h('div', { class: 'status' }, h('span', { class: 'dot ok' }), 'Companion running'),
        h('button', { class: 'link', type: 'button', onclick: () => act('reveal', { key: '' }) }, 'Open folder')),
      h('code', { title: c.downloadDir || '' }, c.downloadDir || ''),
    );
    if (!c.ffmpeg) box.append(h('p', { class: 'warn' }, 'ffmpeg is missing, so songs can’t be turned into MP3s. See the README.'));
    if (!c.jsRuntime) box.append(h('p', { class: 'warn' }, 'Install Deno or Node.js so YouTube downloads work.'));
    return;
  }
  box.append(
    h('div', { class: 'head' },
      h('div', { class: 'status' }, h('span', { class: 'dot off' }), 'Companion app isn’t running'),
      h('button', { class: 'link', type: 'button', onclick: () => act('refresh') }, 'Check again')),
    h('p', null, 'Plays still count. Songs get saved as soon as it starts. To start it, double-click ',
      h('strong', null, 'start-windows.bat'), ' in the companion folder (Mac or Linux: ',
      h('strong', null, 'start-mac-linux.sh'), ').'),
  );
}

function renderNow() {
  const box = $('#now');
  const n = state.now;
  // Playing tabs check in every 45s; anything quieter than that has stopped.
  if (!n || Date.now() - (n.at || 0) > 100 * 1000) {
    box.hidden = true;
    return;
  }
  const rec = state.tracks[n.key];
  const plays = rec ? rec.plays : 0;
  const threshold = state.settings.threshold;
  let detail;
  let action = null;

  if (n.reason) {
    detail = h('div', { class: 'state' }, `Not counted: ${REASONS[n.reason] || n.reason}`);
  } else if (rec && rec.status === 'done') {
    detail = h('div', { class: 'state ok' }, 'Saved');
    action = h('button', { class: 'btn small', type: 'button', onclick: () => act('reveal', { key: n.key }) }, 'Show');
  } else if (rec && rec.status === 'ignored') {
    detail = h('div', { class: 'state' }, 'You chose not to save this one');
    action = h('button', { class: 'btn small', type: 'button', onclick: () => act('unignore', { key: n.key }) }, 'Undo');
  } else if (rec && rec.status !== 'counting') {
    detail = statusLine(rec);
  } else {
    detail = meter(plays, threshold);
    action = h('button', {
      class: 'btn small primary',
      type: 'button',
      onclick: () => act('saveNow', { key: n.key, track: n }, 'Saving it now'),
    }, 'Save now');
  }

  box.replaceChildren(
    h('div', { class: 'label' }, 'Now playing'),
    h('div', { class: 'body' },
      artFor(n),
      h('div', { class: 'info' },
        h('div', { class: 'title', title: n.title }, n.title || 'Untitled'),
        h('div', { class: 'sub' }, subline(n)),
        detail),
      action && h('div', { class: 'actions' }, action)),
  );
  box.hidden = false;
}

function savedRow(t) {
  let action = null;
  if (t.status === 'done') {
    action = h('button', { class: 'btn small', type: 'button', onclick: () => act('reveal', { key: t.key }) }, 'Show');
  } else if (t.status === 'error' || t.status === 'missing') {
    action = h('button', { class: 'btn small', type: 'button', onclick: () => act('saveNow', { key: t.key }, 'Trying again') },
      t.status === 'error' ? 'Retry' : 'Save again');
  }
  return h('li', { class: `item ${t.status}` },
    artFor(t),
    h('div', { class: 'info' },
      h('div', { class: 'title', title: t.title }, t.title || 'Untitled'),
      h('div', { class: 'sub' }, subline(t)),
      statusLine(t)),
    action && h('div', { class: 'actions' }, action));
}

function listeningRow(t) {
  const threshold = state.settings.threshold;
  const ignored = t.status === 'ignored';
  const actions = ignored
    ? [h('button', { class: 'btn small subtle', type: 'button', onclick: () => act('unignore', { key: t.key }) }, 'Undo')]
    : [
      h('button', { class: 'btn small subtle', type: 'button', title: 'Never save this song', onclick: () => act('ignore', { key: t.key }) }, 'Skip'),
      h('button', { class: 'btn small', type: 'button', onclick: () => act('saveNow', { key: t.key }, 'Saving it now') }, 'Save'),
    ];
  return h('li', { class: `item ${t.status}` },
    artFor(t),
    h('div', { class: 'info' },
      h('div', { class: 'title', title: t.title }, t.title || 'Untitled'),
      h('div', { class: 'sub' }, subline(t)),
      ignored ? h('div', { class: 'state' }, 'Won’t be saved') : meter(t.plays, threshold)),
    h('div', { class: 'actions' }, actions));
}

function emptyState(title, text) {
  return h('li', { class: 'empty' }, h('strong', null, title), text);
}

function renderLists() {
  const all = Object.values(state.tracks);
  const rank = (t) => (IN_FLIGHT.includes(t.status) ? 0 : t.status === 'error' ? 1 : 2);
  const saved = all
    .filter((t) => SAVED_SIDE.includes(t.status))
    .sort((a, b) => rank(a) - rank(b) || (b.savedAt || b.lastPlayed || 0) - (a.savedAt || a.lastPlayed || 0));
  const listening = all
    .filter((t) => t.status === 'counting' || t.status === 'ignored')
    .sort((a, b) => (b.lastPlayed || 0) - (a.lastPlayed || 0))
    .slice(0, 200);

  $('#count-saved').textContent = saved.length || '';
  $('#count-listening').textContent = listening.length || '';

  const n = state.settings.threshold;
  $('#tab-saved').replaceChildren(...(saved.length
    ? saved.map(savedRow)
    : [emptyState('Nothing saved yet', `Play a song ${n} ${n === 1 ? 'time' : 'times'} and it lands here as an MP3.`)]));
  $('#tab-listening').replaceChildren(...(listening.length
    ? listening.map(listeningRow)
    : [emptyState('No plays yet', 'Songs you listen to show up here with their play counts.')]));
}

function renderSettings() {
  const form = $('#tab-settings');
  for (const name of ['otherSites', 'youtubeMusicOnly', 'notify']) form.elements[name].checked = !!state.settings[name];
  for (const name of ['minTrackSec', 'maxTrackMin']) {
    const input = form.elements[name];
    if (document.activeElement !== input) input.value = state.settings[name];
  }
  const url = $('#companionUrl');
  if (document.activeElement !== url) url.value = state.settings.companionUrl;
  const folder = $('#folder');
  if (document.activeElement !== folder && state.companion && state.companion.downloadDir) {
    folder.value = state.companion.downloadDir;
  }
  folder.disabled = !companionOnline();
  $('#setFolder').disabled = !companionOnline();
  if (!companionOnline()) setHint('Start the companion app to change this.', false, 'offline');
  else if ($('#folderHint').dataset.kind === 'offline') setHint('');
}

function setHint(text, bad = false, kind = '') {
  const hint = $('#folderHint');
  hint.textContent = text;
  hint.classList.toggle('bad', bad);
  hint.dataset.kind = kind;
}

// ---- wiring -------------------------------------------------------------------

function showTab(tab) {
  state.tab = tab;
  for (const b of document.querySelectorAll('.tabs button')) b.setAttribute('aria-selected', String(b.dataset.tab === tab));
  $('#tab-saved').hidden = tab !== 'saved';
  $('#tab-listening').hidden = tab !== 'listening';
  $('#tab-settings').hidden = tab !== 'settings';
}

async function saveSettings(patch) {
  try {
    const res = await send('saveSettings', { patch });
    state.settings = res.settings;
    render();
  } catch (e) {
    toast(e.message);
    render();
  }
}

function wire() {
  for (const b of document.querySelectorAll('.tabs button')) b.addEventListener('click', () => showTab(b.dataset.tab));

  $('#enabled').addEventListener('change', (e) => saveSettings({ enabled: e.target.checked }));

  const step = (delta) => {
    const next = Math.min(100, Math.max(1, state.settings.threshold + delta));
    state.settings.threshold = next;
    renderTop();
    renderLists();
    saveSettings({ threshold: next });
  };
  $('#less').addEventListener('click', () => step(-1));
  $('#more').addEventListener('click', () => step(1));

  const form = $('#tab-settings');
  form.addEventListener('submit', (e) => e.preventDefault());
  for (const name of ['otherSites', 'youtubeMusicOnly', 'notify']) {
    form.elements[name].addEventListener('change', (e) => saveSettings({ [name]: e.target.checked }));
  }
  for (const name of ['minTrackSec', 'maxTrackMin']) {
    form.elements[name].addEventListener('change', (e) => saveSettings({ [name]: e.target.value }));
  }

  const saveFolder = async () => {
    const dir = $('#folder').value.trim();
    if (!dir) return;
    $('#setFolder').disabled = true;
    try {
      const res = await send('setFolder', { dir });
      setHint(`Saving to ${res.downloadDir}`);
    } catch (e) {
      setHint(e.message, true);
    } finally {
      $('#setFolder').disabled = !companionOnline();
    }
  };
  $('#setFolder').addEventListener('click', saveFolder);
  $('#folder').addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); saveFolder(); } });

  const saveUrl = () => saveSettings({ companionUrl: $('#companionUrl').value });
  $('#setUrl').addEventListener('click', saveUrl);
  $('#companionUrl').addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); saveUrl(); } });

  // Two clicks to clear, so a stray click can't wipe your play counts.
  let armTimer = 0;
  $('#clear').addEventListener('click', async (e) => {
    const btn = e.currentTarget;
    if (!btn.classList.contains('armed')) {
      btn.classList.add('armed');
      btn.textContent = 'Click again to clear play counts';
      armTimer = setTimeout(() => {
        btn.classList.remove('armed');
        btn.textContent = 'Clear listening history';
      }, 3000);
      return;
    }
    clearTimeout(armTimer);
    btn.classList.remove('armed');
    btn.textContent = 'Clear listening history';
    await act('clearHistory', null, 'Listening history cleared. Saved songs are kept.');
  });

  // Firefox asks for site access separately from installing.
  $('#grant').addEventListener('click', async () => {
    const granted = await chrome.permissions.request({ origins: ['<all_urls>'] });
    $('#permission').hidden = granted;
  });
}

async function load() {
  const local = await chrome.storage.local.get(['settings', 'tracks', 'companion']);
  const session = chrome.storage.session ? await chrome.storage.session.get('now') : {};
  state.settings = { ...ILUV.DEFAULTS, ...(local.settings || {}) };
  state.tracks = local.tracks || {};
  state.companion = local.companion || null;
  state.now = session.now || null;
  render();
}

chrome.storage.onChanged.addListener((changes, area) => {
  if (area === 'local') {
    if (changes.settings) state.settings = { ...ILUV.DEFAULTS, ...(changes.settings.newValue || {}) };
    if (changes.tracks) state.tracks = changes.tracks.newValue || {};
    if (changes.companion) state.companion = changes.companion.newValue || null;
  } else if (area === 'session' && changes.now) {
    state.now = changes.now.newValue || null;
  } else {
    return;
  }
  render();
});

wire();
load();
send('popupOpened').catch(() => {});
chrome.permissions.contains({ origins: ['<all_urls>'] }).then((ok) => { $('#permission').hidden = ok; }, () => {});
