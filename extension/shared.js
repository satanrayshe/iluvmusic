// Shared by the background worker, the content script and the popup.
// Loaded with importScripts() in Chrome's service worker and as a plain
// script everywhere else, so it only defines a global.
var ILUV = Object.freeze({
  DEFAULTS: Object.freeze({
    enabled: true,            // master switch
    threshold: 3,             // save a song once it has been played this many times
    otherSites: true,         // count audio on sites other than the big four
    youtubeMusicOnly: true,   // on youtube.com, skip videos YouTube doesn't file under Music
    minTrackSec: 30,          // ignore clips shorter than this
    maxTrackMin: 15,          // ignore anything longer than this (podcasts, mixes, films)
    notify: true,             // show a notification when a song is saved
    companionUrl: 'http://127.0.0.1:7777',
  }),

  SITE_NAMES: Object.freeze({
    youtube: 'YouTube',
    ytmusic: 'YouTube Music',
    spotify: 'Spotify',
    soundcloud: 'SoundCloud',
  }),

  // A play counts once you've heard half the song, or four minutes of it,
  // whichever comes first (the same rule Last.fm uses for scrobbles).
  listenNeeded(durationSec) {
    return durationSec > 0 && Number.isFinite(durationSec) ? Math.min(durationSec * 0.5, 240) : 60;
  },

  siteLabel(track) {
    if (!track) return '';
    return ILUV.SITE_NAMES[track.site] || String(track.host || 'Web').replace(/^www\./, '');
  },
});
