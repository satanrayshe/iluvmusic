"""Zips the extension for uploading to a store (Firefox Add-ons, Edge Add-ons,
Opera) or for sharing.  Run: python tools/package.py  ->  dist/iluvmusic-<version>.zip"""
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXT = ROOT / 'extension'


def main():
    version = json.loads((EXT / 'manifest.json').read_text(encoding='utf-8'))['version']
    out = ROOT / 'dist' / f'iluvmusic-{version}.zip'
    out.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        for path in sorted(EXT.rglob('*')):
            if path.is_file() and not path.name.startswith('.'):
                z.write(path, path.relative_to(EXT).as_posix())
    print('wrote', out)


if __name__ == '__main__':
    main()
