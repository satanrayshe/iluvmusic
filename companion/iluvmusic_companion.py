#!/usr/bin/env python3
"""
iluvmusic companion

A small local server that the iluvmusic browser extension talks to. When the
extension decides you've replayed a song enough times, it asks this server to
save it, and the server turns it into an MP3 in your music folder using yt-dlp
and ffmpeg.

It listens on 127.0.0.1 only, so nothing outside this computer can reach it,
and it refuses requests from ordinary websites.

    python iluvmusic_companion.py                  run it
    python iluvmusic_companion.py --dir D:/Music   save somewhere else
    python iluvmusic_companion.py --install-autostart
    python iluvmusic_companion.py --remove-autostart
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote_plus, urlparse

APP_NAME = 'iluvmusic-companion'
VERSION = '1.0.0'
DEFAULT_PORT = 7777

HOME = Path(os.environ.get('ILUVMUSIC_HOME') or (Path.home() / '.iluvmusic'))
CONFIG_PATH = HOME / 'config.json'
LIBRARY_PATH = HOME / 'library.json'
LOG_PATH = HOME / 'companion.log'
SCRIPT_PATH = Path(__file__).resolve()

# Requests may come from the extension (these origins) or from local tools
# that send no Origin header at all. Ordinary websites always send an
# http(s) Origin, so they are turned away.
EXTENSION_SCHEMES = {'chrome-extension', 'moz-extension', 'safari-web-extension',
                     'ms-browser-extension', 'extension'}
MAX_BODY = 64 * 1024
NO_WINDOW = 0x08000000 if os.name == 'nt' else 0  # CREATE_NO_WINDOW

# Filled in by main() once yt-dlp is importable.
yt_dlp = None
YoutubeDL = None
HAVE_EJS = False
FFMPEG: str | None = None
JS_RUNTIMES: dict = {}
CONFIG: dict = {}
LIB: 'Library'
JOBS: 'queue.Queue[str]' = queue.Queue()


# --------------------------------------------------------------------------
# small helpers

_log_lock = threading.Lock()


def log(*parts) -> None:
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] " + ' '.join(str(p) for p in parts)
    with _log_lock:
        try:
            print(line, flush=True)
        except Exception:
            pass
        try:
            with open(LOG_PATH, 'a', encoding='utf-8') as f:
                f.write(line + '\n')
        except OSError:
            pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return default
    except (OSError, ValueError) as e:
        # Keep the broken file around instead of silently overwriting it.
        backup = path.with_name(path.name + f'.broken-{int(time.time())}')
        try:
            path.replace(backup)
            log(f'{path.name} could not be read ({e}); moved it to {backup.name}')
        except OSError:
            log(f'{path.name} could not be read ({e})')
        return default


def save_json(path: Path, data) -> None:
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')
    os.replace(tmp, path)


def friendly_error(e: BaseException) -> str:
    msg = re.sub(r'\x1b\[[0-9;]*m', '', str(e)).strip()
    msg = re.sub(r'^ERROR:\s*', '', msg)
    return (msg or e.__class__.__name__)[:400]


def norm(s: str | None) -> str:
    return re.sub(r'[\W_]+', ' ', (s or '').lower()).strip()


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


# --------------------------------------------------------------------------
# config and library

def default_music_dir() -> str:
    music = Path.home() / 'Music'
    return str((music if music.is_dir() else Path.home()) / 'iluvmusic')


def load_config() -> dict:
    cfg = {
        'downloadDir': default_music_dir(),
        'port': DEFAULT_PORT,
        'quality': '0',          # yt-dlp VBR quality; 0 is the best
        'autoUpdate': True,      # keep yt-dlp fresh (only inside our own venv)
        'lastUpdateCheck': 0,
    }
    stored = load_json(CONFIG_PATH, {})
    if isinstance(stored, dict):
        cfg.update({k: v for k, v in stored.items() if k in cfg})
    return cfg


def save_config() -> None:
    save_json(CONFIG_PATH, CONFIG)


class Library:
    """Everything the companion has been asked to save, keyed by the
    extension's track key. Stored in ~/.iluvmusic/library.json.

    The file is the seed for a future cloud library: every entry has a stable
    id, where it came from, and where the MP3 lives on disk."""

    SCHEMA = 1

    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.RLock()
        data = load_json(path, {})
        items = data.get('items') if isinstance(data, dict) else None
        self.items: dict[str, dict] = items if isinstance(items, dict) else {}

    def save(self) -> None:
        with self.lock:
            save_json(self.path, {'schema': self.SCHEMA, 'app': APP_NAME, 'items': self.items})

    @staticmethod
    def public(item: dict) -> dict:
        out = {k: item.get(k) for k in (
            'id', 'key', 'title', 'artist', 'album', 'site', 'sourceUrl', 'resolvedUrl',
            'status', 'error', 'file', 'bytes', 'requestedAt', 'downloadedAt')}
        if out['status'] == 'done' and not (out['file'] and Path(out['file']).is_file()):
            out['status'] = 'missing'
        return out

    def snapshot(self) -> list[dict]:
        with self.lock:
            return [self.public(i) for i in self.items.values()]


# --------------------------------------------------------------------------
# finding and saving songs

class YdlLogger:
    def debug(self, msg):
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        if 'Falling back on generic information extractor' in msg:
            return  # expected for ordinary web pages
        log('yt-dlp warning:', friendly_error(Exception(msg)))

    def error(self, msg):
        log('yt-dlp:', friendly_error(Exception(msg)))


def base_opts() -> dict:
    opts = {
        'quiet': True,
        'no_warnings': True,
        'noprogress': True,
        'noplaylist': True,
        'socket_timeout': 30,
        'retries': 3,
        'logger': YdlLogger(),
    }
    if FFMPEG:
        opts['ffmpeg_location'] = FFMPEG
    if JS_RUNTIMES:
        opts['js_runtimes'] = JS_RUNTIMES
    if not HAVE_EJS:
        # YouTube needs yt-dlp's challenge solver. It ships in yt-dlp[default];
        # if that is missing, let yt-dlp fetch it from its GitHub releases.
        opts['remote_components'] = ['ejs:github']
    return opts


def entry_url(entry: dict | None) -> str:
    if not entry:
        return ''
    url = entry.get('webpage_url') or entry.get('url') or ''
    if re.fullmatch(r'[\w-]{11}', url or '') and 'youtube' in (entry.get('ie_key') or '').lower():
        url = f'https://www.youtube.com/watch?v={url}'
    return url


def search_query(item: dict) -> str:
    title = item.get('title') or ''
    artist = item.get('artist') or ''
    if artist and norm(artist) not in norm(title):
        return f'{artist} - {title}'
    return title


def pick_by_duration(entries: list[dict], target: float | None) -> dict:
    if target:
        slack = max(8.0, target * 0.1)
        for e in entries:
            d = e.get('duration')
            if isinstance(d, (int, float)) and abs(d - target) <= slack:
                return e
    return entries[0]


def search_source(item: dict) -> str:
    """Find the song on YouTube Music (then plain YouTube) by artist and title.
    Used for services whose audio is DRM-protected, like Spotify."""
    query = search_query(item)
    if not query:
        raise RuntimeError('Not enough information to look this song up')
    for search_url in (f'https://music.youtube.com/search?q={quote_plus(query)}#songs',
                       f'ytsearch5:{query}'):
        try:
            with YoutubeDL({**base_opts(), 'extract_flat': 'in_playlist', 'playlistend': 5}) as ydl:
                info = ydl.extract_info(search_url, download=False)
        except Exception as e:
            log('Search failed for', repr(query), '-', friendly_error(e))
            continue
        entries = [e for e in (info or {}).get('entries') or [] if e and entry_url(e)][:5]
        if not entries:
            continue
        pick = pick_by_duration(entries, item.get('duration'))
        log(f'Matched {query!r} -> {pick.get("title")!r} {entry_url(pick)}')
        return entry_url(pick)
    raise RuntimeError(f'Could not find "{query}" on YouTube')


def pick_from_page(url: str, item: dict) -> str | None:
    """For an arbitrary web page, make sure we download the song that was
    playing, not a whole album or an unrelated video on the same page."""
    with YoutubeDL({**base_opts(), 'extract_flat': 'in_playlist', 'playlistend': 100}) as ydl:
        info = ydl.extract_info(url, download=False)
    if not info:
        return None
    if info.get('_type') not in ('playlist', 'multi_video'):
        return url
    entries = [e for e in info.get('entries') or [] if e]
    want = norm(item.get('title'))
    if want:
        for e in entries:
            got = norm(e.get('title'))
            if got and (want in got or got in want):
                return entry_url(e) or None
    if len(entries) == 1:
        return entry_url(entries[0]) or None
    return None


def resolve_source(item: dict) -> str:
    url = item.get('sourceUrl') or ''
    if item.get('mode') == 'url' and url:
        if item.get('site') in ('youtube', 'ytmusic', 'soundcloud'):
            return url
        try:
            picked = pick_from_page(url, item)
            if picked:
                return picked
            log('No matching audio found on', url, '- searching YouTube instead')
        except Exception as e:
            log('Could not read', url, '-', friendly_error(e), '- searching YouTube instead')
    return search_source(item)


WINDOWS_RESERVED = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)),
                    *(f'LPT{i}' for i in range(1, 10))}


def clean_filename(name: str, limit: int = 150) -> str:
    name = re.sub(r'[\x00-\x1f<>:"/\\|?*]+', ' ', name or '')
    name = re.sub(r'\s+', ' ', name).strip().strip('.').strip()
    name = name[:limit].rstrip(' .')
    if name.split('.')[0].upper() in WINDOWS_RESERVED:
        name = '_' + name
    return name or 'Untitled'


def file_stem(item: dict) -> str:
    title = (item.get('title') or '').strip()
    artist = (item.get('artist') or '').strip()
    if artist and norm(artist) not in norm(title):
        return clean_filename(f'{artist} - {title}')
    return clean_filename(title or artist)


def fetch_artwork(url: str | None):
    if not url or not url.startswith('https://'):
        return None
    try:
        req = urllib.request.Request(url, headers={'User-Agent': f'Mozilla/5.0 {APP_NAME}/{VERSION}',
                                                   'Accept': 'image/jpeg,image/png'})
        with urllib.request.urlopen(req, timeout=15) as r:
            ctype = r.headers.get_content_type()
            if ctype not in ('image/jpeg', 'image/png'):
                return None
            data = r.read(5 * 1024 * 1024 + 1)
            if len(data) > 5 * 1024 * 1024:
                return None
            return data, ctype
    except Exception:
        return None


def write_tags(path: Path, item: dict) -> None:
    try:
        from mutagen.id3 import APIC, ID3, ID3NoHeaderError, TALB, TIT2, TPE1
    except ImportError:
        return
    try:
        try:
            tags = ID3(path)
        except ID3NoHeaderError:
            tags = ID3()
        if item.get('cleanMeta'):
            if item.get('title'):
                tags.setall('TIT2', [TIT2(encoding=3, text=item['title'])])
            if item.get('artist'):
                tags.setall('TPE1', [TPE1(encoding=3, text=item['artist'])])
            if item.get('album'):
                tags.setall('TALB', [TALB(encoding=3, text=item['album'])])
        art = fetch_artwork(item.get('artwork'))
        if art:
            tags.delall('APIC')
            tags.add(APIC(encoding=3, mime=art[1], type=3, desc='Cover', data=art[0]))
        tags.save(path, v2_version=3)
    except Exception as e:
        log('Could not write tags for', path.name, '-', e)


def save_song(item: dict) -> dict:
    out_dir = Path(CONFIG['downloadDir'])
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = file_stem(item)
    target = out_dir / f'{stem}.mp3'
    if target.is_file() and target.stat().st_size > 0:
        log('Already in your folder:', target.name)
        return {'file': str(target), 'bytes': target.stat().st_size,
                'resolvedUrl': item.get('resolvedUrl') or item.get('sourceUrl') or ''}

    source = resolve_source(item)
    opts = {
        **base_opts(),
        'format': 'bestaudio/best',
        'playlist_items': '1',  # never pull a whole playlist by accident
        'outtmpl': {'default': str(out_dir / (stem.replace('%', '%%') + '.%(ext)s'))},
        'writethumbnail': True,
        'overwrites': True,
        'postprocessors': [
            {'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3',
             'preferredquality': str(CONFIG.get('quality', '0'))},
            {'key': 'FFmpegMetadata', 'add_metadata': True},
            {'key': 'EmbedThumbnail', 'already_have_thumbnail': False},
        ],
    }
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(source, download=True) or {}
    if info.get('_type') in ('playlist', 'multi_video'):
        info = next((e for e in info.get('entries') or [] if e), {}) or {}
    if not target.is_file():
        produced = next((d.get('filepath') for d in info.get('requested_downloads') or []
                         if d.get('filepath')), None)
        if produced and Path(produced).is_file():
            target = Path(produced)
        else:
            raise RuntimeError('The download finished but no MP3 came out of it')
    write_tags(target, item)
    return {'file': str(target), 'bytes': target.stat().st_size,
            'resolvedUrl': info.get('webpage_url') or source}


def describe(item: dict) -> str:
    who = f"{item.get('artist')} - " if item.get('artist') else ''
    return f"{who}{item.get('title') or item.get('sourceUrl') or item.get('key')}"


def worker_loop() -> None:
    while True:
        key = JOBS.get()
        with LIB.lock:
            item = LIB.items.get(key)
            if not item or item.get('status') != 'queued':
                continue
            item['status'] = 'downloading'
            item['error'] = None
            LIB.save()
            job = dict(item)
        log('Saving', describe(job))
        try:
            result = save_song(job)
        except Exception as e:
            msg = friendly_error(e)
            log('Failed:', describe(job), '-', msg)
            with LIB.lock:
                item.update(status='error', error=msg, failedAt=now_iso())
                LIB.save()
        else:
            with LIB.lock:
                item.update(status='done', error=None, downloadedAt=now_iso(), **result)
                LIB.save()
            log('Saved', result['file'])


# --------------------------------------------------------------------------
# HTTP API

def text_field(body: dict, name: str, limit: int, required: bool = False) -> str:
    value = body.get(name)
    if value is None or value == '':
        if required:
            raise ApiError(400, f'Missing {name}')
        return ''
    if not isinstance(value, str):
        raise ApiError(400, f'{name} must be text')
    return value.strip()[:limit]


def api_download(body: dict):
    key = text_field(body, 'key', 512, required=True)
    url = text_field(body, 'url', 2048)
    if url and urlparse(url).scheme not in ('http', 'https'):
        raise ApiError(400, 'Only http and https links can be saved')
    mode = body.get('mode') if body.get('mode') in ('url', 'search') else ('url' if url else 'search')
    duration = body.get('duration')
    duration = float(duration) if isinstance(duration, (int, float)) and 0 < duration < 86400 else None
    artwork = text_field(body, 'artwork', 2048)
    fields = {
        'title': text_field(body, 'title', 300),
        'artist': text_field(body, 'artist', 300),
        'album': text_field(body, 'album', 300),
        'site': text_field(body, 'site', 40) or 'web',
        'sourceUrl': url,
        'mode': mode,
        'duration': duration,
        'artwork': artwork if artwork.startswith('https://') else '',
        'cleanMeta': bool(body.get('cleanMeta')),
    }
    if not fields['title'] and not url:
        raise ApiError(400, 'Need a title or a link to find the song')
    with LIB.lock:
        item = LIB.items.get(key)
        if item and item.get('status') in ('queued', 'downloading'):
            return 200, Library.public(item)
        if item and item.get('status') == 'done' and Library.public(item)['status'] == 'done':
            return 200, Library.public(item)
        if not item:
            item = {'id': uuid.uuid4().hex, 'key': key, 'requestedAt': now_iso()}
            LIB.items[key] = item
        item.update(fields)
        item.update(status='queued', error=None)
        LIB.save()
    JOBS.put(key)
    log('Queued', describe(item))
    return 202, Library.public(item)


def reveal(path: Path) -> None:
    system = platform.system()
    if system == 'Windows':
        if path.is_file():
            subprocess.Popen(f'explorer /select,"{path}"')
        else:
            os.startfile(str(path))  # type: ignore[attr-defined]
    elif system == 'Darwin':
        subprocess.Popen(['open', '-R', str(path)] if path.is_file() else ['open', str(path)])
    else:
        subprocess.Popen(['xdg-open', str(path.parent if path.is_file() else path)])


def api_reveal(body: dict):
    key = text_field(body, 'key', 512)
    target = Path(CONFIG['downloadDir'])
    if key:
        with LIB.lock:
            item = LIB.items.get(key)
            if item and item.get('file') and Path(item['file']).is_file():
                target = Path(item['file'])
    target.parent.mkdir(parents=True, exist_ok=True)
    if target == Path(CONFIG['downloadDir']):
        target.mkdir(parents=True, exist_ok=True)
    reveal(target)
    return 200, {'ok': True}


def api_config(body: dict):
    new_dir = text_field(body, 'downloadDir', 1024)
    if new_dir:
        path = Path(os.path.expandvars(os.path.expanduser(new_dir)))
        if not path.is_absolute():
            raise ApiError(400, 'Use a full folder path, like C:\\Users\\you\\Music\\iluvmusic')
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / '.iluvmusic-write-test'
            probe.write_text('ok', encoding='utf-8')
            probe.unlink()
        except OSError as e:
            raise ApiError(400, f'Cannot save into that folder: {e.strerror or e}')
        CONFIG['downloadDir'] = str(path)
        save_config()
        log('Music folder is now', path)
    return 200, health()


def health() -> dict:
    with LIB.lock:
        busy = sum(1 for i in LIB.items.values() if i.get('status') in ('queued', 'downloading'))
    return {
        'ok': True,
        'app': APP_NAME,
        'version': VERSION,
        'ytdlp': getattr(getattr(yt_dlp, 'version', None), '__version__', '?'),
        'ffmpeg': bool(FFMPEG),
        'jsRuntime': ', '.join(JS_RUNTIMES) or None,
        'downloadDir': CONFIG['downloadDir'],
        'busy': busy,
    }


POST_ROUTES = {'/download': api_download, '/reveal': api_reveal, '/config': api_config}


class Handler(BaseHTTPRequestHandler):
    server_version = f'{APP_NAME}/{VERSION}'

    def log_message(self, fmt, *args):  # keep the console quiet
        pass

    def _origin(self) -> str | None:
        return self.headers.get('Origin')

    def _is_extension_origin(self) -> bool:
        origin = self._origin()
        return bool(origin) and origin.split(':', 1)[0].lower() in EXTENSION_SCHEMES

    def _origin_allowed(self) -> bool:
        # Firefox sends "Origin: null" for an extension's own requests, so null
        # is let through here. That's still safe: a web page can only send a
        # JSON POST after a CORS preflight, and do_OPTIONS approves preflights
        # for extension origins only. Pages can't read our answers either,
        # since only extension origins get CORS headers.
        origin = self._origin()
        return origin is None or origin == 'null' or self._is_extension_origin()

    def _host_allowed(self) -> bool:
        # Blocks DNS-rebinding tricks: the browser must be talking to us by
        # our loopback name, not through some website's domain.
        port = self.server.server_address[1]
        host = (self.headers.get('Host') or '').lower()
        return host in {f'127.0.0.1:{port}', f'localhost:{port}', f'[::1]:{port}'}

    def _cors_headers(self) -> None:
        if self._is_extension_origin():
            self.send_header('Access-Control-Allow-Origin', self._origin())
            self.send_header('Vary', 'Origin')

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self._cors_headers()
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def _guard(self) -> bool:
        if not self._host_allowed():
            self._send(403, {'error': 'Wrong host'})
            return False
        if not self._origin_allowed():
            self._send(403, {'error': 'Only the iluvmusic extension can use this'})
            return False
        return True

    def do_OPTIONS(self):
        if not self._host_allowed() or not self._is_extension_origin():
            self._send(403, {'error': 'Only the iluvmusic extension can use this'})
            return
        self.send_response(204)
        self._cors_headers()
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        if self.headers.get('Access-Control-Request-Private-Network'):
            self.send_header('Access-Control-Allow-Private-Network', 'true')
        self.send_header('Access-Control-Max-Age', '600')
        self.send_header('Content-Length', '0')
        self.end_headers()

    def do_GET(self):
        if not self._guard():
            return
        path = urlparse(self.path).path
        if path == '/health':
            self._send(200, health())
        elif path == '/library':
            self._send(200, {'items': LIB.snapshot(), 'downloadDir': CONFIG['downloadDir']})
        else:
            self._send(404, {'error': 'Not found'})

    def do_POST(self):
        if not self._guard():
            return
        ctype = (self.headers.get('Content-Type') or '').split(';')[0].strip().lower()
        if ctype != 'application/json':
            return self._send(415, {'error': 'Send JSON'})
        try:
            length = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY:
            return self._send(413, {'error': 'Request too large'})
        try:
            body = json.loads(self.rfile.read(length) or b'{}')
        except ValueError:
            return self._send(400, {'error': 'Bad JSON'})
        if not isinstance(body, dict):
            return self._send(400, {'error': 'Bad JSON'})
        route = POST_ROUTES.get(urlparse(self.path).path)
        if not route:
            return self._send(404, {'error': 'Not found'})
        try:
            status, payload = route(body)
        except ApiError as e:
            status, payload = e.status, {'error': str(e)}
        except Exception:
            log(traceback.format_exc())
            status, payload = 500, {'error': 'Something went wrong in the companion; see companion.log'}
        self._send(status, payload)


# --------------------------------------------------------------------------
# setup: tools, updates, autostart

def find_ffmpeg() -> str | None:
    found = shutil.which('ffmpeg')
    if found:
        return found
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def find_js_runtimes() -> dict:
    found = {}
    for name in ('deno', 'node', 'bun'):
        path = shutil.which(name)
        if path:
            found[name] = {'path': path}
    return found


def in_own_venv() -> bool:
    return sys.prefix != sys.base_prefix and Path(sys.prefix).resolve().parent == SCRIPT_PATH.parent


def maybe_update_ytdlp() -> None:
    """YouTube changes often and old yt-dlp versions stop working, so refresh
    it once a day. Only touches the companion's own .venv, never a system
    Python. Runs before yt-dlp is imported so the new version is used."""
    if not CONFIG.get('autoUpdate', True) or not in_own_venv():
        return
    if time.time() - float(CONFIG.get('lastUpdateCheck') or 0) < 24 * 3600:
        return
    log('Checking for a newer yt-dlp...')
    try:
        r = subprocess.run(
            [sys.executable, '-m', 'pip', 'install', '--quiet', '--disable-pip-version-check',
             '--retries', '1', '--timeout', '15', '--upgrade', 'yt-dlp[default]'],
            capture_output=True, text=True, timeout=300, creationflags=NO_WINDOW)
        if r.returncode == 0:
            CONFIG['lastUpdateCheck'] = time.time()
            save_config()
        else:
            log('yt-dlp update skipped:', (r.stderr or r.stdout).strip().splitlines()[-1:] or '?')
    except Exception as e:
        log('yt-dlp update skipped:', e)


def autostart_target() -> Path | None:
    system = platform.system()
    if system == 'Windows':
        appdata = os.environ.get('APPDATA')
        if not appdata:
            return None
        return Path(appdata) / 'Microsoft/Windows/Start Menu/Programs/Startup/iluvmusic-companion.vbs'
    if system == 'Darwin':
        return Path.home() / 'Library/LaunchAgents/com.iluvmusic.companion.plist'
    return Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config') / 'autostart/iluvmusic-companion.desktop'


def install_autostart() -> int:
    target = autostart_target()
    if not target:
        log('Could not work out where to put the autostart entry on this system.')
        return 1
    target.parent.mkdir(parents=True, exist_ok=True)
    python = Path(sys.executable)
    system = platform.system()
    if system == 'Windows':
        quiet = python.with_name('pythonw.exe')
        exe = quiet if quiet.exists() else python
        target.write_text(
            "' Starts the iluvmusic companion quietly when you sign in.\r\n"
            f'CreateObject("WScript.Shell").Run """{exe}"" ""{SCRIPT_PATH}""", 0, False\r\n',
            encoding='utf-8')
    elif system == 'Darwin':
        target.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.iluvmusic.companion</string>
  <key>ProgramArguments</key>
  <array><string>{python}</string><string>{SCRIPT_PATH}</string></array>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>/dev/null</string>
  <key>StandardErrorPath</key><string>/dev/null</string>
</dict>
</plist>
""", encoding='utf-8')
    else:
        target.write_text(
            '[Desktop Entry]\nType=Application\nName=iluvmusic companion\n'
            f'Exec="{python}" "{SCRIPT_PATH}"\nX-GNOME-Autostart-enabled=true\nNoDisplay=true\n',
            encoding='utf-8')
    log('The companion will now start by itself when you sign in.', f'({target})')
    return 0


def remove_autostart() -> int:
    target = autostart_target()
    if target and target.exists():
        target.unlink()
        log('Removed autostart entry', target)
    else:
        log('No autostart entry to remove.')
    return 0


def companion_already_running(port: int) -> bool:
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=2) as r:
            return json.loads(r.read()).get('app') == APP_NAME
    except Exception:
        return False


# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    global yt_dlp, YoutubeDL, HAVE_EJS, FFMPEG, JS_RUNTIMES, CONFIG, LIB

    parser = argparse.ArgumentParser(description='Saves the songs you replay most as MP3s.')
    parser.add_argument('--dir', help='folder to save MP3s into (remembered)')
    parser.add_argument('--port', type=int, help=f'port to listen on (default {DEFAULT_PORT})')
    parser.add_argument('--install-autostart', action='store_true', help='start automatically when you sign in')
    parser.add_argument('--remove-autostart', action='store_true', help='stop starting automatically')
    parser.add_argument('--no-update', action='store_true', help="don't check for a newer yt-dlp")
    args = parser.parse_args(argv)

    HOME.mkdir(parents=True, exist_ok=True)
    try:
        if LOG_PATH.exists() and LOG_PATH.stat().st_size > 1_000_000:
            LOG_PATH.replace(LOG_PATH.with_name('companion.old.log'))
    except OSError:
        pass

    if args.install_autostart:
        return install_autostart()
    if args.remove_autostart:
        return remove_autostart()

    CONFIG = load_config()
    if args.dir:
        CONFIG['downloadDir'] = str(Path(os.path.expanduser(args.dir)).resolve())
    if args.port:
        CONFIG['port'] = args.port
    save_config()
    port = int(CONFIG['port'])

    if companion_already_running(port):
        log(f'The companion is already running on port {port}.')
        return 0

    if not args.no_update:
        maybe_update_ytdlp()

    try:
        import yt_dlp as _yt_dlp
        from yt_dlp import YoutubeDL as _YoutubeDL
    except ImportError:
        log('yt-dlp is not installed. Run the start script, or: python -m pip install -r requirements.txt')
        return 1
    yt_dlp, YoutubeDL = _yt_dlp, _YoutubeDL
    try:
        import yt_dlp_ejs  # noqa: F401
        HAVE_EJS = True
    except ImportError:
        HAVE_EJS = False
    FFMPEG = find_ffmpeg()
    JS_RUNTIMES = find_js_runtimes()

    Path(CONFIG['downloadDir']).mkdir(parents=True, exist_ok=True)
    LIB = Library(LIBRARY_PATH)
    pending = [k for k, i in LIB.items.items() if i.get('status') in ('queued', 'downloading')]
    for key in pending:
        LIB.items[key]['status'] = 'queued'
        JOBS.put(key)
    if pending:
        LIB.save()

    try:
        server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    except OSError as e:
        log(f'Port {port} is busy ({e.strerror or e}). Close whatever is using it, or run with --port.')
        return 1
    server.daemon_threads = True
    threading.Thread(target=worker_loop, name='saver', daemon=True).start()

    log(f'iluvmusic companion {VERSION} is running at http://127.0.0.1:{port}')
    log('Saving MP3s to', CONFIG['downloadDir'])
    log('yt-dlp', health()['ytdlp'], '| ffmpeg', FFMPEG or 'NOT FOUND',
        '| JavaScript runtime', ', '.join(JS_RUNTIMES) or 'none')
    if not FFMPEG:
        log('WARNING: ffmpeg was not found, so songs cannot be converted to MP3. '
            'Install it (Windows: winget install ffmpeg) or: python -m pip install imageio-ffmpeg')
    if not JS_RUNTIMES:
        log('WARNING: no JavaScript runtime found. YouTube downloads need Deno or Node.js '
            '(Windows: winget install DenoLand.Deno).')
    if pending:
        log(f'Picking up {len(pending)} unfinished download(s).')
    log('Leave this running. Press Ctrl+C to stop.')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log('Stopped.')
    finally:
        server.server_close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
