// Runs inside the page's own JavaScript (world: MAIN) so it can see audio that
// sites keep off-page (Spotify, SoundCloud) and read player data (YouTube).
// It only reports what is playing, once a second, to bridge.js through a DOM
// event. All counting and decisions happen in bridge.js and background.js.
(() => {
  'use strict';
  if (window.__iluvmusicHooked) return;
  try {
    Object.defineProperty(window, '__iluvmusicHooked', { value: true });
  } catch (_) {
    return;
  }

  const EVENT = 'iluvmusic:state';
  const tracked = new Set();
  let timer = 0;
  let lastIdle = '';

  // Catch every media element the page plays, including ones never attached
  // to the document (Spotify and SoundCloud create theirs off-page).
  const nativePlay = HTMLMediaElement.prototype.play;
  HTMLMediaElement.prototype.play = function play() {
    try {
      tracked.add(this);
      start();
    } catch (_) {}
    return nativePlay.apply(this, arguments);
  };
  document.addEventListener('play', (e) => {
    if (e.target instanceof HTMLMediaElement) {
      tracked.add(e.target);
      start();
    }
  }, true);

  function start() {
    if (!timer) timer = setInterval(tick, 1000);
  }

  function tick() {
    let state;
    try {
      state = snapshot();
    } catch (_) {
      state = { playing: false };
    }
    const json = JSON.stringify(state);
    if (!state.playing) {
      if (json === lastIdle) return;
      lastIdle = json;
    } else {
      lastIdle = '';
    }
    window.dispatchEvent(new CustomEvent(EVENT, { detail: json }));
  }

  function activeMedia() {
    const all = new Set(tracked);
    document.querySelectorAll('audio, video').forEach((el) => all.add(el));
    let best = null;
    for (const el of all) {
      if (!el.isConnected && el.paused) {
        tracked.delete(el); // forget finished off-page players
        continue;
      }
      if (el.paused || el.ended || el.readyState < 2) continue;
      if (el.muted || el.volume === 0) continue; // muted previews and autoplay ads
      if (!best || length(el) > length(best)) best = el;
    }
    return best;
  }

  const length = (el) => (Number.isFinite(el.duration) ? el.duration : 0);

  function sessionMeta() {
    const m = navigator.mediaSession && navigator.mediaSession.metadata;
    if (!m) return { title: '', artist: '', album: '', artwork: '' };
    let artwork = '';
    try {
      let bestSize = -1;
      for (const a of Array.from(m.artwork || [])) {
        const size = Math.max(0, ...String(a.sizes || '').split(/\s+/).map((s) => parseInt(s, 10) || 0));
        if (size >= bestSize) {
          bestSize = size;
          artwork = a.src;
        }
      }
    } catch (_) {}
    return { title: m.title || '', artist: m.artist || '', album: m.album || '', artwork };
  }

  function snapshot() {
    const el = activeMedia();
    if (!el) return { playing: false };
    const meta = sessionMeta();
    const info = adapterFor(location.hostname)(meta) || {};
    return {
      playing: true,
      t: el.currentTime || 0,
      d: length(el),
      rate: el.playbackRate || 1,
      host: location.hostname,
      site: info.site || 'web',
      id: info.id || '',
      url: info.url || '',
      mode: info.mode || 'url',
      title: tidy(info.title || meta.title),
      artist: tidy(info.artist || meta.artist),
      album: tidy(info.album || meta.album),
      artwork: absolute(meta.artwork),
      music: typeof info.music === 'boolean' ? info.music : null,
      cleanMeta: !!info.cleanMeta,
      minDuration: info.minDuration || 0,
      skip: info.skip || '',
    };
  }

  const tidy = (s) => String(s || '').replace(/\s+/g, ' ').trim().slice(0, 300);

  function absolute(src) {
    try {
      return src ? new URL(src, location.href).href : '';
    } catch (_) {
      return '';
    }
  }

  function pageUrl() {
    const u = new URL(location.href);
    u.hash = '';
    return u.href;
  }

  // ---- per-site details ------------------------------------------------

  // Services whose audio is DRM-protected. We can't take the stream itself,
  // so the companion finds the same song on YouTube Music by name.
  const SEARCH_ONLY = /(^|\.)(music\.apple\.com|deezer\.com|tidal\.com|music\.amazon\.[a-z.]+|pandora\.com|qobuz\.com|napster\.com|anghami\.com)$/;

  function adapterFor(host) {
    if (host === 'music.youtube.com') return ytMusic;
    if (/(^|\.)youtube(-nocookie)?\.com$/.test(host)) return youtube;
    if (host === 'open.spotify.com') return spotify;
    if (host === 'soundcloud.com' || host === 'm.soundcloud.com') return soundcloud;
    return generic;
  }

  const ytPlayer = () => document.getElementById('movie_player') || document.querySelector('.html5-video-player');
  const channelName = (s) => String(s || '').replace(/\s*-\s*Topic$/i, '');
  const call = (obj, fn) => {
    try {
      return obj && typeof obj[fn] === 'function' ? obj[fn]() : null;
    } catch (_) {
      return null;
    }
  };

  function youtube(meta) {
    if (location.pathname.startsWith('/shorts/')) return { site: 'youtube', skip: 'shorts' };
    const player = ytPlayer();
    if (player && player.classList.contains('ad-showing')) return { site: 'youtube', skip: 'ad' };
    const data = call(player, 'getVideoData');
    const resp = call(player, 'getPlayerResponse');
    const id = (data && data.video_id)
      || new URLSearchParams(location.search).get('v')
      || (location.pathname.match(/^\/(?:embed|live|v)\/([\w-]{11})/) || [])[1]
      || '';
    const details = resp && resp.videoDetails && resp.videoDetails.videoId === id ? resp.videoDetails : null;
    const micro = details && resp.microformat && resp.microformat.playerMicroformatRenderer;
    let music = null;
    if (micro && micro.category) music = micro.category === 'Music';
    if (details && details.musicVideoType) music = true;
    return {
      site: 'youtube',
      id,
      url: id ? `https://www.youtube.com/watch?v=${id}` : '',
      mode: 'url',
      title: (details && details.title) || (data && data.title) || meta.title,
      artist: channelName((details && details.author) || (data && data.author) || meta.artist),
      album: '',
      music,
    };
  }

  function ytMusic(meta) {
    const player = ytPlayer();
    if (player && player.classList.contains('ad-showing')) return { site: 'ytmusic', skip: 'ad' };
    const data = call(player, 'getVideoData');
    const id = (data && data.video_id) || new URLSearchParams(location.search).get('v') || '';
    return {
      site: 'ytmusic',
      id,
      url: id ? `https://music.youtube.com/watch?v=${id}` : '',
      mode: 'url',
      title: meta.title || (data && data.title),
      artist: meta.artist || channelName(data && data.author),
      album: meta.album,
      music: true,
      cleanMeta: true,
    };
  }

  function spotify(meta) {
    const bar = document.querySelector('[data-testid="now-playing-widget"]')
      || document.querySelector('[data-testid="now-playing-bar"]')
      || document.querySelector('footer');
    let id = '';
    if (bar) {
      for (const a of bar.querySelectorAll('a[href]')) {
        let href = a.getAttribute('href') || '';
        try {
          href = decodeURIComponent(href);
        } catch (_) {}
        if (/\/episode\/|spotify:episode:/.test(href)) return { site: 'spotify', skip: 'podcast' };
        const m = href.match(/track[/:]([A-Za-z0-9]{22})/);
        if (m) {
          id = m[1];
          break;
        }
      }
    }
    if (!id && /^(spotify|advertisement)$/i.test(String(meta.artist).trim())) return { site: 'spotify', skip: 'ad' };
    return {
      site: 'spotify',
      id,
      url: id ? `https://open.spotify.com/track/${id}` : '',
      mode: 'search',
      music: true,
      cleanMeta: true,
      // Without a track link we can't rule out an ad, so only trust full-length songs.
      minDuration: id ? 0 : 60,
    };
  }

  function soundcloud(meta) {
    const link = document.querySelector('.playbackSoundBadge__titleLink');
    const who = document.querySelector('.playbackSoundBadge__lightLink');
    let url = '';
    if (link) {
      try {
        const u = new URL(link.getAttribute('href'), location.origin);
        url = u.origin + u.pathname;
      } catch (_) {}
    }
    return {
      site: 'soundcloud',
      id: url ? new URL(url).pathname : '',
      url,
      mode: url ? 'url' : 'search',
      title: meta.title || (link && (link.getAttribute('title') || link.textContent)),
      artist: meta.artist || (who && (who.getAttribute('title') || who.textContent)),
      music: true,
      cleanMeta: true,
    };
  }

  function generic(meta) {
    const searchOnly = SEARCH_ONLY.test(location.hostname);
    return {
      site: 'web',
      url: searchOnly ? '' : pageUrl(),
      mode: searchOnly ? 'search' : 'url',
      title: meta.title || document.title,
      artist: meta.artist,
      album: meta.album,
      cleanMeta: !!meta.artist,
    };
  }
})();
