"""本地字幕优先；公开台词使用展开后的页面，并保留来源和离线缓存。

卡牌同源引用定位在 Service 完成，本模块不接触 SQLite 或 Unity 对象。
仅匹配来源与本地各自唯一的基础事件，不猜随机分支，也不转写音频。
"""
from collections import defaultdict
from html import unescape
from html.parser import HTMLParser
from urllib.error import HTTPError
import json
from pathlib import Path
import re
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen


SOURCE = 'https://hearthstone.huijiwiki.com'
EVENTS = {'登场': 'Play', '召唤': 'Play', '出场': 'Play', '攻击': 'Attack', '死亡': 'Death',
          'Play': 'Play', 'Summon': 'Play', 'Attack': 'Attack', 'Death': 'Death'}


def _unique_quotes(found):
    """同一事件出现多条记录时不按顺序猜音频，即使文字相同也不合并。"""
    return {event: values[0] for event, values in found.items() if len(values) == 1}


def parse_quotes(wikitext):
    """兼容旧缓存/测试中的 wikitext；按模板深度寻找台词参数的边界。

    原先用换行加右花括号截断，会把多行 quote 的结束误当作卡牌模板结束。
    模板嵌套和链接只作为文本结构扫描，不执行模板。
    """
    start = re.search(r'\|台词\s*=', wikitext)
    if not start:
        return {}
    tail = wikitext[start.end():]
    depth, end = 0, len(tail)
    for token in re.finditer(r'\{\{|\}\}|^\s*\|[^\n=]+=', tail, re.M):
        value = token[0]
        if value == '{{':
            depth += 1
        elif value == '}}':
            if depth == 0:
                end = token.start()
                break
            depth -= 1
        elif depth == 0:
            end = token.start()
            break
    found = defaultdict(list)
    for block in re.split(r'(?m)^\s*\*\s*', tail[:end]):
        heading, _, body = block.partition('\n')
        event = EVENTS.get(heading.strip().strip("'"))
        if not event:
            continue
        for match in re.finditer(r'\{\{quote\s*\|([^{}]+)\}\}', body, re.I):
            value = re.split(r'<br\s*/?>', match[1], flags=re.I)[0].strip()
            if any(token in value for token in ('[[', ']]', '|', '<', '>')):
                continue
            value = unescape(value)
            if re.search(r'[\u4e00-\u9fff]', value):
                found[event].append(value)
    return _unique_quotes(found)


class QuoteHTMLParser(HTMLParser):
    """读取 MediaWiki 展开后的台词区；转引模板也能被正常解析。

    仅在“台词”二级标题内接受已知事件与 blockquote，避免从背景故事、
    趣闻或页面导航拿到同样出现“登场”的文字。HTML 永不进入应用 DOM。
    """
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.active = False
        self.heading = None
        self.heading_parts = []
        self.label = None
        self.label_parts = []
        self.event = None
        self.quote = None
        self.quote_tag = None
        self.found = defaultdict(list)
        self.has_section = False

    def handle_starttag(self, tag, attrs):
        if tag in ('h2', 'h3'):
            self.heading, self.heading_parts = tag, []
        if self.active and tag in ('li', 'dt'):
            self.label, self.label_parts = tag, []
        if self.active and tag in ('blockquote', 'dd') and self.quote is None:
            self.quote, self.quote_tag = [], tag
        if tag == 'br' and self.quote is not None:
            self.quote.append('\n')

    def handle_data(self, data):
        if self.heading:
            self.heading_parts.append(data)
        if self.label:
            self.label_parts.append(data)
        if self.quote is not None:
            self.quote.append(data)

    def handle_endtag(self, tag):
        if tag == self.heading:
            title = re.sub(r'\[.*?\]', '', ''.join(self.heading_parts)).strip()
            if tag == 'h2':
                self.active = title in ('台词', '语音', '卡牌语音', '音效', 'Sounds', 'Sound', 'Quotes')
                self.has_section |= self.active
                self.event = None
            elif self.active:
                self.event = EVENTS.get(title)
            self.heading = None
        if tag == self.label:
            self.event = EVENTS.get(''.join(self.label_parts).strip())
            self.label = None
        if tag == self.quote_tag and self.quote is not None:
            lines = [line.strip() for line in ''.join(self.quote).splitlines() if line.strip()]
            if self.event and lines and re.search(r'[\u4e00-\u9fff]', lines[0]):
                self.found[self.event].append(lines[0])
            self.quote, self.quote_tag = None, None


def parse_html_quotes(html):
    parser = QuoteHTMLParser()
    parser.feed(html)
    return _unique_quotes(parser.found)


def cache_quotes(workspace, dbfid, quotes):
    """HTTP 和隔离浏览窗口共用缓存格式，浏览器验证成功后可继续离线读取。"""
    dbfid = int(dbfid)
    if dbfid <= 0 or not isinstance(quotes, dict):
        raise ValueError('无效台词数据')
    if any(k not in EVENTS.values() or not isinstance(v, str) or not v.strip() or len(v) > 2000
           for k, v in quotes.items()):
        raise ValueError('无效台词格式')
    result = {'quotes': quotes, 'source': SOURCE + f'/wiki/Card/{dbfid}',
              'source_name': '炉石传说中文维基（灰机）', 'fetched_at': time.time()}
    path = Path(workspace) / 'cache' / 'transcripts-v2' / f'{dbfid}.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    # GUI 的浏览读取和后台 HTTP 可能同时完成，分别创建临时文件再原子替换。
    import tempfile
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                     suffix='.tmp', delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(result, stream, ensure_ascii=False)
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return result


# 短暂熔断保护站点和交互队列。每张卡仍优先读取各自缓存；不缓存为“无台词”。
_blocked_until = 0


class SourceBlocked(RuntimeError):
    pass


def fetch_quotes(workspace, dbfid, *, force=False):
    """读取展开 HTML，兼容已存在的成功缓存；旧版空缓存直接失效。"""
    global _blocked_until
    dbfid = int(dbfid)
    if dbfid <= 0:
        raise ValueError('卡牌缺少有效的 DBF ID')
    cached = None
    for version in ('transcripts-v2', 'transcripts-v1'):
        path = Path(workspace) / 'cache' / version / f'{dbfid}.json'
        try:
            data = json.loads(path.read_text('utf-8'))
            if not isinstance(data.get('quotes'), dict) or not isinstance(data.get('fetched_at'), (int, float)):
                continue
            if version == 'transcripts-v1' and not data['quotes']:
                continue
            cached = data
            ttl = 30 * 86400 if data['quotes'] else 86400
            if not force and time.time() - data['fetched_at'] < ttl:
                return data
            break
        except (ValueError, OSError, TypeError):
            continue
    url = SOURCE + '/api.php?' + urlencode({'action': 'parse', 'page': f'Card/{dbfid}',
                                           'prop': 'text', 'format': 'json', 'redirects': 1}, safe='/')
    try:
        if not force and time.monotonic() < _blocked_until:
            raise SourceBlocked('台词站点需要浏览器验证')
        request = Request(url, headers={'User-Agent': 'PengPengWorkbench/0.2 (local Hearthstone resource viewer)'})
        with urlopen(request, timeout=8) as response:
            payload = response.read(1_000_001)
        if len(payload) > 1_000_000:
            raise ValueError('台词来源响应过大')
        data = json.loads(payload)
        if 'error' in data:
            if data['error'].get('code') != 'missingtitle':
                raise ValueError('台词来源暂不可用')
            quotes = {}
        elif 'text' in data.get('parse', {}):
            quotes = parse_html_quotes(data['parse']['text']['*'])
        else:
            quotes = parse_quotes(data['parse']['wikitext']['*'])
        return cache_quotes(workspace, dbfid, quotes)
    except Exception as exc:
        blocked = isinstance(exc, SourceBlocked) or isinstance(exc, HTTPError) and exc.code in (403, 429)
        if isinstance(exc, HTTPError) and exc.code in (403, 429):
            _blocked_until = time.monotonic() + 60
        if cached and cached.get('quotes'):
            return {**cached, 'stale': True}
        if blocked:
            raise SourceBlocked('台词站点暂时拒绝自动查询，需要在浏览窗口完成验证后读取') from exc
        raise


def supplement(workspace, dbfid, items, locale='zhcn', providers=None, force=False):
    """精确字幕可覆盖任意语音；Wiki 只匹配唯一基础事件，始终保留已有文字。

    force 是用户主动重试：绕过本轮涉及来源的缓存与短冷却，不删除成功缓存。
    若重试仍离线，来源仍可返回标记为 stale 的旧成功结果。
    """
    from .audio_strings import audio_key
    from .transcript_sources import DEFAULT_SOURCES, validate_sources, fetch_source
    if locale != 'zhcn':
        return {'items': [], 'note': '公开补充来源目前仅支持简体中文。', 'sources': []}
    providers = validate_sources(DEFAULT_SOURCES if providers is None else providers)
    providers = [p for p in providers if p['enabled']]
    candidates = defaultdict(dict)
    missing = {item['id']: item for item in items
               if item.get('kind') == 'voice' and item.get('locale') == locale and not item.get('text')}
    # 唯一性必须基于所有音频，而不是仅缺字的音频，否则会把随机分支误认为唯一。
    for item in items:
        if item.get('kind') != 'voice' or item.get('locale') != locale:
            continue
        match = re.fullmatch(r'm_(Play|Attack|Death)EffectDef\.m_SoundSpellPaths\[\d+\]', item.get('event', ''))
        if match:
            candidates[(int(item.get('transcript_dbfid') or dbfid), match[1])][item['id']] = item
    eligible = {key: next(iter(clips.values())) for key, clips in candidates.items() if len(clips) == 1}
    results, sources, notes = [], [], []
    def accept(item, text, data, method):
        results.append({'id': item['id'], 'text': text, 'source': data['source'],
                        'source_name': data['source_name'], 'stale': data.get('stale', False),
                        'match_method': method})
        missing.pop(item['id'], None)
    for provider in providers:
        if not missing:
            break
        identities = [None] if provider['kind'] == 'hsdata' else list(dict.fromkeys(
            key[0] for key, item in eligible.items() if item['id'] in missing))
        count = 0
        stale = False
        for source in identities:
            remaining = {event: item for (identity, event), item in eligible.items()
                         if identity == source and item['id'] in missing}
            first = next(iter(remaining.values()), {})
            identity = {'dbfid': source, 'cardid': first.get('transcript_cardid', ''),
                        'name': first.get('transcript_name', ''), 'locale': locale}
            try:
                data = fetch_source(workspace, provider, identity, force=True) if force else fetch_source(workspace, provider, identity)
            except Exception as exc:
                if provider['id'] == 'huiji':
                    sources.append(source)
                reason = '需要浏览器验证' if isinstance(exc, SourceBlocked) else (f'HTTP {exc.code}' if isinstance(exc, HTTPError) else type(exc).__name__)
                notes.append(f"{provider['name']}：查询失败（{reason}），可重试")
                continue
            stale |= data.get('stale', False)
            if provider['kind'] == 'hsdata':
                for item in list(missing.values()):
                    text = data['clips'].get(audio_key(item.get('name', '')))
                    if text:
                        accept(item, text, data, 'audio_key')
                        count += 1
            else:
                for event, item in remaining.items():
                    if item['id'] in missing and event in data['quotes']:
                        accept(item, data['quotes'][event], data, 'event')
                        count += 1
        if identities:
            notes.append(f"{provider['name']}：补充 {count} 条" + ('（离线旧缓存）' if stale else ''))
    note = f'已补充 {len(results)} 条公开台词。' + '；'.join(dict.fromkeys(notes))
    if not providers:
        note = '未启用台词来源，可在设置中勾选。'
    elif missing:
        note += '；其余片段暂无可准确对应的台词，可稍后重试。'
    return {'items': results, 'note': note, 'sources': list(dict.fromkeys(sources))}
