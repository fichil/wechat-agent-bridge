"""Private immutable staging. The worker accepts no caller-selected filesystem path."""
import hashlib
import re
from pathlib import Path
from bridge_paths import DATA

ROOT = DATA / 'outgoing-media'
EXTENSIONS = {'image/png':'.png', 'image/jpeg':'.jpg', 'image/gif':'.gif'}


def stage(attempt, media):
    if not re.fullmatch(r'[0-9a-f]{32}', attempt): raise ValueError('native_media_invalid')
    data = media['data']
    if hashlib.sha256(data).hexdigest() != media['sha256']: raise ValueError('native_media_invalid')
    suffix = EXTENSIONS[media['mime_type']]
    ROOT.mkdir(parents=True, exist_ok=True)
    path = ROOT / (attempt + suffix)
    with path.open('xb') as stream: stream.write(data)
    return dict(media_token=path.name,media_sha256=media['sha256'])


def resolve(request):
    token, digest = request.get('media_token',''), request.get('media_sha256','')
    if not re.fullmatch(r'[0-9a-f]{32}\.(png|jpg|gif)',token) or not re.fullmatch(r'[0-9a-f]{64}',digest):
        raise ValueError('native_media_invalid')
    path = ROOT / token
    if path.is_symlink() or path.resolve().parent != ROOT.resolve(): raise ValueError('native_media_invalid')
    with path.open('rb') as stream:
        data = stream.read(8 * 1024 * 1024 + 1)
    if not data or len(data)>8*1024*1024 or hashlib.sha256(data).hexdigest()!=digest:
        raise ValueError('native_media_invalid')
    return path.resolve()


def release(request):
    # Only a completed native task with a certain result releases the input file.
    resolve(request).unlink()
