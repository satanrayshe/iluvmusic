"""Companion API test. Runs offline against a local test page.

    python tests/companion_api_test.py

Checks the safety rules (which callers are accepted), input validation,
saving from an ordinary web page, not saving the same file twice, noticing a
deleted file and saving it again, and writing the autostart entry (into a fake
AppData/home folder, never the real one).
"""
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
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPANION = ROOT / 'companion' / 'iluvmusic_companion.py'
VENV_PY = ROOT / 'companion' / ('.venv/Scripts/python.exe' if os.name == 'nt' else '.venv/bin/python')

failures = []


def check(cond, label):
    print(('ok    ' if cond else 'FAIL  ') + label)
    if not cond:
        failures.append(label)



class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def call(port, method, path, body=None, headers=None, raw=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(f'http://127.0.0.1:{port}{path}', data=data, method=method)
    if body is not None:
        req.add_header('Content-Type', 'application/json')
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            text = r.read().decode() or '{}'
            return r.status, (json.loads(text) if text.strip() else {}), dict(r.headers)
    except urllib.error.HTTPError as e:
        text = e.read().decode() or '{}'
        return e.code, json.loads(text), dict(e.headers)


def main():
    tmp = Path(tempfile.mkdtemp(prefix='iluvmusic-api-'))
    site, home, music = tmp / 'site', tmp / 'home', tmp / 'music'
    site.mkdir()
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=500:duration=35',
                    '-q:a', '6', str(site / 'song.mp3')], check=True)
    (site / 'song.html').write_text('<!doctype html><title>Song page</title><audio src="song.mp3" controls></audio>')

    web_port, port = free_port(), free_port()
    handler = functools.partial(QuietHandler, directory=str(site))
    web = http.server.ThreadingHTTPServer(('127.0.0.1', web_port), handler)
    threading.Thread(target=web.serve_forever, daemon=True).start()

    env = dict(os.environ, ILUVMUSIC_HOME=str(home))
    proc = subprocess.Popen([str(VENV_PY), str(COMPANION), '--dir', str(music), '--port', str(port), '--no-update'],
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        for _ in range(30):
            try:
                if call(port, 'GET', '/health')[0] == 200:
                    break
            except OSError:
                time.sleep(0.5)

        print('-- who may call')
        check(call(port, 'GET', '/library')[0] == 200, 'no Origin (local tools) is accepted')
        status, _, hdrs = call(port, 'GET', '/library', headers={'Origin': 'chrome-extension://abc'})
        check(status == 200 and hdrs.get('Access-Control-Allow-Origin') == 'chrome-extension://abc',
              'Chrome extension origin is accepted and gets CORS headers')
        check(call(port, 'GET', '/library', headers={'Origin': 'moz-extension://x-y'})[0] == 200, 'Firefox extension origin accepted')
        status, _, hdrs = call(port, 'GET', '/library', headers={'Origin': 'null'})
        check(status == 200 and 'Access-Control-Allow-Origin' not in hdrs,
              'Origin: null (Firefox background) accepted, but gets no CORS headers')
        check(call(port, 'GET', '/library', headers={'Origin': 'https://evil.example'})[0] == 403, 'website origin refused')
        check(call(port, 'POST', '/download', {'key': 'k', 'title': 't'}, headers={'Origin': 'https://evil.example'})[0] == 403,
              'website POST refused')
        check(call(port, 'GET', '/library', headers={'Host': f'evil.example:{port}'})[0] == 403, 'DNS-rebinding host refused')
        check(call(port, 'OPTIONS', '/download', headers={'Origin': 'null', 'Access-Control-Request-Method': 'POST'})[0] == 403,
              'preflight from Origin: null refused (blocks sandboxed web frames)')
        check(call(port, 'OPTIONS', '/download', headers={'Origin': 'https://evil.example', 'Access-Control-Request-Method': 'POST'})[0] == 403,
              'preflight from website refused')
        check(call(port, 'OPTIONS', '/download', headers={'Origin': 'chrome-extension://abc', 'Access-Control-Request-Method': 'POST'})[0] == 204,
              'preflight from extension allowed')
        check(call(port, 'POST', '/download', raw=b'{"key":"k"}', headers={'Content-Type': 'text/plain'})[0] == 415,
              'non-JSON POST refused (what a page could send without a preflight)')

        print('-- input checks')
        check(call(port, 'POST', '/download', {'title': 'x'})[0] == 400, 'missing key refused')
        check(call(port, 'POST', '/download', {'key': 'k', 'url': 'file:///C:/Windows/win.ini'})[0] == 400, 'file:// link refused')
        check(call(port, 'POST', '/download', {'key': 'k'})[0] == 400, 'no title and no link refused')
        check(call(port, 'POST', '/config', {'downloadDir': 'relative/path'})[0] == 400, 'relative music folder refused')
        check(call(port, 'POST', '/nope', {})[0] == 404, 'unknown route is 404')

        print('-- saving')
        song = {'key': 'web:test|tester|page song', 'site': 'web', 'mode': 'url',
                'url': f'http://127.0.0.1:{web_port}/song.html', 'title': 'Page Song', 'artist': 'Tester',
                'duration': 35, 'cleanMeta': True}

        def wait_done(key):
            for _ in range(60):
                item = next(i for i in call(port, 'GET', '/library')[1]['items'] if i['key'] == key)
                if item['status'] not in ('queued', 'downloading'):
                    return item
                time.sleep(1)
            return item

        check(call(port, 'POST', '/download', song)[0] == 202, 'download accepted')
        item = wait_done(song['key'])
        check(item['status'] == 'done' and Path(item['file']).name == 'Tester - Page Song.mp3',
              f"saved as 'Tester - Page Song.mp3' ({item['status']}: {item.get('error') or item.get('file')})")

        status, again, _ = call(port, 'POST', '/download', song)
        check(status == 200 and again['status'] == 'done', 'asking again for a saved song does nothing')

        other = dict(song, key='spotify:other-source-same-song')
        call(port, 'POST', '/download', other)
        item2 = wait_done(other['key'])
        check(item2['status'] == 'done' and item2['file'] == item['file'] and len(list(music.glob('*.mp3'))) == 1,
              'same song from another site reuses the existing file')

        Path(item['file']).unlink()
        item = next(i for i in call(port, 'GET', '/library')[1]['items'] if i['key'] == song['key'])
        check(item['status'] == 'missing', 'deleted file is reported as missing')
        call(port, 'POST', '/download', song)
        item = wait_done(song['key'])
        check(item['status'] == 'done' and Path(item['file']).is_file(), 'missing file is saved again on request')

        lib = json.loads((home / 'library.json').read_text(encoding='utf-8'))
        check(lib.get('schema') == 1 and song['key'] in lib['items'] and lib['items'][song['key']].get('id'),
              'library.json written with schema and stable ids')

        new_dir = tmp / 'elsewhere'
        status, h, _ = call(port, 'POST', '/config', {'downloadDir': str(new_dir)})
        check(status == 200 and Path(h['downloadDir']) == new_dir and new_dir.is_dir(), 'music folder can be changed')
    finally:
        proc.terminate()
        out = proc.communicate(timeout=10)[0]
        web.shutdown()

    print('-- autostart (fake home folders)')
    fake = dict(os.environ, ILUVMUSIC_HOME=str(home), APPDATA=str(tmp / 'appdata'),
                XDG_CONFIG_HOME=str(tmp / 'xdg'), HOME=str(tmp / 'fakehome'), USERPROFILE=str(tmp / 'fakehome'))
    subprocess.run([str(VENV_PY), str(COMPANION), '--install-autostart'], env=fake, check=True, capture_output=True)
    entries = [p for p in tmp.rglob('*') if p.name.startswith(('iluvmusic-companion.', 'com.iluvmusic.'))]
    check(len(entries) == 1, f'one autostart entry written ({[str(e.relative_to(tmp)) for e in entries]})')
    if entries:
        text = entries[0].read_text(encoding='utf-8')
        check(str(COMPANION) in text, 'autostart entry points at the companion script')
        if os.name == 'nt':
            check('pythonw.exe' in text and ', 0, False' in text, 'Windows entry starts it hidden with pythonw')
    subprocess.run([str(VENV_PY), str(COMPANION), '--remove-autostart'], env=fake, check=True, capture_output=True)
    check(not any(e.exists() for e in entries), 'autostart entry removed')

    shutil.rmtree(tmp, ignore_errors=True)
    if failures:
        print('\n---- companion log ----\n' + out)
        print(f'\n{len(failures)} FAILED')
        sys.exit(1)
    print('\nALL GOOD')


if __name__ == '__main__':
    main()
