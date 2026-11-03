"""Public stable GitHub releases. No credentials or caller-provided URLs."""
import json
import re
import time
from urllib.parse import urlsplit

import httpx

REPOSITORY = 'fichil/wechat-agent-bridge'
RELEASES_URL = 'https://github.com/' + REPOSITORY + '/releases'
API_URL = 'https://api.github.com/repos/' + REPOSITORY + '/releases'
CHECK_INTERVAL = 24 * 60 * 60
MAX_ARCHIVE = 1024 ** 3


def version_key(value):
    match = re.fullmatch(r'v?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-([0-9A-Za-z.-]+))?', value or '')
    if not match:
        raise ValueError('版本号格式无效。')
    return (*map(int, match.group(1, 2, 3)), not bool(match[4]))


def allowed_url(url):
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None, 443)
            or parsed.hostname not in {'api.github.com', 'github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com'}):
        raise ValueError('更新下载地址不是受支持的 GitHub 地址。')
    return url


def client():
    return httpx.Client(timeout=httpx.Timeout(30, connect=15), follow_redirects=False,
                        headers={'User-Agent': 'WeChatAgentBridge-Updater/1', 'Accept': 'application/vnd.github+json'},
                        trust_env=False)


def fetch(url, *, output=None, limit=2 * 1024**2, progress=None):
    """Bound redirects, size and elapsed time; never persist a partial final file."""
    started = time.monotonic()
    with client() as session:
        for _ in range(6):
            with session.stream('GET', allowed_url(url)) as response:
                if response.is_redirect:
                    url = str(response.url.join(response.headers['location']))
                    continue
                if response.status_code in (403, 429):
                    raise ValueError('GitHub 暂时限流，请稍后重试。')
                response.raise_for_status()
                total = int(response.headers.get('content-length', 0))
                if total > limit:
                    raise ValueError('更新文件超出大小限制。')
                size = 0
                content = bytearray()
                for chunk in response.iter_bytes(256 * 1024):
                    size += len(chunk)
                    if size > limit or time.monotonic() - started > (1800 if output else 60):
                        raise ValueError('更新下载超出大小或时间限制。')
                    if output: output.write(chunk)
                    else: content.extend(chunk)
                    if progress: progress(size, total)
                return bytes(content)
    raise ValueError('更新下载重定向次数过多。')


def select_release(releases, current):
    candidates = []
    for item in releases:
        if item.get('draft') or item.get('prerelease') or not item.get('published_at'):
            continue
        tag = item.get('tag_name', '')
        if not re.fullmatch(r'v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)', tag):
            continue
        if version_key(tag) > version_key(current): candidates.append(item)
    if not candidates: return None
    item = max(candidates, key=lambda value: version_key(value['tag_name']))
    version = item['tag_name'][1:]
    name = 'WeChatAgentBridge-' + version + '-windows-x64'
    assets = {}
    for asset in item.get('assets', []):
        key = asset.get('name')
        if key in assets: raise ValueError('Release 存在重复附件。')
        if asset.get('state') == 'uploaded': assets[key] = asset
    required = [name + '.zip', name + '.zip.sha256', name + '.manifest.json']
    ready = all(n in assets for n in required)
    result = dict(version=version, release_id=item['id'], tag=item['tag_name'],
                  published_at=item['published_at'], notes=str(item.get('body') or '')[:32000],
                  url=RELEASES_URL + '/tag/' + item['tag_name'], ready=ready,
                  reason=None if ready else '发布附件尚未就绪，请稍后重新检查。')
    if ready:
        for filename in required:
            asset = assets[filename]
            expected = RELEASES_URL + '/download/' + item['tag_name'] + '/' + filename
            if asset.get('browser_download_url') != expected:
                raise ValueError('Release 附件地址与版本不一致。')
        result['assets'] = {n: dict(url=assets[n]['browser_download_url'], size=assets[n]['size'], id=assets[n]['id'],
                                    digest=assets[n].get('digest')) for n in required}
        if not 0 < result['assets'][name + '.zip']['size'] <= MAX_ARCHIVE:
            raise ValueError('更新包大小无效。')
    return result


def latest(current):
    try:
        published = json.loads(fetch(API_URL + '/latest'))
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404: return None
        raise
    if not isinstance(published, dict): raise ValueError('GitHub 更新响应格式无效。')
    return select_release([published], current)
