"""End-to-end test: real extension in Chromium + real companion + local test songs.

    python tests/e2e_test.py [--keep] [--shots DIR]

Needs: Playwright for Python with Chromium (pip install playwright && playwright
install chromium), ffmpeg on PATH (only to make the test tones), and the
companion's .venv (run companion/start-windows.bat or start-mac-linux.sh once).

What it checks:
  * plays are counted for an <audio> tag and for an off-page Audio() object
  * replaying a looping song counts again
  * after `threshold` plays the song is sent to the companion and saved as MP3
  * the MP3 has the page's title/artist in its tags
  * the companion's bundled ffmpeg fallback works (system ffmpeg hidden)
  * the popup renders (screenshots in light and dark)
"""
import argparse
import functools
import http.server
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
EXT = ROOT / 'extension'
COMPANION = ROOT / 'companion'
VENV_PY = COMPANION / ('.venv/Scripts/python.exe' if os.name == 'nt' else '.venv/bin/python')

PAGE_INLINE = """<!doctype html><title>Inline test</title>
<audio id="a" src="tone-a.mp3" loop></audio>
<script>
navigator.mediaSession.metadata = new MediaMetadata({title: 'Sine Song', artist: 'Test Artist', album: 'Test Album'});
const a = document.getElementById('a');
a.playbackRate = 4;
a.play();
</script>"""

PAGE_DETACHED = """<!doctype html><title>Detached test</title>
<script>
navigator.mediaSession.metadata = new MediaMetadata({title: 'Hidden Player Song', artist: 'Test Artist'});
const a = new Audio('tone-b.mp3');
a.loop = true;
a.playbackRate = 4;
a.play();
</script>"""



class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def env_without_ffmpeg():
    env = dict(os.environ)
    parts = env.get('PATH', '').split(os.pathsep)
    while True:
        found = shutil.which('ffmpeg', path=os.pathsep.join(parts))
        if not found:
            break
        parts = [p for p in parts if Path(p).resolve() != Path(found).resolve().parent]
    env['PATH'] = os.pathsep.join(parts)
    return env


def wait_for(fn, timeout, what):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        last = fn()
        if last:
            return last
        time.sleep(1)
    raise AssertionError(f'Timed out waiting for {what}. Last value: {last!r}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--keep', action='store_true', help='keep the temp folder')
    ap.add_argument('--shots', help='folder to write popup screenshots to')
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix='iluvmusic-e2e-'))
    site, home, music, profile = (tmp / n for n in ('site', 'home', 'music', 'profile'))
    site.mkdir()
    print('temp folder:', tmp)

    for name, freq in (('tone-a.mp3', 440), ('tone-b.mp3', 660)):
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', f'sine=frequency={freq}:duration=40',
                        '-q:a', '6', str(site / name)], check=True)
    (site / 'inline.html').write_text(PAGE_INLINE, encoding='utf-8')
    (site / 'detached.html').write_text(PAGE_DETACHED, encoding='utf-8')

    web_port, api_port = free_port(), free_port()
    handler = functools.partial(QuietHandler, directory=str(site))
    web = http.server.ThreadingHTTPServer(('127.0.0.1', web_port), handler)
    threading.Thread(target=web.serve_forever, daemon=True).start()

    comp_env = env_without_ffmpeg()
    comp_env['ILUVMUSIC_HOME'] = str(home)
    comp = subprocess.Popen([str(VENV_PY), str(COMPANION / 'iluvmusic_companion.py'), '--dir', str(music),
                             '--port', str(api_port), '--no-update'],
                            env=comp_env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    def companion_health():
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{api_port}/health', timeout=2) as r:
                return json.loads(r.read())
        except Exception:
            return None

    failures = []
    try:
        health = wait_for(companion_health, 30, 'companion to start')
        print('companion up; ffmpeg found:', health['ffmpeg'])

        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                str(profile), channel='chromium', headless=True,
                args=[f'--disable-extensions-except={EXT}', f'--load-extension={EXT}',
                      '--autoplay-policy=no-user-gesture-required'])
            sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event('serviceworker')
            ext_id = sw.url.split('/')[2]
            print('extension id:', ext_id)
            sw.evaluate("""async (url) => {
                await chrome.storage.local.set({settings: {...ILUV.DEFAULTS, threshold: 3, companionUrl: url}});
            }""", f'http://127.0.0.1:{api_port}')

            inline = ctx.new_page()
            inline.goto(f'http://127.0.0.1:{web_port}/inline.html')
            detached = ctx.new_page()
            detached.goto(f'http://127.0.0.1:{web_port}/detached.html')

            get_tracks = lambda: sw.evaluate("async () => (await chrome.storage.local.get('tracks')).tracks || {}")

            def find(tracks, title):
                return next((t for t in tracks.values() if t.get('title') == title), None)

            # The off-page player: one counted play is enough to prove detection.
            t = wait_for(lambda: find(get_tracks(), 'Hidden Player Song'), 30, 'off-page player to be counted')
            print('off-page player counted:', t['plays'], 'play(s), key', t['key'])
            detached.close()

            now = sw.evaluate("async () => (await chrome.storage.session.get('now')).now || null")
            print('now playing record:', now and now.get('title'))

            # The <audio> tag loops; each loop is a replay. Three plays -> saved.
            def saved():
                t = find(get_tracks(), 'Sine Song')
                return t if t and t['status'] in ('done', 'error') else None
            t = wait_for(saved, 120, 'Sine Song to be saved')
            print('Sine Song:', t['status'], '| plays', t['plays'], '|', t.get('file') or t.get('error'))
            if t['status'] != 'done':
                failures.append(f"Sine Song ended as {t['status']}: {t.get('error')}")
            elif t['plays'] < 3:
                failures.append(f"Sine Song saved after only {t['plays']} plays")
            inline.close()

            hidden = find(get_tracks(), 'Hidden Player Song')
            if not hidden or hidden['status'] != 'counting':
                failures.append(f'Hidden Player Song should still be counting: {hidden}')

            if args.shots:
                shots = Path(args.shots)
                shots.mkdir(parents=True, exist_ok=True)
                popup = ctx.new_page()
                popup.set_viewport_size({'width': 360, 'height': 600})
                popup.goto(f'chrome-extension://{ext_id}/popup/popup.html')
                popup.wait_for_selector('.item')
                time.sleep(1)
                popup.screenshot(path=str(shots / 'popup-saved-light.png'), full_page=True)
                popup.click('button[data-tab="listening"]')
                popup.screenshot(path=str(shots / 'popup-listening-light.png'), full_page=True)
                popup.emulate_media(color_scheme='dark')
                popup.click('button[data-tab="settings"]')
                popup.screenshot(path=str(shots / 'popup-settings-dark.png'), full_page=True)
                popup.click('button[data-tab="saved"]')
                popup.screenshot(path=str(shots / 'popup-saved-dark.png'), full_page=True)
                print('screenshots in', shots)
            ctx.close()

        mp3s = list(music.glob('*.mp3'))
        print('files in music folder:', [m.name for m in mp3s])
        if len(mp3s) != 1:
            failures.append(f'expected exactly one MP3, found {len(mp3s)}')
        else:
            probe = subprocess.run([str(VENV_PY), '-I', '-c',
                                    'import sys, json; from mutagen.mp3 import MP3; f = MP3(sys.argv[1]); '
                                    'print(json.dumps({"len": round(f.info.length), "title": str(f.get("TIT2")), '
                                    '"artist": str(f.get("TPE1")), "album": str(f.get("TALB"))}))',
                                    str(mp3s[0])], capture_output=True, text=True, check=True)
            tags = json.loads(probe.stdout)
            print('mp3 tags:', tags)
            if (tags['title'], tags['artist'], tags['album']) != ('Sine Song', 'Test Artist', 'Test Album'):
                failures.append(f'unexpected tags {tags}')
            if abs(tags['len'] - 40) > 2:
                failures.append(f"unexpected length {tags['len']}s")
    finally:
        comp.terminate()
        try:
            out = comp.communicate(timeout=10)[0]
        except subprocess.TimeoutExpired:
            comp.kill()
            out = comp.communicate()[0]
        web.shutdown()
        print('---- companion log ----')
        print(out.strip())
        if not args.keep:
            shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print('\nFAILED:')
        for f in failures:
            print(' -', f)
        sys.exit(1)
    print('\nALL GOOD')


if __name__ == '__main__':
    main()
