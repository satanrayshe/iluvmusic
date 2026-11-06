// Keeps play counts, decides when a song is due, and hands it to the
// companion app, which does the actual MP3 conversion. Runs as a service
// worker in Chromium browsers and as an event page in Firefox and Safari.
'use strict';
if (typeof importScripts === 'function' && typeof ILUV === 'undefined') importScripts('shared.js');

const IN_FLIGHT = new Set(['pending', 'queued', 'downloading']);
const COUNTING_KEEP_DAYS = 120;
const SYNC_ALARM = 'iluvmusic-sync';
const PRUNE_ALARM = 'iluvmusic-prune';

// ---- storage ---------------------------------------------------------------

async function getSettings() {
  const { settings } = await chrome.storage.local.get('settings');
  return { ...ILUV.DEFAULTS, ...(settings || {}) };
}

// Every change to the track list goes through this queue so two quick
// messages can't overwrite each other's updates.
let trackQueue = Promise.resolve();
function withTracks(fn) {
  const run = trackQueue.then(async () => {
    const { tracks = {} } = await chrome.storage.local.get('tracks');
    const result = await fn(tracks);
    await chrome.storage.local.set({ tracks });
    return result;
  });
  trackQueue = run.catch(() => {});
  return run;
}

const clip = (v, n) => (typeof v === 'string' ? v.slice(0, n) : '');

// Only keep known fields, and only plain values, from what pages report.
function cleanTrack(t) {
  if (!t || typeof t !== 'object' || typeof t.key !== 'string' || !t.key) return null;
  return {
    key: t.key.slice(0, 512),
    site: clip(t.site, 40) || 'web',
    host: clip(t.host, 255),
    title: clip(t.title, 300),
    artist: clip(t.artist, 300),
    album: clip(t.album, 300),
    artwork: /^https:\/\//.test(t.artwork || '') ? clip(t.artwork, 2048) : '',
    url: /^https?:\/\//.test(t.url || '') ? clip(t.url, 2048) : '',
    mode: t.mode === 'search' ? 'search' : 'url',
    duration: Number.isFinite(t.duration) && t.duration > 0 ? Math.round(t.duration) : 0,
    cleanMeta: !!t.cleanMeta,
  };
}

function newRecord(track, now) {
  return { ...track, plays: 0, firstPlayed: now, lastPlayed: now, status: 'counting', error: '', file: '' };
}

// ---- talking to the companion ---------------------------------------------

async function api(settings, method, path, body) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 8000);
  try {
    const res = await fetch(settings.companionUrl.replace(/\/+$/, '') + path, {
      method,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
      signal: ctrl.signal,
      cache: 'no-store',
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const err = new Error(data.error || `The companion answered ${res.status}`);
      err.status = res.status;
      throw err;
    }
    return data;
  } finally {
    clearTimeout(timer);
  }
}

async function setCompanion(state) {
  await chrome.storage.local.set({ companion: { ...state, checkedAt: Date.now() } });
}

function downloadRequest(t) {
  return {
    key: t.key,
    site: t.site,
    url: t.url,
    mode: t.mode,
    title: t.title,
    artist: t.artist,
    album: t.album,
    artwork: t.artwork,
    duration: t.duration,
    cleanMeta: t.cleanMeta,
  };
}

// Copy the companion's view of a song onto our record.
function adopt(t, remote, justSaved) {
  const before = t.status;
  t.status = remote.status;
  t.file = remote.file || t.file || '';
  t.error = remote.error || '';
  if (t.status === 'done' && before !== 'done') {
    t.savedAt = Date.now();
    if (before !== 'missing') justSaved.push({ key: t.key, title: t.title, artist: t.artist });
  }
}

let syncing = null;
let resyncTimer = 0;

function sync(force = false) {
  if (!syncing) syncing = doSync(force).finally(() => { syncing = null; });
  return syncing;
}

async function doSync(force) {
  const settings = await getSettings();
  const { tracks = {} } = await chrome.storage.local.get('tracks');
  const hasWork = Object.values(tracks).some((t) => IN_FLIGHT.has(t.status));
  if (!hasWork && !force) return;

  let health, library;
  try {
    [health, library] = await Promise.all([
      api(settings, 'GET', '/health'),
      api(settings, 'GET', '/library'),
    ]);
  } catch (e) {
    await setCompanion({ online: false, error: e.name === 'AbortError' ? 'No answer' : String(e.message || e) });
    return;
  }
  await setCompanion({
    online: true,
    version: health.version,
    downloadDir: health.downloadDir,
    ffmpeg: !!health.ffmpeg,
    jsRuntime: health.jsRuntime || '',
    ytdlp: health.ytdlp || '',
  });

  const remote = new Map((library.items || []).map((i) => [i.key, i]));
  const justSaved = [];
  const toSubmit = [];

  await withTracks((all) => {
    for (const t of Object.values(all)) {
      const r = remote.get(t.key);
      if (t.status === 'pending') {
        if (r && (r.status === 'queued' || r.status === 'downloading' || r.status === 'done')) adopt(t, r, justSaved);
        else toSubmit.push(downloadRequest(t));
      } else if (t.status === 'queued' || t.status === 'downloading') {
        if (r) adopt(t, r, justSaved);
        else {
          t.status = 'pending'; // the companion lost it (e.g. its library was reset)
          toSubmit.push(downloadRequest(t));
        }
      } else if ((t.status === 'done' || t.status === 'missing') && r && (r.status === 'done' || r.status === 'missing')) {
        adopt(t, r, justSaved);
      }
    }
  });

  for (const req of toSubmit) {
    try {
      const r = await api(settings, 'POST', '/download', req);
      await withTracks((all) => { if (all[req.key]) adopt(all[req.key], r, justSaved); });
    } catch (e) {
      if (e.status) {
        await withTracks((all) => {
          if (all[req.key]) Object.assign(all[req.key], { status: 'error', error: String(e.message || e) });
        });
      } else {
        break; // companion went away; try again on the next sync
      }
    }
  }

  await announce(justSaved, settings);

  const { tracks: after = {} } = await chrome.storage.local.get('tracks');
  if (Object.values(after).some((t) => t.status === 'queued' || t.status === 'downloading')) {
    clearTimeout(resyncTimer);
    resyncTimer = setTimeout(() => sync(), 2500);
  }
}

// ---- telling the user ------------------------------------------------------

async function setBadge(count) {
  await chrome.action.setBadgeBackgroundColor({ color: '#d6336c' });
  await chrome.action.setBadgeText({ text: count > 0 ? String(Math.min(count, 99)) : '' });
}

async function announce(saved, settings) {
  if (!saved.length) return;
  const { unseen = 0 } = await chrome.storage.local.get('unseen');
  await chrome.storage.local.set({ unseen: unseen + saved.length });
  await setBadge(unseen + saved.length);
  if (!settings.notify) return;
  const name = (s) => (s.artist ? `${s.title} by ${s.artist}` : s.title);
  const message = saved.length === 1 ? name(saved[0]) : `${name(saved[0])} and ${saved.length - 1} more`;
  try {
    await chrome.notifications.create(`saved:${saved[0].key}`, {
      type: 'basic',
      iconUrl: chrome.runtime.getURL('icons/icon-128.png'),
      title: 'Saved to your music folder',
      message,
    });
  } catch (_) {}
}

chrome.notifications.onClicked.addListener(async (id) => {
  if (!id.startsWith('saved:')) return;
  chrome.notifications.clear(id);
  try {
    await api(await getSettings(), 'POST', '/reveal', { key: id.slice('saved:'.length) });
  } catch (_) {}
});

// ---- events from pages ------------------------------------------------------

async function recordPlay(raw) {
  const track = cleanTrack(raw);
  if (!track) return;
  const settings = await getSettings();
  if (!settings.enabled) return;
  const now = Date.now();
  const due = await withTracks((all) => {
    const t = all[track.key] || (all[track.key] = newRecord(track, now));
    Object.assign(t, track);
    t.plays += 1;
    t.lastPlayed = now;
    if (t.status === 'counting' && t.plays >= settings.threshold) {
      t.status = 'pending';
      return true;
    }
    return false;
  });
  if (due) await sync();
}

async function setNowPlaying(raw, reason, sender) {
  const track = cleanTrack(raw);
  if (!track || !chrome.storage.session) return;
  await chrome.storage.session.set({
    now: { ...track, reason: clip(reason, 40), tabId: sender.tab ? sender.tab.id : -1, at: Date.now() },
  });
}

async function clearNowPlaying(key, sender) {
  if (!chrome.storage.session) return;
  const { now } = await chrome.storage.session.get('now');
  if (now && now.key === key && now.tabId === (sender.tab ? sender.tab.id : -1)) {
    await chrome.storage.session.remove('now');
  }
}

chrome.tabs.onRemoved.addListener(async (tabId) => {
  if (!chrome.storage.session) return;
  const { now } = await chrome.storage.session.get('now');
  if (now && now.tabId === tabId) await chrome.storage.session.remove('now');
});

// ---- requests from the popup ------------------------------------------------

async function markDue(key, track) {
  const now = Date.now();
  await withTracks((all) => {
    let t = all[key];
    if (!t) {
      const clean = cleanTrack(track);
      if (!clean || clean.key !== key) throw new Error('Unknown song');
      t = all[key] = newRecord(clean, now);
    }
    t.status = 'pending';
    t.error = '';
  });
  await sync(true);
}

async function setStatus(key, status) {
  const settings = await getSettings();
  await withTracks((all) => {
    const t = all[key];
    if (!t) return;
    t.status = status === 'counting' && t.plays >= settings.threshold && settings.enabled ? 'pending' : status;
  });
  await sync();
}

async function saveSettings(patch) {
  const current = await getSettings();
  const next = { ...current };
  const num = (v, lo, hi) => Math.min(hi, Math.max(lo, Math.round(Number(v))));
  if ('enabled' in patch) next.enabled = !!patch.enabled;
  if ('threshold' in patch && Number.isFinite(Number(patch.threshold))) next.threshold = num(patch.threshold, 1, 100);
  if ('otherSites' in patch) next.otherSites = !!patch.otherSites;
  if ('youtubeMusicOnly' in patch) next.youtubeMusicOnly = !!patch.youtubeMusicOnly;
  if ('notify' in patch) next.notify = !!patch.notify;
  if ('minTrackSec' in patch && Number.isFinite(Number(patch.minTrackSec))) next.minTrackSec = num(patch.minTrackSec, 0, 600);
  if ('maxTrackMin' in patch && Number.isFinite(Number(patch.maxTrackMin))) next.maxTrackMin = num(patch.maxTrackMin, 1, 180);
  if ('companionUrl' in patch) {
    const url = String(patch.companionUrl || '').trim().replace(/\/+$/, '');
    if (!/^http:\/\/(127\.0\.0\.1|localhost|\[::1\]):\d{2,5}$/.test(url)) {
      throw new Error('Use an address like http://127.0.0.1:7777');
    }
    next.companionUrl = url;
  }
  await chrome.storage.local.set({ settings: next });

  // Lowering the bar (or switching back on) can make songs due right away.
  if (next.enabled) {
    const due = await withTracks((all) => {
      let any = false;
      for (const t of Object.values(all)) {
        if (t.status === 'counting' && t.plays >= next.threshold) {
          t.status = 'pending';
          any = true;
        }
      }
      return any;
    });
    if (due || next.companionUrl !== current.companionUrl) await sync(true);
  }
  return next;
}

async function handle(msg, sender) {
  // Content scripts report the page's URL; extension pages report our own.
  const fromPage = !String(sender.url || '').startsWith(chrome.runtime.getURL(''));
  switch (msg && msg.type) {
    case 'play':
      return recordPlay(msg.track);
    case 'now':
      return setNowPlaying(msg.track, msg.reason, sender);
    case 'stopped':
      return clearNowPlaying(msg.key, sender);
  }
  // Everything below is for the popup only, never for web pages.
  if (fromPage || sender.id !== chrome.runtime.id) throw new Error('Not allowed');
  const settings = await getSettings();
  switch (msg.type) {
    case 'popupOpened':
      await chrome.storage.local.set({ unseen: 0 });
      await setBadge(0);
      sync(true);
      return {};
    case 'refresh':
      await sync(true);
      return {};
    case 'saveNow':
      return markDue(msg.key, msg.track);
    case 'ignore':
      return setStatus(msg.key, 'ignored');
    case 'unignore':
      return setStatus(msg.key, 'counting');
    case 'forget':
      return withTracks((all) => { delete all[msg.key]; });
    case 'clearHistory':
      return withTracks((all) => {
        for (const [k, t] of Object.entries(all)) if (t.status === 'counting' || t.status === 'ignored') delete all[k];
      });
    case 'reveal':
      return api(settings, 'POST', '/reveal', { key: msg.key || '' });
    case 'setFolder': {
      const h = await api(settings, 'POST', '/config', { downloadDir: String(msg.dir || '') });
      await sync(true);
      return { downloadDir: h.downloadDir };
    }
    case 'saveSettings':
      return { settings: await saveSettings(msg.patch || {}) };
    default:
      throw new Error('Unknown request');
  }
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  handle(msg, sender).then(
    (result) => sendResponse({ ok: true, ...(result || {}) }),
    (err) => sendResponse({ ok: false, error: String((err && err.message) || err) }),
  );
  return true;
});

// ---- housekeeping -----------------------------------------------------------

async function prune() {
  const cutoff = Date.now() - COUNTING_KEEP_DAYS * 24 * 3600 * 1000;
  await withTracks((all) => {
    for (const [k, t] of Object.entries(all)) {
      if (t.status === 'counting' && (t.lastPlayed || 0) < cutoff) delete all[k];
    }
  });
}

async function ensureAlarms() {
  if (!(await chrome.alarms.get(SYNC_ALARM))) chrome.alarms.create(SYNC_ALARM, { periodInMinutes: 1 });
  if (!(await chrome.alarms.get(PRUNE_ALARM))) chrome.alarms.create(PRUNE_ALARM, { periodInMinutes: 24 * 60 });
}

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === SYNC_ALARM) sync();
  if (alarm.name === PRUNE_ALARM) prune();
});

chrome.runtime.onInstalled.addListener(async () => {
  const { settings } = await chrome.storage.local.get('settings');
  await chrome.storage.local.set({ settings: { ...ILUV.DEFAULTS, ...(settings || {}) } });
  await ensureAlarms();
  sync(true);
});

chrome.runtime.onStartup.addListener(() => {
  ensureAlarms();
  sync(true);
});

ensureAlarms();
