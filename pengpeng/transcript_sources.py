"""可配置台词来源。只读取数据，不执行站点脚本；失败隔离到单个来源。

内置百科只解析明确的语音区，且核对卡牌名称。自定义源使用固定协议，
不提供任意 Python / JavaScript 解析器，避免配置成为代码执行入口。
"""
import hashlib
import json
import re
import string
import time
from collections import defaultdict
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen
from urllib.error import HTTPError


DEFAULT_SOURCES = [
    {'id': 'baidu', 'name': '百度百科（部分卡牌）', 'kind': 'baidu', 'enabled': True,
     'url': 'https://bkso.baidu.com/item/{name}'},
    {'id': 'huiji', 'name': '炉石中文维基（灰机）', 'kind': 'huiji', 'enabled': True, 'url': ''},
    {'id': 'hsdata', 'name': 'HearthSim hsdata（精确字幕）', 'kind': 'hsdata', 'enabled': False, 'url': ''},
    {'id': 'wikigg', 'name': '炉石 Wiki.gg（中文）', 'kind': 'wikigg', 'enabled': False,
     'url': 'https://hearthstone.wiki.gg/zh/api.php?action=parse&page={name}&prop=text&format=json&redirects=1'},
]


def migrate_sources(values):
    """升级旧工作区：仅在首次缺少新来源时补入，并保留用户的开关与自定义源。

    新版本已保存的完整列表按原顺序返回，不在每次启动时重排用户设置。
    空列表表示全部停用，升级也不能悄悄开启联网。
    """
    present = {s['id'] for s in values}
    additions = [dict(s) for s in DEFAULT_SOURCES if s['id'] in ('hsdata', 'wikigg') and s['id'] not in present]
    if not additions:
        return validate_sources(values)
    if not any(s.get('enabled') for s in values):
        additions = [{**s, 'enabled': False} for s in additions]
    ordered = [s for s in values if s['id'] != 'huiji'] + [s for s in values if s['id'] == 'huiji']
    return validate_sources(ordered + additions)



def validate_sources(values):
    """保存前完整校验，保持旧设置不受失败提交影响。内置来源只能调整开关和顺序。"""
    if not isinstance(values, list) or len(values) > 16:
        raise ValueError('台词来源必须为列表，最多 16 个')
    result, seen = [], set()
    builtins = {s['id']: s for s in DEFAULT_SOURCES}
    for source in values:
        if not isinstance(source, dict):
            raise ValueError('无效的台词来源')
        identity = source.get('id', '')
        if not isinstance(identity, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', identity) or identity in seen:
            raise ValueError('来源编号无效或重复')
        seen.add(identity)
        if type(source.get('enabled')) is not bool:
            raise ValueError('来源开关必须为布尔值')
        if identity in builtins:
            result.append({**builtins[identity], 'enabled': source['enabled']})
            continue
        name, url, kind = source.get('name'), source.get('url'), source.get('kind')
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
            raise ValueError('请输入来源名称（最多 80 字）')
        if kind not in ('json', 'mediawiki') or not isinstance(url, str) or len(url) > 2048:
            raise ValueError('请选择 JSON 或 MediaWiki 格式并填写接口地址')
        parts = urlsplit(url)
        if parts.scheme not in ('https', 'http') or not parts.hostname or parts.username or parts.password or parts.fragment:
            raise ValueError('接口必须是无账号密码、无片段的 HTTP(S) 地址')
        fields = list(string.Formatter().parse(url))
        if not any(field in ('dbfid', 'cardid', 'name') for _, field, _, _ in fields):
            raise ValueError('接口地址必须包含 {dbfid}、{cardid} 或 {name}')
        if any(field is not None and (field not in ('dbfid', 'cardid', 'name', 'locale') or spec or conversion)
               for _, field, spec, conversion in fields):
            raise ValueError('仅支持 {dbfid}、{cardid}、{name}、{locale} 占位符')
        if '{' in parts.netloc or any(c.isspace() for c in url):
            raise ValueError('占位符不能用于域名，地址不能包含空白')
        result.append({'id': identity, 'name': name.strip(), 'url': url, 'kind': kind, 'enabled': source['enabled']})
    # 内置源始终在设置中可见；省略相当于禁用，而不是偷偷重新启用。
    result.extend({**source, 'enabled': False} for source in DEFAULT_SOURCES if source['id'] not in seen)
    if len(result) > 16:
        raise ValueError('包含内置来源在内，最多配置 16 个来源')
    return result


class BaiduQuotes(HTMLParser):
    """只采集语音二级标题下的列表项，跳过引用号；不从攻略或脚本抓取文字。"""
    def __init__(self):
        super().__init__()
        self.heading = None
        self.active = False
        self.row = None
        self.skip = 0
        self.names = []
        self.description = ''
        self.found = defaultdict(list)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'meta':
            if attrs.get('property') == 'og:title':
                self.names.append(attrs.get('content', ''))
            if attrs.get('name') == 'description':
                self.description = attrs.get('content', '')
        if tag == 'h2':
            self.heading = []
        if self.active and tag == 'li':
            self.row = []
        if tag in ('script', 'style', 'sup'):
            self.skip += 1

    def handle_data(self, data):
        if self.skip:
            return
        if self.heading is not None:
            self.heading.append(data)
        if self.row is not None:
            self.row.append(data)

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'sup'):
            self.skip = max(0, self.skip - 1)
        if tag == 'h2' and self.heading is not None:
            self.active = ''.join(self.heading).strip() in ('卡牌语音', '角色台词', '台词')
            self.heading = None
        if tag == 'li' and self.row is not None:
            match = re.fullmatch(r'\s*(召唤|出场|登场|攻击|死亡)[：:]\s*(.+?)\s*', ''.join(self.row))
            if match:
                event = {'出场': 'Play', '召唤': 'Play', '登场': 'Play', '攻击': 'Attack', '死亡': 'Death'}[match[1]]
                self.found[event].append(match[2])
            self.row = None


def parse_baidu(html, name):
    parser = BaiduQuotes()
    parser.feed(html)
    if not name or name not in parser.names or '炉石传说' not in parser.description or '卡牌' not in parser.description:
        return {}
    return {key: values[0] for key, values in parser.found.items() if len(values) == 1}


_cooldowns = {}


class WikiIdentity(HTMLParser):
    """只在信息框的 cardid 字段中收集卡牌 ID，不接受正文的其他卡牌引用。"""
    def __init__(self):
        super().__init__()
        self.depth = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag == 'div':
            if self.depth:
                self.depth += 1
            elif dict(attrs).get('data-source', '').lower() in ('id', 'cardid'):
                self.depth = 1

    def handle_endtag(self, tag):
        if tag == 'div' and self.depth:
            self.depth -= 1

    def handle_data(self, data):
        if self.depth:
            self.parts.append(data)


def parse_wikigg(html, cardid):
    from .transcripts import parse_html_quotes
    identity = WikiIdentity()
    identity.feed(html)
    if not cardid or cardid.upper() not in re.findall(r'[A-Za-z0-9_]+', ''.join(identity.parts).upper()):
        raise ValueError('Wiki.gg 页面卡牌 ID 不匹配')
    return parse_html_quotes(html)


def fetch_hsdata(workspace, *, force=False):
    """按需下载公开 GAMEPLAY_AUDIO 表，一份语言缓存供所有卡牌共用。

    hsdata 是客户端字符串的快照，不承诺比用户本地版本更新；调用者只补缺失
    字幕，并且只按完整音频键匹配，不把同一卡的随机分支合并。
    """
    from .audio_strings import parse_audio_strings
    url = 'https://raw.githubusercontent.com/HearthSim/hsdata/master/Strings/zhCN/GAMEPLAY_AUDIO.txt'
    path = Path(workspace) / 'cache' / 'hsdata-zhCN-gameplay.json'
    cached = None
    try:
        cached = json.loads(path.read_text('utf8'))
        if not isinstance(cached['clips'], dict):
            raise ValueError('字幕缓存格式无效')
        if not force and time.time() - cached['fetched_at'] < 86400:
            return cached
    except (OSError, ValueError, KeyError, TypeError):
        cached = None
    try:
        if not force and time.monotonic() < _cooldowns.get('hsdata', 0):
            raise ValueError('字幕源暂时冷却，请稍后重试')
        with urlopen(Request(url, headers={'User-Agent': 'PengPengWorkbench/0.2'}), timeout=8) as response:
            payload = response.read(8_000_001)
        if len(payload) > 8_000_000:
            raise ValueError('字幕表超过 8 MB')
        clips = parse_audio_strings(payload.decode('utf-8-sig'))
        if not clips:
            raise ValueError('字幕源返回空表')
        result = {'clips': clips, 'quotes': {}, 'source': url,
                  'source_name': 'HearthSim hsdata', 'fetched_at': time.time()}
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(result, ensure_ascii=False), encoding='utf8')
        temporary.replace(path)
        return result
    except Exception:
        # 同一轮切换多张卡时，网络故障不应逐卡重复下载整张表。
        _cooldowns['hsdata'] = time.monotonic() + 60
        if cached and cached.get('clips'):
            return {**cached, 'stale': True}
        raise


def fetch_source(workspace, source, identity, *, force=False):
    """每个配置和卡牌独立缓存；停用源不进入此函数，也不会使用其缓存。

    成功文字保留 30 天，空结果仅 1 天；网络失败不写空缓存，允许旧成功缓存
    离线使用。403/429 的短冷却只作用于该接口，不阻止后续来源。
    """
    from .transcripts import fetch_quotes, parse_html_quotes
    if source['kind'] == 'huiji':
        return fetch_quotes(workspace, identity['dbfid'], force=True) if force else fetch_quotes(workspace, identity['dbfid'])
    if source['kind'] == 'hsdata':
        return fetch_hsdata(workspace, force=force)
    if source['kind'] == 'wikigg' and not identity.get('name'):
        raise ValueError('卡牌缺少中文名称，无法定位 Wiki.gg 页面')
    url = source['url'].format(**{k: quote(str(v), safe='') for k, v in identity.items()})
    key = hashlib.sha256(json.dumps(source, sort_keys=True).encode()).hexdigest()
    path = Path(workspace) / 'cache' / 'transcripts-v3' / key / (hashlib.sha256(url.encode()).hexdigest() + '.json')
    cached = None
    try:
        cached = json.loads(path.read_text('utf8'))
        if not force and time.time() - cached['fetched_at'] < (30 * 86400 if cached['quotes'] else 86400):
            return cached
    except (OSError, ValueError, KeyError, TypeError):
        cached = None
    try:
        if not force and time.monotonic() < _cooldowns.get(key, 0):
            raise ValueError('来源暂时冷却，请稍后重试')
        with urlopen(Request(url, headers={'User-Agent': 'Mozilla/5.0 (compatible; PengPengWorkbench/0.2)'}), timeout=8) as response:
            payload = response.read(2_000_001)
        if len(payload) > 2_000_000:
            raise ValueError('来源响应超过 2 MB')
        if source['kind'] == 'baidu':
            quotes = parse_baidu(payload.decode('utf8'), identity['name'])
        else:
            data = json.loads(payload)
            if source['kind'] in ('mediawiki', 'wikigg'):
                if data.get('error', {}).get('code') == 'missingtitle':
                    quotes = {}
                elif 'error' in data:
                    raise ValueError('Wiki 接口返回错误，稍后可重试')
                else:
                    html = data['parse']['text']['*']
                    if source['kind'] == 'wikigg':
                        # 名称可能与其他模式卡牌重名；验证信息框的卡牌 ID。
                        quotes = parse_wikigg(html, identity['cardid'])
                    else:
                        quotes = parse_html_quotes(html)
            else:
                if str(data.get('dbfid')) != str(identity['dbfid']) or data.get('locale') != identity['locale']:
                    raise ValueError('接口返回的卡牌编号或语言不匹配')
                quotes = data['quotes']
        if not isinstance(quotes, dict) or any(k not in ('Play', 'Attack', 'Death') or not isinstance(v, str)
                or not v.strip() or len(v) > 2000 for k, v in quotes.items()):
            raise ValueError('台词格式无效')
        result = {'quotes': quotes, 'source': ('https://hearthstone.wiki.gg/zh/wiki/' + quote(identity['name'], safe='') if source['kind'] == 'wikigg' else url), 'source_name': source['name'], 'fetched_at': time.time()}
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(result, ensure_ascii=False), encoding='utf8')
        temporary.replace(path)
        return result
    except Exception as exc:
        if isinstance(exc, HTTPError) and exc.code in (403, 429):
            _cooldowns[key] = time.monotonic() + 60
        if cached and cached.get('quotes'):
            return {**cached, 'stale': True}
        raise
