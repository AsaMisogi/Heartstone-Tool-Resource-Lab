"""只检查官方仓库的稳定 Release；不下载、不执行远程文件。"""
import json
import re
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from . import __version__

REPOSITORY = 'AsaMisogi/Heartstone-Tool-Resource-Lab'
RELEASES_URL = f'https://github.com/{REPOSITORY}/releases'


def version_tuple(value):
    """按数值比较版本，拒绝把预发布标签或任意文本误认为稳定升级。"""
    match = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)', str(value))
    if not match:
        raise ValueError('发布标签不是受支持的稳定版本号')
    return tuple(map(int, match.groups()))


def check_update():
    request = Request(f'https://api.github.com/repos/{REPOSITORY}/releases/latest',
                      headers={'Accept': 'application/vnd.github+json',
                               'User-Agent': f'PengPengWorkbench/{__version__}'})
    try:
        with urlopen(request, timeout=10) as response:
            data = json.loads(response.read(1024 * 1024))
    except HTTPError as exc:
        if exc.code == 404:
            return {'available': False, 'message': '仓库尚未发布稳定版本', 'current': __version__}
        if exc.code in (403, 429):
            raise ValueError('GitHub 暂时限制了请求频率，请稍后重试') from exc
        raise
    if not isinstance(data, dict) or data.get('draft') or data.get('prerelease'):
        raise ValueError('GitHub 未返回有效的稳定版本')
    tag = data.get('tag_name', '')
    available = version_tuple(tag) > version_tuple(__version__)
    # 链接自行构造，不信任响应中可能指向其他网站的 html_url。
    return {'available': available, 'current': __version__, 'latest': tag,
            'url': f'{RELEASES_URL}/tag/{tag}',
            'message': f'发现新版本 {tag}' if available else f'当前 v{__version__} 已是最新版本'}
