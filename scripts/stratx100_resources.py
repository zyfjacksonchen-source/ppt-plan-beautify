#!/usr/bin/env python3
"""Pinned template downloads and offline imports; Python standard library only."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import time
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]

def manifest():
    return json.loads((ROOT / 'assets/stratx100/resources.json').read_text())

def cache():
    return Path(os.environ.get('PPT_TEMPLATE_CACHE', str(Path.home() / '.cache/ppt-plan-beautify/1.0.0'))).expanduser()

def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()

def valid(path, entry):
    return path.is_file() and path.stat().st_size == entry['size'] and digest(path) == entry['sha256']

def resolve(relative):
    local = ROOT / 'assets/stratx100' / relative
    return local if local.is_file() else cache() / relative

def download(entry, base=None, direct=False):
    dest = cache() / entry['file']
    if valid(dest, entry):
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + '.part')
    origins = [base] if base else manifest()['origins']
    if not origins:
        raise ValueError('No published download origin; use import or --base-url with the approved mirror')
    errors = []
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) if direct else urllib.request.build_opener()
    for origin in origins:
        if not origin.startswith('https://'):
            raise ValueError('Download origin must use HTTPS')
        url = origin.rstrip('/') + '/' + entry['key']
        for attempt in range(3):
            try:
                offset = part.stat().st_size if part.exists() else 0
                if offset >= entry['size']:
                    if valid(part, entry):
                        part.replace(dest)
                        return dest
                    part.unlink()
                    offset = 0
                req = urllib.request.Request(url, headers={'Range': f'bytes={offset}-', 'Accept-Encoding': 'identity', 'User-Agent': 'ppt-plan-beautify/1.0'})
                with opener.open(req, timeout=45) as response:
                    if response.status == 206:
                        expected = f'bytes {offset}-{entry["size"]-1}/{entry["size"]}'
                        if response.headers.get('Content-Range') != expected:
                            raise ValueError('Unexpected Content-Range')
                        mode = 'ab' if offset else 'wb'
                    elif response.status == 200:
                        mode, offset = 'wb', 0
                    else:
                        raise ValueError(f'Unexpected HTTP {response.status}')
                    with part.open(mode) as f:
                        total = offset
                        for chunk in iter(lambda: response.read(256 * 1024), b''):
                            total += len(chunk)
                            if total > entry['size']:
                                raise ValueError('Response exceeds pinned resource size')
                            f.write(chunk)
                if not valid(part, entry):
                    if part.stat().st_size >= entry['size']:
                        part.unlink()
                    raise ValueError('Resource incomplete or SHA-256 mismatch')
                part.replace(dest)
                return dest
            except (OSError, ValueError) as exc:
                errors.append(type(exc).__name__ + ': ' + str(exc))
                if attempt < 2:
                    time.sleep(attempt + 1)
    raise ValueError('Download failed; partial file retained for retry. Try an approved --base-url mirror or offline import. ' + '; '.join(errors))

def import_pack(path):
    entries = manifest()['files']
    count = 0
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        if len(names) != len(set(names)):
            raise ValueError('Duplicate archive entries')
        # Extract only exact pinned members, never arbitrary archive paths.
        for entry in entries:
            name = entry['file']
            info = z.getinfo(name)
            if info.file_size != entry['size']:
                raise ValueError('Unexpected resource size: ' + name)
            dest = cache() / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_suffix(dest.suffix + '.import')
            try:
                with z.open(info) as src, tmp.open('wb') as out:
                    shutil.copyfileobj(src, out, 1024 * 1024)
                if not valid(tmp, entry):
                    raise ValueError('SHA-256 mismatch: ' + name)
                tmp.replace(dest)
                count += 1
            finally:
                tmp.unlink(missing_ok=True)
    return count

def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    fetch = sub.add_parser('fetch')
    fetch.add_argument('--theme', default='morning-bay-haze')
    fetch.add_argument('--preview', help='Download only this template preview, e.g. 055')
    fetch.add_argument('--base-url', help='Approved HTTPS mirror containing the same versioned object keys')
    fetch.add_argument('--direct', action='store_true', help='Ignore proxy only for this download process; system settings unchanged')
    imp = sub.add_parser('import')
    imp.add_argument('archive', type=Path)
    sub.add_parser('status')
    args = p.parse_args()
    if args.command == 'import':
        print(json.dumps({'imported': import_pack(args.archive), 'cache': str(cache())}))
    elif args.command == 'status':
        print(json.dumps({'cache': str(cache()), 'resources': [{'file': e['file'], 'verified': valid(resolve(e['file']), e)} for e in manifest()['files']]}, ensure_ascii=False, indent=2))
    else:
        key = 'preview-' + args.preview.zfill(3) if args.preview else args.theme
        entries = [e for e in manifest()['files'] if e['id'] == key]
        if len(entries) != 1:
            raise ValueError('Unknown theme or preview ID')
        print(download(entries[0], args.base_url, args.direct))

if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        raise SystemExit('ERROR: ' + str(exc))
