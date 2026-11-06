// Runs in the extension's isolated world. Listens to what hook.js reports,
// decides when a song has really been listened to, and tells the background
// worker. A "play" counts once you've heard half the song (or 4 minutes);
// starting it again from the top counts as a new play.
(() => {
  'use strict';
  if (globalThis.__iluvmusicBridge) return;
  globalThis.__iluvmusicBridge = true;

  const settings = { ...ILUV.DEFAULTS };
  chrome.storage.local.get('settings').then((r) => Object.assign(settings, r.settings || {}), () => {});
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area === 'local' && changes.settings) Object.assign(settings, ILUV.DEFAULTS, changes.settings.newValue || {});
  });

  let session = null; // the song currently being listened to in this frame
  let alive = true;

  window.addEventListener('iluvmusic:state', (e) => {
    if (!alive || typeof e.detail !== 'string') return;
    let state;
    try {
      state = JSON.parse(e.detail);
    } catch (_) {
      return;
    }
    if (state && typeof state === 'object') onState(state);
  });

  function send(message) {
    try {
      chrome.runtime.sendMessage(message).catch(onSendError);
    } catch (err) {
      onSendError(err);
    }
  }

  function onSendError(err) {
    // The extension was reloaded or removed; this old copy should go quiet.
    if (/context invalidated/i.test(String(err && err.message))) alive = false;
  }

  const norm = (s) => String(s || '').toLowerCase().replace(/\s+/g, ' ').trim();

  function keyFor(s) {
    switch (s.site) {
      case 'youtube':
      case 'ytmusic':
        return s.id ? `yt:${s.id}` : '';
      case 'spotify':
        return s.id ? `spotify:${s.id}` : s.title ? `spotify:${norm(s.artist)}|${norm(s.title)}` : '';
      case 'soundcloud':
        return s.id ? `sc:${s.id}` : s.title ? `sc:${norm(s.artist)}|${norm(s.title)}` : '';
      default:
        return s.title ? `web:${s.host}|${norm(s.artist)}|${norm(s.title)}` : '';
    }
  }

  function whyNotCounted(s) {
    if (s.skip) return s.skip;
    if (s.site === 'web' && !settings.otherSites) return 'other-sites-off';
    if (s.site === 'youtube' && settings.youtubeMusicOnly && s.music === false) return 'not-music';
    const min = Math.max(Number(settings.minTrackSec) || 0, s.minDuration || 0);
    if (s.d > 0) {
      if (s.d < min) return 'too-short';
      if (s.d > (Number(settings.maxTrackMin) || 15) * 60) return 'too-long';
    } else if (s.site === 'web') {
      return 'unknown-length'; // live streams, calls, broken players
    }
    return '';
  }

  function toTrack(s, key) {
    return {
      key,
      site: s.site,
      host: s.host,
      title: s.title,
      artist: s.artist,
      album: s.album,
      artwork: s.artwork,
      url: s.url,
      mode: s.mode,
      duration: s.d > 0 ? Math.round(s.d) : 0,
      cleanMeta: s.cleanMeta,
    };
  }

  function onState(s) {
    const now = performance.now();
    if (!settings.enabled || !s.playing) {
      if (session && session.wall !== null) {
        session.wall = null; // paused: stop the clock, keep progress
        send({ type: 'stopped', key: session.key });
      }
      return;
    }

    const key = keyFor(s);
    if (!key) return;
    const reason = whyNotCounted(s);
    const track = toTrack(s, key);

    // A new song, or back at the start after getting well into this one (a replay).
    if (!session || session.key !== key || (s.t < 10 && session.maxT - s.t > 20)) {
      session = { key, listened: 0, counted: false, lastT: s.t, maxT: s.t, wall: now, reason, announced: now };
      send({ type: 'now', track, reason });
      return;
    }

    if (session.wall !== null) {
      // Count only time actually heard: ignore jumps forward (seeking) and
      // anything faster than the clock allows.
      const wallSec = (now - session.wall) / 1000;
      let heard = s.t - session.lastT;
      if (!(heard >= 0) || heard > wallSec * (s.rate || 1) + 1.5) heard = 0;
      session.listened += heard;
    } else {
      session.announced = -Infinity; // resumed after a pause: show it as playing again
    }
    session.lastT = s.t;
    session.maxT = Math.max(session.maxT, s.t);
    session.wall = now;

    // Re-announce on changes, and every 45s as a heartbeat so the popup can
    // tell a song that's still playing from one whose tab is long gone.
    if (reason !== session.reason || now - session.announced > 45000) {
      session.reason = reason;
      session.announced = now;
      send({ type: 'now', track, reason });
    }
    if (!reason && !session.counted && session.listened >= ILUV.listenNeeded(s.d)) {
      session.counted = true;
      send({ type: 'play', track });
    }
  }
})();
