"""ifindhs 语音表适配：按音频键配对，不把多条触发语音压成单个事件。"""
import hashlib
import json
import re
import time
from collections import defaultdict
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit
from urllib.request import Request, urlopen

from .audio_strings import audio_key

SOURCE = 'https://wiki.ifindhs.com'


def page_url(name):
    return SOURCE + '/index.php?title=' + quote(name, safe='')


class VoiceTable(HTMLParser):
    """只消费表格单元格和 audio 标记，忽略 onclick、脚本及站外内容。"""
    def __init__(self):
        super().__init__()
        self.rows, self.cells, self.cell, self.keys, self.skip = [], None, None, [], 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ('script', 'style'):
            self.skip += 1
        if tag == 'tr':
            self.cells, self.keys = [], []
        elif tag in ('td', 'th') and self.cells is not None:
            self.cell = []
        elif tag == 'audio' and self.cells is not None:
            key = attrs.get('id') or Path(unquote(urlsplit(attrs.get('src', '')).path)).stem
            if re.fullmatch(r'VO_[\w]+', key, re.I):
                self.keys.append(audio_key(key))

    def handle_data(self, data):
        if self.cell is not None and not self.skip:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.skip = max(0, self.skip-1)
        if tag in ('td', 'th') and self.cell is not None:
            self.cells.append(''.join(self.cell).strip())
            self.cell = None
        elif tag == 'tr' and self.cells is not None:
            self.rows.append((self.cells, self.keys))
            self.cells = None


def parse_page(html, cardid):
    parser = VoiceTable()
    parser.feed(html)
    # 名称不足以区分英雄与同名随从；必须核对信息框代码。
    verified = any(len(cells) >= 2 and cells[0].strip('：: ') == '代码'
                   and cardid.upper() in re.findall(r'[A-Za-z0-9_]+', cells[1].upper())
                   for cells, _ in parser.rows)
    if not verified:
        raise ValueError('ifindhs 页面卡牌代码不匹配，或页面仍需浏览器验证')
    found, events, triggers = defaultdict(set), defaultdict(set), {}
    mapping = {'打出': 'Play', '登场': 'Play', '攻击': 'Attack', '死亡': 'Death'}
    for cells, keys in parser.rows:
        if len(cells) < 2 or len(set(keys)) != 1:
            continue
        key = keys[0]
        text = cells[1].replace('▶', '').replace('\ufe0f', '').strip()
        if not text or re.fullmatch(r'<_.*_>', text) or len(text) > 2000:
            continue
        found[key].add(text)
        if cells[0] in mapping:
            events[mapping[cells[0]]].add(text)
        if '卡牌触发' in cells[0]:
            triggers[key] = re.sub(r'^.*?卡牌触发[：:]\s*', '', cells[0]).strip()
    return {'clips': {k: next(iter(v)) for k, v in found.items() if len(v) == 1},
            'quotes': {k: next(iter(v)) for k, v in events.items() if len(v) == 1},
            'triggers': triggers}


def cache_path(workspace, cardid):
    return Path(workspace) / 'cache' / 'ifindhs-v1' / (hashlib.sha256(cardid.encode()).hexdigest() + '.json')


def save_page(workspace, cardid, html, url):
    result = {**parse_page(html, cardid), 'source': url, 'source_name': '炉石传说 Wiki · ifindhs', 'fetched_at': time.time()}
    if not result['clips']:
        raise ValueError('页面没有可精确匹配的台词，未覆盖已有缓存')
    path = cache_path(workspace, cardid)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(result, ensure_ascii=False), 'utf8')
    temporary.replace(path)
    return result


def fetch(workspace, identity, force=False):
    cached = None
    path = cache_path(workspace, identity['cardid'])
    try:
        cached = json.loads(path.read_text('utf8'))
        if not force and time.time()-cached['fetched_at'] < 30*86400:
            return cached
    except (OSError, ValueError, KeyError):
        pass
    names = [identity['name']]
    if identity['cardid'].startswith('HERO_'):
        names.insert(0, identity['name'] + '（英雄）')
    error = None
    for name in names:
        try:
            url = page_url(name)
            with urlopen(Request(url, headers={'User-Agent': 'Mozilla/5.0 PengPengWorkbench/0.5'}), timeout=8) as response:
                payload = response.read(2_000_001)
            if len(payload) > 2_000_000:
                raise ValueError('Wiki 页面超过 2 MB')
            return save_page(workspace, identity['cardid'], payload.decode('utf8'), url)
        except Exception as exc:
            error = exc
    if cached and cached.get('clips'):
        return {**cached, 'stale': True}
    raise error or ValueError('Wiki 页面不可用')
