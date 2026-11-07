# Store submission kit

Everything the Chrome Web Store, Firefox Add-ons and Edge Add-ons forms ask
for, ready to paste.

## Files to upload

| What | File | Used by |
| --- | --- | --- |
| Extension package | `dist/iluvmusic-1.0.0.zip` (run `python tools/package.py`) | all |
| Store icon, 128×128 | `store/icon-128.png` | Chrome |
| Logo, 300×300 | `store/logo-300.png` | Edge, Firefox |
| Screenshots, 1280×800 | `store/screenshot-1.png`, `-2`, `-3` | all |
| Small promo tile, 440×280 | `store/promo-440x280.png` | Chrome, Edge |

Privacy policy URL: https://github.com/satanrayshe/iluvmusic/blob/main/PRIVACY.md
Homepage / support URL: https://github.com/satanrayshe/iluvmusic
Support (issues) URL: https://github.com/satanrayshe/iluvmusic/issues

## Name

iluvmusic

## Summary (132 characters max)

Saves the songs you replay most as MP3s. Works on YouTube Music, YouTube, Spotify, SoundCloud and most sites that play audio.

## Description

Keep listening to music the way you already do. When you play a song enough times, iluvmusic saves it to your computer as an MP3, with its title, artist, album and cover art. You never have to download your playlist by hand.

How it works
• Play music on YouTube Music, YouTube, Spotify, SoundCloud or almost any site that plays audio.
• A play counts once you've heard half the song (or 4 minutes). Skipping ahead doesn't count; starting the song over counts again.
• When a song reaches your number (3 by default, and you can change it), it's saved to Music/iluvmusic.

Needs the free companion app
Browsers can't turn a stream into an MP3 on their own, so iluvmusic comes with a small companion app that runs on your computer and does the saving. Download it, with setup steps for Windows, Mac and Linux, from https://github.com/satanrayshe/iluvmusic. Until it's running, iluvmusic keeps counting, and saves everything that's due as soon as it starts.

What you can do from the popup
• See what's playing and how close it is to being saved
• Save a song right away, or skip one so it's never saved
• Choose your play count, your music folder, and the shortest and longest songs to count
• On YouTube, count only music, so vlogs and tutorials aren't saved

Spotify, Apple Music, Deezer and Tidal lock their audio, so for those iluvmusic finds the same song on YouTube Music by name and length, and saves that.

Not counted: ads, YouTube Shorts, podcasts, muted videos, live streams, and very short or very long tracks.

Private by design: no accounts, no analytics, no tracking. Your play counts stay in your browser, and songs are saved by the app on your own computer.

Only save music you have the right to keep.

## Category

- Chrome: Entertainment
- Edge: Entertainment
- Firefox: Photos, Music & Videos (second choice: Download Management)

## Chrome: Privacy practices tab

**Single purpose**

Counts how many times you play each song in your browser and, when a song reaches the number you set, hands it to the iluvmusic companion app on your computer to save it as an MP3.

**Permission justifications**

- `storage`: Keeps your play counts, your saved-song list and your settings on your computer.
- `alarms`: Checks with the companion app once a minute, so songs that became due while it was closed get saved, and clears out old play counts once a day.
- `notifications`: Tells you when a song has been saved. You can turn this off in Settings.
- Host permission (`<all_urls>`) and content scripts on all sites: Music plays on many different websites (YouTube Music, Spotify, SoundCloud, Bandcamp, embedded players), so the extension needs to notice what's playing on any page. It reads only the playing song's title, artist, album, cover art, length and progress. It also talks to the companion app at http://127.0.0.1.

**Remote code**

No, I am not using remote code.

**Data usage**

Tick:
- Website content
- Web history

Then tick all three statements:
- I do not sell or transfer user data to third parties, outside of the approved use cases.
- I do not use or transfer user data for purposes that are unrelated to my item's single purpose.
- I do not use or transfer user data to determine creditworthiness or for lending purposes.

(The extension only reads the playing song's details and the page address. They stay on the user's computer, so ticking these two is the cautious choice.)

## Firefox: Notes to reviewer

Also paste into Edge's "Notes for certification" box.

iluvmusic counts song plays and, when a song reaches the user's number, sends its details to a companion app on the user's own computer (http://127.0.0.1:7777). The app saves the song as an MP3. The extension itself never downloads anything, and sends nothing to any outside server.

The code is plain, unminified JavaScript with no build step; the zip is exactly the extension/ folder of https://github.com/satanrayshe/iluvmusic.

To test without the companion: play any song on YouTube Music or SoundCloud and open the popup. It shows the song under "Now playing" with a play-count meter, and the "Listening" tab lists it. The popup says the companion isn't running; counting still works.

To test with the companion: clone the repo and run companion/start-windows.bat (or companion/start-mac-linux.sh; needs Python 3.9+). Set "Save a song after" to 1 and play a song past its halfway point. It is saved to Music/iluvmusic.

On Firefox, host access is optional in Manifest V3, so the popup shows an "Allow access" button that requests it.

## Firefox: License

The repo has no license file yet, so pick **All Rights Reserved**. If you'd rather let people reuse the code, add a license to the repo first and pick the same one here.
