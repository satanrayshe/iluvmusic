# iluvmusic privacy policy

Last updated: 7 November 2026

iluvmusic has two parts: a browser extension and a companion app that runs
on your own computer. Neither part has accounts, analytics, ads or tracking.
Nothing about you or your listening is ever sent to the developer, and nothing
is sold or shared.

## What the extension reads

To count plays, the extension looks at audio and video playing on the pages
you visit. For the song that's playing, it reads:

- the title, artist and album
- the cover art address
- the page address
- the song's length and how far into it you are

It doesn't read anything else on the page: no forms, passwords, messages or
browsing history.

## Where it's kept

**Play counts and song details:**
- These stay in your browser's storage for the extension, on your computer.
- Songs that are still being counted are removed after 120 days without a play.

**Your settings:**
- Kept in the same browser storage, on your computer.

## What gets sent, and where

**To the companion app on your computer:**
- When a song reaches your play count, or you click Save, the extension sends
  its details to the companion app.
- The app listens only at `127.0.0.1` (your own machine). Nothing in this
  step leaves your computer.

**Connections the companion app makes to save the song:**
- **The song's source.** It downloads the audio from the site the song came
  from: YouTube, YouTube Music, SoundCloud or the page you played it on.
- **YouTube Music search, for locked services.** For Spotify, Apple Music and
  other services that lock their audio, it searches YouTube Music for the
  artist and title.
- **Cover art.** It downloads the cover image from the address the music site
  gave.
- **Updates.** Once a day it checks the Python Package Index (pypi.org) for a
  newer version of yt-dlp, the downloading tool it uses. If a YouTube helper
  component is missing, yt-dlp fetches it from GitHub.

These requests go straight to those sites, like any download you'd make
yourself, and carry no information about you.

**Thumbnails in the popup:**
- The popup loads cover images straight from the music site, the same way
  the site itself shows them.

## Files on your computer

The companion app saves:
- your MP3s, in `Music/iluvmusic` (or a folder you choose)
- a list of saved songs, in `~/.iluvmusic/library.json`
- its settings and log, in `~/.iluvmusic/`

## Deleting your data

- **Play counts:** Settings → **Clear listening history** in the popup
  removes the counts for songs that haven't been saved.
- **Everything the extension stored:** removing the extension deletes it all,
  including the list of saved songs.
- **Saved songs and the companion's files:** delete the `~/.iluvmusic` folder
  and your music folder.

## Contact

Questions or problems: <https://github.com/satanrayshe/iluvmusic/issues>
