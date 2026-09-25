"""应用服务：本地索引、检索、媒体解码和导出。

全部由解析进程调用，不在 GUI 线程执行 Unity 解压。索引是可重建缓存，
导出是用户作品目录，两者分开保存。任何游戏资源错误都会保留诊断信息。
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import os
import re
import shutil
import time
import uuid
from collections import OrderedDict
from pathlib import Path

import soundfile as sf

from .common import (LOCALES, LOCALE_NAMES, audio_category, bundle_locale,
                     executable_version, fingerprint, guid, localized, references, safe_name)
from .storage import Store
from .catalog import CATALOG_VERSION, build_catalog, options as catalog_options
from datetime import date
from PIL import Image
from .images import crop_preview, portrait_preview
from .unity import UnityReader
from .transcript_sources import DEFAULT_SOURCES, validate_sources, migrate_sources
from .speech_config import DEFAULT_CONFIG, validate_config
from .audio_strings import audio_key
from .shared_audio import base_event, shared_rules, event_underlays
from .card_details import card_metadata, catalog_summaries
from .audio_library import update_labels, VERSION as AUDIO_LABEL_VERSION

log = logging.getLogger(__name__)


def publish_cache(temporary, destination):
    """普通解析和特效解析可能同时生成相同媒体，发布时只暴露完整文件。

    各进程使用自己的临时路径；若另一个进程已经完成，则保留它的文件，
    避免 Windows 播放器持有文件时再次替换而报共享冲突。
    """
    try:
        if not destination.exists():
            temporary.replace(destination)
    except PermissionError:
        if not destination.exists():
            raise
    finally:
        temporary.unlink(missing_ok=True)


class Service:
    def __init__(self, workspace: Path, emit=lambda event: None):
        self.workspace = workspace.resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.emit = emit
        self.store = None
        self.reader = None
        self.root = None
        self.cache = None
        self.settings_path = self.workspace / 'settings.json'
        self.settings = {'game_path': '', 'locale': 'zhcn', 'paired_audio': False, 'general_audio': False, 'scroll_mode': 'smooth', 'infinite_scroll': False, 'page_size': 24, 'online_transcripts': True, 'transcript_sources': [dict(s) for s in DEFAULT_SOURCES],
                         'speech_config': dict(DEFAULT_CONFIG), 'speech_api_key': '',
                         'speech_recognition': True, 'source_defaults_version': 0, 'index_guide_seen': False,
                         'mix_voice_export': False, 'view_state': {}, 'ui_scale': 1.0, 'font_scale': 1.0,
                         'auto_check_updates': True, 'voice_page_size': 24,
                         'catalog_density_version': 0, 'export_path': str(self.workspace / 'exports')}
        if self.settings_path.exists():
            try:
                self.settings.update(json.loads(self.settings_path.read_text('utf-8')))
            except (ValueError, OSError) as exc:
                log.warning('设置文件无法读取，使用默认设置：%s', exc)
        self.settings['transcript_sources'] = migrate_sources(self.settings['transcript_sources'])
        if self.settings['catalog_density_version'] < 1:
            for view in ('cards', 'heroes'):
                display = self.settings.get('view_state', {}).get(view, {}).get('display', {})
                if display.get('size') == 220:
                    display['size'] = 180
            self.settings['catalog_density_version'] = 1
        # 一次性迁移旧默认值；之后尊重用户手动开启和排序，不在启动时反复重置。
        if self.settings['source_defaults_version'] < 1:
            sources = self.settings['transcript_sources']
            tail = ('hsdata', 'wikigg')
            self.settings['transcript_sources'] = [s for s in sources if s['id'] not in tail] + [
                {**s, 'enabled': False} for identity in tail for s in sources if s['id'] == identity]
            self.settings['source_defaults_version'] = 1
            self.save_settings()
        self.strings = {}
        self.audio_relations = OrderedDict()
        self.card_voices = OrderedDict()
        self.effect_previews = OrderedDict()
        self.shared_spell_table = None
        self.card_tags = None
        self.voice_cache_stamp = {}

    def save_settings(self, **values):
        if 'scroll_mode' in values and values['scroll_mode'] not in ('smooth', 'instant'):
            raise ValueError('滚动方式必须为轻量平滑或即时滚动')
        if 'auto_check_updates' in values and type(values['auto_check_updates']) is not bool:
            raise ValueError('自动检查更新必须为开关值')
        if 'voice_page_size' in values and (type(values['voice_page_size']) is not int or values['voice_page_size'] not in (12, 24, 48, 96)):
            raise ValueError('语音每页数量必须为 12、24、48 或 96')
        for key, bounds in [('ui_scale', (0.8, 1.4)), ('font_scale', (0.85, 1.5))]:
            if key in values and (type(values[key]) not in (int, float) or not bounds[0] <= values[key] <= bounds[1]):
                raise ValueError('缩放倍率超出支持范围')
        if 'mix_voice_export' in values and type(values['mix_voice_export']) is not bool:
            raise ValueError('合并导出选项必须为开关值')
        if 'view_state' in values:
            from .preferences import validate_views
            values['view_state'] = validate_views(values['view_state'])
        if 'speech_recognition' in values and type(values['speech_recognition']) is not bool:
            raise ValueError('语音识别选项必须为开关值')
        if 'speech_config' in values:
            values['speech_config'] = validate_config(values['speech_config'])
        if 'transcript_sources' in values:
            values['transcript_sources'] = validate_sources(values['transcript_sources'])
        if 'general_audio' in values and type(values['general_audio']) is not bool:
            raise ValueError('通用音效选项必须为开关值')
        if 'export_path' in values and not str(values['export_path']).strip():
            raise ValueError('请选择有效的导出目录')
        if 'page_size' in values and values['page_size'] not in (24, 48, 72, 96):
            raise ValueError('每页数量必须为 24、48、72 或 96')
        if 'speech_api_key' in values:
            key = values['speech_api_key']
            if not isinstance(key, str) or len(key) > 4096 or any(c in key for c in '\r\n'):
                raise ValueError('API 密钥格式无效')
            values['speech_api_key'] = key.strip()
        self.settings.update({k: v for k, v in values.items() if k in self.settings})
        temporary = self.settings_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.settings, ensure_ascii=False, indent=2), 'utf-8')
        temporary.replace(self.settings_path)
        return self.settings

    def initialize(self, game_path=None, effect_only=False):
        from .game_updates import probe, analyze
        update = probe(self.workspace, game_path or self.settings['game_path']) if not effect_only else None
        def progress(message, done=0, total=0):
            self.emit({'event': 'progress', 'message': message, 'done': done, 'total': total})
        progress('验证安装目录与资源指纹')
        root = Path(game_path or self.settings['game_path']).resolve()
        paths = [root / 'Data/Win/asset_manifest.unity3d', root / 'Data/Win/dbf.unity3d']
        if not all(p.is_file() for p in paths):
            raise ValueError('请选择炉石安装目录（其下需要有 Data/Win/asset_manifest.unity3d 和 dbf.unity3d）')
        paths += list((root / 'Data/Win').glob('asset_manifest_*.unity3d'))
        version = fingerprint(paths)
        # 不同安装 / 补丁使用独立索引，防止旧版 GUID 和新包混用。
        cache = self.workspace / 'cache' / version
        cache.mkdir(parents=True, exist_ok=True)
        store = Store(cache / 'index.sqlite3')
        try:
            reader = UnityReader(root, store, progress)
        except Exception:
            store.close()
            raise
        if self.store:
            self.store.close()
        self.store, self.reader, self.root, self.cache = store, reader, root, cache
        self.audio_relations.clear()
        self.card_voices.clear()
        self.effect_previews.clear()
        self.shared_spell_table = None
        self.card_tags = None
        self.voice_cache_stamp.clear()
        # 特效进程连接现有索引即可，不重建图鉴、不改设置，避免并发写设置文件。
        if effect_only:
            return {}
        self.save_settings(game_path=str(root))
        if not self.store.get_meta('cards_ready'):
            self._build_cards()
        if self.store.get_meta('filters_version') != CATALOG_VERSION or self.store.get_meta('rotation_year') != date.today().year:
            build_catalog(self)
            self.store.set_meta('immune_keyword_version', None)
        from .catalog import KEYWORD_VERSION, update_immune_keywords
        if self.store.get_meta('immune_keyword_version') != KEYWORD_VERSION:
            update_immune_keywords(self.store)
        progress('读取客户端字幕')
        self._load_strings()
        # 命名分类规则升级只重算已索引文本，不重新解压全部音频包。
        if self.store.get_meta('audio_category_version') != 4:
            with self.store.db:
                rows = self.store.db.execute("SELECT id,name,bundle,duration FROM assets WHERE kind='AudioClip'").fetchall()
                self.store.db.executemany('UPDATE assets SET category=? WHERE id=?',
                    [(audio_category(r['name'], r['bundle'], r['duration']), r['id']) for r in rows])
            self.store.set_meta('audio_category_version', 4)
        if self.store.get_meta('audio_label_version') != AUDIO_LABEL_VERSION:
            with self.store.db:
                update_labels(self.store, self.store.db.execute("SELECT * FROM assets WHERE kind='AudioClip'").fetchall())
            self.store.set_meta('audio_label_version', AUDIO_LABEL_VERSION)
        # 二次启动前移除已经从磁盘消失的资源包记录，避免将已卸载语音显示为可用。
        progress('核对已安装资源包')
        bundles = set(self.reader.source_paths())
        with self.store.db:
            for row in self.store.db.execute('SELECT name FROM bundles').fetchall():
                if row['name'] not in bundles:
                    self.store.db.execute('DELETE FROM assets WHERE bundle=?', (row['name'],))
                    self.store.db.execute('DELETE FROM bundles WHERE name=?', (row['name'],))
        progress('本地档案已就绪', 1, 1)
        analyze(self, update)
        return self.status()

    def game_status(self, game_path=None):
        """启动、回到窗口及定期检查，只读取清单的大小和修改时间。"""
        from .game_updates import probe
        return probe(self.workspace, game_path or self.settings['game_path'])

    def _build_cards(self):
        self.emit({'event': 'progress', 'message': '正在读取本地卡牌文本和英雄皮肤…', 'done': 0, 'total': 0})
        env = self.reader.load('dbf.unity3d')
        records = env.container['Assets/Game/DBF-Asset/CARD.asset'].read_typetree()['Records']
        heroes = env.container['Assets/Game/DBF-Asset/CARD_HERO.asset'].read_typetree()['Records']
        hero_map = {r['m_cardId']: r for r in heroes}
        seen = set()
        with self.store.db:
            for index, record in enumerate(records):
                if index % 500 == 0:
                    self.emit({'event': 'progress', 'message': '保存卡牌文本', 'done': index, 'total': len(records)})
                cid = record['m_noteMiniGuid']
                if not cid:
                    continue
                seen.add(cid)
                data = {k: record.get(k, {}) for k in ('m_name', 'm_textInHand', 'm_flavorText',
                    'm_howToGetCard', 'm_howToGetGoldCard', 'm_howToGetSignatureCard', 'm_howToGetDiamondCard')}
                data['artist'] = record.get('m_artistName', '')
                data['dbfid'] = record['m_ID']
                if record['m_ID'] in hero_map:
                    data['hero_description'] = hero_map[record['m_ID']].get('m_description', {})
                is_hero = record['m_ID'] in hero_map or cid.startswith(('HERO_', 'TB_BaconShop_HERO_', 'BG_HERO_'))
                self.store.db.execute('INSERT OR REPLACE INTO cards VALUES (?,?,?,?,?,?,NULL)',
                    (cid, localized(record, 'm_name'), localized(record, 'm_name', 'enus'), int(is_hero),
                     self.reader.cards.get(cid, ''), json.dumps(data, ensure_ascii=False)))
            # 资源清单中有定义但 DBF 没有名字的内部卡牌也保留。
            for cid in self.reader.cards.keys() - seen:
                self.store.db.execute('INSERT OR REPLACE INTO cards VALUES (?,?,?,?,?,?,NULL)',
                    (cid, cid, cid, int(cid.startswith('HERO_')), self.reader.cards[cid], '{}'))
        self.store.set_meta('cards_ready', True)
        log.info('卡牌文本索引完成：%s', self.store.db.execute('SELECT COUNT(*) FROM cards').fetchone()[0])

    def _load_strings(self):
        self.strings.clear()
        for index, locale in enumerate(LOCALES):
            self.emit({'event': 'progress', 'message': '读取客户端字幕 · ' + locale, 'done': index, 'total': len(LOCALES)})
            folder = self.root / 'Strings' / (locale[:2] + locale[2:].upper())
            values = {}
            for path in folder.glob('*.txt'):
                # 制作人员名单是富文本，不是 TAG/TEXT 表，不能交给 TSV 字幕解析。
                if path.name.upper().startswith('CREDITS_'):
                    continue
                # 与 hsdata 使用同一 TSV 规则，避免两条路径产生不同字幕键。
                from .audio_strings import parse_audio_strings
                values.update(parse_audio_strings(path.read_text(encoding='utf-8-sig')))
            if values:
                self.strings[locale] = values

    def audio_installed(self, locale):
        """语言清单或文本存在不等于音频存在，必须检查实际文件。"""
        if not self.reader:
            return False
        return any(bundle_locale(name) == locale and 'audio' in name.lower()
                   for name in self.reader.source_paths())

    def card_render(self, cardid, locale='zhcn', variant=0):
        """按需缓存完整卡面。只请求固定图源，不把本地路径或用户数据发往网络。

        成品图与本地原画分目录缓存；网络失败保留原画功能，损坏响应不会写入缓存。
        请求只在用户打开完整卡面时发生，超时后可以重试。
        """
        from urllib.request import urlopen, Request
        from urllib.error import URLError
        if not re.fullmatch(r'[A-Za-z0-9_]+', cardid) or locale not in LOCALES:
            raise ValueError('无效的卡牌或语言')
        if type(variant) is not int or variant not in range(4):
            raise ValueError('无效的卡面品质')
        if variant:
            # 网站的品质后缀来自其实际卡面组件。只下载用户选中的卡面，
            # 不用普通卡面加金边冒充金卡；网络缺图也不回退成错误品质。
            if locale != 'zhcn':
                raise ValueError('动态完整卡面图源目前提供简体中文；本地原画支持已安装语言。')
            suffix = {1: '', 2: '_SIG', 3: '_DIA'}[variant]
            url = f'https://search.ifindhs.com/webms/latest/{cardid}{suffix}.webm'
            path = self.cache / 'renders' / locale / f'{cardid}-{variant}.webm'
            if not path.exists():
                try:
                    with urlopen(Request(url, headers={'User-Agent': 'PengPengWorkbench/0.5'}), timeout=15) as response:
                        payload = response.read(40*1024*1024+1)
                    if len(payload) > 40*1024*1024 or not payload.startswith(b'\x1aE\xdf\xa3'):
                        raise ValueError('图源未返回有效 WebM')
                    path.parent.mkdir(parents=True, exist_ok=True)
                    temporary = path.with_suffix('.tmp')
                    temporary.write_bytes(payload)
                    temporary.replace(path)
                except (URLError, OSError, ValueError) as exc:
                    raise ValueError('此品质完整卡面暂不可用，可重试或查看本地原画。') from exc
            return {'url': path.as_uri(), 'path': str(path), 'media': 'video', 'source_url': url,
                    'source': 'ifindhs · 在线最新' + {1: '金卡', 2: '异画', 3: '钻石'}[variant] + '动态卡面'}
        path = self.cache / 'renders' / locale / (cardid + '.png')
        url = f'https://art.hearthstonejson.com/v1/render/latest/{locale[:2] + locale[2:].upper()}/512x/{cardid}.png'
        if not path.exists():
            try:
                with urlopen(Request(url, headers={'User-Agent': 'PengPengWorkbench/0.2'}), timeout=12) as response:
                    payload = response.read(12 * 1024 * 1024 + 1)
                if len(payload) > 12 * 1024 * 1024:
                    raise ValueError('卡图响应过大')
                with Image.open(io.BytesIO(payload)) as image:
                    if image.format != 'PNG':
                        raise ValueError('图源未返回 PNG 卡图')
                    image.verify()
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_suffix('.tmp')
                temporary.write_bytes(payload)
                temporary.replace(path)
            except (URLError, OSError, ValueError) as exc:
                raise ValueError('完整卡图暂不可用（离线或图源尚未收录），可继续查看本地原画。') from exc
        with Image.open(path) as image:
            width, height = image.size
        return {'url': path.as_uri(), 'path': str(path), 'width': width, 'height': height,
                'source': 'HearthstoneJSON · 在线最新普通卡面', 'source_url': url}

    def status(self):
        if not self.store:
            return {'ready': False, 'settings': self.settings}
        db = self.store.db
        counts = {r[0]: r[1] for r in db.execute('SELECT kind,COUNT(*) FROM assets GROUP BY kind')}
        total = len(self.reader.source_paths())
        return {'ready': True, 'settings': self.settings, 'version': self.cache.name,
                'game_version': executable_version(self.root / 'Hearthstone.exe'),
                'update_index_pending': self.store.get_meta('update_index_pending', False),
                'asset_comparison_available': self.store.get_meta('asset_comparison_available'),
                'cards': db.execute('SELECT COUNT(*) FROM cards WHERE hero=0').fetchone()[0],
                'heroes': db.execute('SELECT COUNT(*) FROM cards WHERE hero=1').fetchone()[0],
                'assets': counts, 'indexed': db.execute("SELECT COUNT(*) FROM bundles WHERE error='' ").fetchone()[0],
                'failed': db.execute("SELECT COUNT(*) FROM bundles WHERE error<>''").fetchone()[0],
                'bundles': total, 'filters': catalog_options(self.store), 'locales': [{'code': k, 'name': LOCALE_NAMES[k],
                    'audioInstalled': self.audio_installed(k)} for k in ('zhcn', 'enus', 'zhtw') + tuple(x for x in LOCALES if x not in ('zhcn', 'enus', 'zhtw'))]}

    def list_cards(self, query='', hero=False, offset=0, limit=48, favorites=False, locale='zhcn', filters=None,
                   sort='default', descending=False, battlegrounds=False):
        # 两个入口的分类条件必须对称；收藏和搜索只能进一步缩小范围。
        # hero 标记由图鉴标签构建，可收集英雄牌仍归入卡牌页。
        where, args = ['hero=?'], [int(bool(hero))]
        # 完整 ID 优先精确定位：EX1_302 不能因 CORE_EX1_302 同组且排序
        # 更靠前而被替换。普通名称、文本与不完整 ID 仍使用全文包含查询。
        exact = self.store.db.execute('SELECT id FROM cards WHERE id=? COLLATE NOCASE',
                                      (query.strip(),)).fetchone() if query else None
        if exact:
            where.append('id=?')
            args.append(exact['id'])
        elif query:
            # LIKE 中的 % 和 _ 是普通搜索文字；不让用户输入意外变成通配符。
            term = '%' + query.strip().replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
            fields = ['id', 'name', 'ename'] + [
                f"json_extract(data, '$.{field}.m_locValues')"
                for field in ('m_name', 'm_textInHand', 'm_flavorText', 'hero_description')]
            where.append('(' + ' OR '.join(f"{field} LIKE ? ESCAPE '\\'" for field in fields) + ')')
            args.extend([term] * len(fields))
        if favorites:
            where.append("id IN (SELECT id FROM favorites WHERE kind='card')")
        # 筛选值一律绑定 SQL 参数；只允许固定列名，避免把界面输入拼入 SQL。
        filters = filters or {}
        if str(filters.get('new', '')) == '1':
            where.append("id IN (SELECT id FROM new_content WHERE kind='card')")
        # 独立战棋入口只收录随从。传统图鉴排除战棋实体，英雄入口保持皮肤筛选。
        columns = {r[1] for r in self.store.db.execute('PRAGMA table_info(card_filters)')}
        if not hero and 'bg' in columns:
            where.append('id IN (SELECT id FROM card_filters WHERE bg=?' + (' AND card_type=4)' if battlegrounds else ')'))
            args.append(int(bool(battlegrounds)))
        if filters.get('race'):
            where.append("id IN (SELECT d.id FROM card_details d,json_each(d.data,'$.races') r WHERE r.value=?)")
            args.append(int(filters['race']))
        if filters.get('keyword'):
            where.append('id IN (SELECT id FROM card_keywords WHERE keyword=?)')
            args.append(filters['keyword'])
        if filters.get('tier'):
            where.append("id IN (SELECT id FROM card_details WHERE json_extract(data,'$.tier')=?)")
            args.append(int(filters['tier']))
        if filters.get('bg_pool'):
            where.append("id IN (SELECT id FROM card_details WHERE json_extract(data,'$.bg_pool')=?)")
            args.append(int(filters['bg_pool']))
        for key, column in {'rarity': 'rarity', 'type': 'card_type',
                            'hero_group': 'hero_group', 'collectible': 'collectible'}.items():
            if str(filters.get(key, '')) != '':
                where.append(f'id IN (SELECT id FROM card_filters WHERE {column}=?)')
                args.append(filters[key])
        if str(filters.get('class', '')) != '':
            where.append('id IN (SELECT id FROM card_classes WHERE class_id=?)')
            args.append(int(filters['class']))
        if filters.get('set'):
            where.append('id IN (SELECT id FROM card_editions WHERE edition=?)' if str(filters['set']).startswith('edition:')
                         else 'id IN (SELECT id FROM card_sets WHERE set_id=?)')
            args.append(filters['set'])
        if str(filters.get('cost', '')) != '':
            operator = '>=' if str(filters['cost']) == '10+' else '='
            where.append(f'id IN (SELECT id FROM card_filters WHERE cost{operator}?)')
            args.append(10 if operator == '>=' else int(filters['cost']))
        for key, allowed in [('format', {'standard': ('standard', 1), 'wild': ('wild', 1), 'bg': ('bg', 1)}),
                             ('battlegrounds', {'exclude': ('bg', 0), 'only': ('bg', 1)})]:
            if filters.get(key) in allowed:
                column, value = allowed[filters[key]]
                where.append(f'id IN (SELECT id FROM card_filters WHERE {column}=?)')
                args.append(value)
        clause = ' AND '.join(where)
        # 排序在 LIMIT 之前完成；白名单隔离 SQL 语法，ID 作为稳定的并列键。
        # 名称使用与显示一致的“所选语言 → 英文 → 索引名称”回退。
        if sort not in ('default', 'name', 'id', 'release') or type(descending) is not bool:
            raise ValueError('无效的排序方式')
        language = LOCALES.index(locale) if locale in LOCALES else 12
        name_sql = (f"COALESCE(NULLIF(json_extract(data, '$.m_name.m_locValues[{language}]'), ''), "
                    "NULLIF(json_extract(data, '$.m_name.m_locValues[0]'), ''), name)")
        release_sql = '(SELECT release_date FROM card_releases r WHERE r.id=cards.id)'
        direction = 'DESC' if descending else 'ASC'
        orders = {
            'default': f"CASE WHEN id LIKE 'CORE_%' THEN 0 WHEN id LIKE 'HERO_%' THEN 1 ELSE 2 END {direction}, id {direction}",
            'id': f'id {direction}',
            'name': f'{name_sql} COLLATE NOCASE {direction}, id {direction}',
            # 未知日期始终在末尾，倒序也不能把空值误认为最新发布。
            'release': f'{release_sql} IS NULL ASC, {release_sql} {direction}, id {direction}',
        }
        # 先筛选，再按同名同图同类型分组。窗口函数在分页之前执行，避免跨页
        # 重复或页数失真；精确 ID 查询仍直接命中该版本，不被组内首选替换。
        grouped = self.store.db.execute("SELECT 1 FROM sqlite_master WHERE name='card_groups'").fetchone()
        cte = ''
        if grouped:
            original_clause, original_args = clause, list(args)
            cte = '''WITH matched AS (SELECT cards.*, COALESCE((SELECT group_key FROM card_groups g WHERE g.id=cards.id),id) AS gkey
                FROM cards WHERE ''' + original_clause + '''), ranked AS (SELECT id,
                ROW_NUMBER() OVER(PARTITION BY gkey ORDER BY
                CASE WHEN id LIKE 'CORE_%' THEN 0 WHEN id IN (SELECT id FROM card_filters WHERE collectible=1) THEN 1 ELSE 2 END,id) AS position
                FROM matched) '''
            clause = 'id IN (SELECT id FROM ranked WHERE position=1)'
            args = original_args
        count = self.store.db.execute(cte + 'SELECT COUNT(*) FROM cards WHERE ' + clause, args).fetchone()[0]
        dated_total = (self.store.db.execute(cte + 'SELECT COUNT(*) FROM cards WHERE ' + clause +
                       f' AND {release_sql} IS NOT NULL', args).fetchone()[0] if sort == 'release' else None)
        rows = self.store.db.execute(cte + f'SELECT id,name,hero,guid,data,{release_sql} AS release_date, '
            '(SELECT source FROM card_releases r WHERE r.id=cards.id) AS release_source FROM cards WHERE ' + clause +
            ' ORDER BY ' + orders[sort] + ' LIMIT ? OFFSET ?', (*args, max(1, min(int(limit), 100)), max(0, int(offset)))).fetchall()
        summaries = catalog_summaries(self.store, [r['id'] for r in rows])
        group_counts = {}
        if grouped and rows:
            marks = ','.join('?' for _ in rows)
            group_counts = dict(self.store.db.execute(f'''SELECT a.id,COUNT(b.id) FROM card_groups a
                JOIN card_groups b ON a.group_key=b.group_key WHERE a.id IN ({marks}) GROUP BY a.id''',
                [r['id'] for r in rows]).fetchall())
        new_ids = {r[0] for r in self.store.db.execute("SELECT id FROM new_content WHERE kind='card'")}
        return {'total': count, 'dated_total': dated_total, 'items': [{'id': r['id'], 'name': localized(json.loads(r['data']), 'm_name', locale) or r['name'],
            'summary': summaries.get(r['id'], {}),
            'is_new': r['id'] in new_ids,
            'version_count': group_counts.get(r['id'], 1),
            'hero': bool(r['hero']), 'has_definition': bool(r['guid']), 'release_date': r['release_date'],
            'release_source': r['release_source'] or '日期未知'} for r in rows]}

    def card(self, cardid, locale='zhcn'):
        row = self.store.db.execute('SELECT * FROM cards WHERE id=?', (cardid,)).fetchone()
        if not row:
            raise ValueError('卡牌不存在')
        record = json.loads(row['data'])
        definition = json.loads(row['definition']) if row['definition'] else {}
        if not definition and row['guid']:
            definition = self.reader.definition(cardid)
            with self.store.db:
                self.store.db.execute('UPDATE cards SET definition=? WHERE id=?', (json.dumps(definition), cardid))
        variants = []
        fields = [('普通', 'm_PortraitTexturePath', None),
                  ('金卡', 'm_GoldenPortraitTexturePath', 'm_GoldenPortraitMaterialPath'),
                  ('异画', 'm_SignaturePortraitTexturePath', 'm_SignaturePortraitMaterialPath'),
                  ('钻石', 'm_DiamondPortraitTexturePath', 'm_DiamondModel')]
        for label, field, material in fields:
            texture = definition.get(field) or definition.get(field.replace('Golden', 'Premium'), '')
            mat = definition.get(material) or definition.get((material or '').replace('Golden', 'Premium'), '')
            variants.append({'label': label, 'texture': texture, 'material': mat,
                'available': bool(texture or mat), 'shared_texture': definition.get('m_PortraitTexturePath', '')})
        # 某些英雄的节日外观不使用独立卡牌 ID，而是挂在 SpecialEvents 中。
        for event in definition.get('m_SpecialEvents', []):
            texture = event.get('m_PortraitTextureOverride', '')
            golden = event.get('m_GoldenPortraitTextureOverride') or event.get('m_PremiumPortraitTextureOverride', '')
            material = event.get('m_GoldenPortraitMaterialOverride') or event.get('m_PremiumPortraitMaterialOverride', '')
            if texture:
                variants.append({'label': f'主题 {event.get("EventType", "")} 原画', 'texture': texture,
                                 'material': '', 'available': True, 'shared_texture': ''})
            if golden or material:
                variants.append({'label': f'主题 {event.get("EventType", "")} 金卡', 'texture': golden,
                                 'material': material, 'available': True, 'shared_texture': texture})
        effects = []
        for field, value in references(definition):
            if '.prefab:' in value.lower():
                effects.append({'field': field, 'ref': value, 'name': value.split(':')[0],
                                'speech': 'Sound' in field or 'Announcer' in field})
        metadata, related = card_metadata(self.store, cardid, locale)
        from .card_relations import text_links
        from .encyclopedia import versions
        editions = versions(self.store, cardid, locale)
        voice_links = []
        if self.store and self.store.db.execute("SELECT 1 FROM sqlite_master WHERE name='voice_links'").fetchone():
            linked = self.store.db.execute('''SELECT DISTINCT c.id,c.name,c.data,c.hero FROM cards c
                JOIN voice_links v ON c.id=CASE WHEN v.hero=? THEN v.card ELSE v.hero END
                WHERE v.hero=? OR v.card=?''', (cardid, cardid, cardid)).fetchall()
            voice_links = [{'id': r['id'], 'name': localized(json.loads(r['data']), 'm_name', locale) or r['name'],
                            'hero': bool(r['hero'])} for r in linked]
        strings = self.strings.get(locale, {})
        keywords = [{'name': strings.get(k['name_key'], ''), 'text': strings.get(k['text_key'], '')}
                    for k in self.store.get_meta('keywords', [])]
        return {'id': cardid, 'name': localized(record, 'm_name', locale) or row['name'],
                'metadata': metadata, 'related': related, 'versions': editions, 'voice_links': voice_links,
                'text_links': text_links(cardid, localized(record, 'm_textInHand', locale), related),
                'keywords': [k for k in keywords if k['name'] and k['text']],
                'text': localized(record, 'm_textInHand', locale), 'flavor': localized(record, 'm_flavorText', locale),
                'description': localized(record, 'hero_description', locale), 'artist': record.get('artist', ''),
                'hero': bool(row['hero']), 'variants': variants, 'effects': effects,
                'favorite': bool(self.store.db.execute("SELECT 1 FROM favorites WHERE kind='card' AND id=?", (cardid,)).fetchone()),
                'definition': definition, 'record': record}

    def thumbnail(self, cardid, locale='zhcn'):
        # 缩略图只需要普通原画引用，不为每张图反复构造词条、版本和关系详情。
        row = self.store.db.execute('SELECT guid,definition FROM cards WHERE id=?', (cardid,)).fetchone()
        if not row:
            raise ValueError('卡牌不存在')
        definition = json.loads(row['definition']) if row['definition'] else self.reader.definition(cardid) if row['guid'] else {}
        texture = definition.get('m_PortraitTexturePath')
        if not texture:
            return {'url': ''}
        obj, _, _ = self.reader.resolve(texture, locale)
        return self._image(obj, thumbnail=True, portrait_rgb=True)

    def _image(self, obj, thumbnail=False, portrait_rgb=False):
        key = hashlib.sha256(f'v6:{obj.assets_file.name}:{obj.path_id}:{thumbnail}:{portrait_rgb}'.encode()).hexdigest()[:24]
        path = self.cache / 'images' / f'{key}.png'
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            image = obj.read().image
            if portrait_rgb:
                image = image.convert('RGB')
            if thumbnail:
                # 只对预览裁去透明留白；无损导出保留完整像素与 Alpha。
                image = portrait_preview(image) if portrait_rgb else crop_preview(image)
                image.thumbnail((480, 560))
            temporary = path.with_suffix(f'.{os.getpid()}.tmp')
            image.save(temporary, format='PNG')
            publish_cache(temporary, path)
        with Image.open(path) as stored:
            width, height = stored.size
        return {'url': path.as_uri(), 'path': str(path), 'name': obj.read().m_Name,
                'width': width, 'height': height, 'source': '本地原始纹理', 'thumbnail': thumbnail}

    def portrait(self, cardid, variant=0, locale='zhcn'):
        data = self.card(cardid, locale)['variants'][int(variant)]
        images, errors = [], []
        if not data['available']:
            return {'images': [], 'note': '本地 CardDef 没有该品质资源', 'errors': []}
        if variant == 3 and not data['texture']:
            return {'images': [], 'note': '钻石资源只有三维模型，当前版本尚不支持模型渲染。', 'errors': []}
        if data['texture']:
            obj, _, _ = self.reader.resolve(data['texture'], locale)
            images.append({'slot': '原画 RGB（完整绘画）', **self._image(obj, portrait_rgb=True)})
            images.append({'slot': '原始 RGBA（含材质蒙版）', **self._image(obj)})
        elif data['shared_texture']:
            obj, _, _ = self.reader.resolve(data['shared_texture'], locale)
            images.append({'slot': '共享基础原画（动态效果见材质图层）', **self._image(obj)})
        if data['material'] and '.mat:' in data['material']:
            textures, tree, material_errors = self.reader.material_textures(data['material'], locale)
            errors.extend(material_errors)
            # 所有图层可见，不把金卡误当成普通卡涂上黄色边框。
            for slot, obj in textures:
                try:
                    images.append({'slot': slot, **self._image(obj)})
                except Exception as exc:
                    errors.append(f'{slot}: {exc}')
        if not images and data['shared_texture']:
            obj, _, _ = self.reader.resolve(data['shared_texture'], locale)
            images.append({'slot': '共享基础纹理', **self._image(obj)})
        return {'images': images, 'errors': errors, 'note': ('金卡 / 异画展示原始纹理及材质图层；未复现游戏专用动态着色器。'
            if data['material'] else '本地原画纹理；不含游戏运行时合成的卡框。')}

    def scan(self, scope='all'):
        """生成器每次仅扫描一个包；进程调度器在包间处理搜索和预览请求。"""
        files = list(self.reader.source_paths().items())
        failed = 0
        if scope == 'music':
            # 音乐散落在专用音乐包、随从/法术包和 Player 内置资源中。
            # 定向索引全部通用音频包，不只搜索名称含 music 的包。
            files = [(name, path) for name, path in files
                     if ('audio' in name and bundle_locale(name) == 'global') or name.startswith('@player/')]
        elif scope != 'all':
            raise ValueError('未知索引范围')
        files.sort(key=lambda pair: (0 if 'audio' in pair[0] else 1 if 'prefab' in pair[0] else 2, pair[0]))
        for index, (bundle_name, path) in enumerate(files):
            stat = path.stat()
            stamp = f'{stat.st_size}:{stat.st_mtime_ns}'
            previous = self.store.db.execute('SELECT stamp,error FROM bundles WHERE name=?', (bundle_name,)).fetchone()
            if previous and previous['stamp'] == stamp and not previous['error']:
                yield {'done': index + 1, 'total': len(files), 'message': '已复用索引 · ' + path.name}
                continue
            if previous:
                self.audio_relations.clear()
                self.card_voices.clear()
            started = time.monotonic()
            try:
                # 扫描发现文件更新时，不能复用预览曾加载的旧环境。
                if previous and hasattr(self.reader, 'cache'):
                    self.reader.cache.pop(bundle_name, None)
                env = self.reader.load(bundle_name)
                containers = {}
                for key, pointer in env.container.items():
                    if pointer.path_id:
                        containers[(pointer.assetsfile.name, pointer.path_id)] = key
                rows = []
                for obj in env.objects:
                    kind = obj.type.name
                    container = containers.get((obj.assets_file.name, obj.path_id), '')
                    if kind != 'AudioClip' and not (container and kind in ('GameObject', 'Texture2D', 'VideoClip')):
                        continue
                    # 纹理的类型树可能包含整幅像素；索引仅需名称，不能把正文读进来。
                    tree = obj.read_typetree() if kind == 'AudioClip' else {'m_Name': obj.peek_name() or container}
                    name = tree.get('m_Name', container)
                    category = audio_category(name, bundle_name, tree.get('m_Length', 0)) if kind == 'AudioClip' else bundle_name.split('_')[0]
                    assetid = hashlib.sha256(f'{bundle_name}:{obj.path_id}'.encode()).hexdigest()[:24]
                    rows.append((assetid, bundle_name, str(obj.path_id), guid(container), name, kind,
                                 category, bundle_locale(bundle_name), tree.get('m_Length', 0)))
                with self.store.db:
                    self.store.db.execute('DELETE FROM assets WHERE bundle=?', (bundle_name,))
                    self.store.db.executemany('INSERT OR REPLACE INTO assets VALUES (?,?,?,?,?,?,?,?,?)', rows)
                    update_labels(self.store, rows)
                    self.store.db.execute('INSERT OR REPLACE INTO bundles VALUES (?,?,?)', (bundle_name, stamp, ''))
                    self.store.db.executemany('INSERT OR REPLACE INTO cabs VALUES (?,?)',
                        [(name, bundle) for name, bundle in self.reader.cabs.items() if bundle == bundle_name])
                log.info('索引 [%s/%s] %s · %s 资源 · %.2fs', index + 1, len(files), path.name, len(rows), time.monotonic() - started)
            except Exception as exc:
                log.exception('资源包解析失败：%s', path.name)
                failed += 1
                with self.store.db:
                    # 文件已变化但无法解析时，不继续暴露上个版本的过期资源。
                    self.store.db.execute('DELETE FROM assets WHERE bundle=?', (bundle_name,))
                    self.store.db.execute('INSERT OR REPLACE INTO bundles VALUES (?,?,?)', (bundle_name, stamp, str(exc)))
            yield {'done': index + 1, 'total': len(files), 'message': path.name}
        if failed:
            # 扫描失败不能留下本轮完整成功标记，也不发布不完整的新资源差分。
            if scope == 'all':
                self.store.set_meta('last_scan', None)
            self.store.set_meta('last_music_scan', None)
            raise ValueError(f'{failed} 个资源包解析失败；已保留成功部分，请查看资源诊断后重试')
        if not failed:
            self.store.set_meta('last_music_scan', time.strftime('%Y-%m-%d %H:%M:%S'))
        if scope == 'music':
            return
        self.store.set_meta('last_scan', time.strftime('%Y-%m-%d %H:%M:%S'))
        from .game_updates import compare_assets
        compare_assets(self)

    def list_assets(self, query='', kind='AudioClip', locale='all', category='', offset=0, limit=60, favorites=False, new=False, subgroup=''):
        clauses, args = ['kind=?'], [kind]
        if new:
            clauses.append("assets.id IN (SELECT id FROM new_content WHERE kind='asset')")
        if query:
            clauses.append('(name LIKE ? OR bundle LIKE ? OR guid LIKE ? OR annotation LIKE ?)')
            args += ['%' + query + '%'] * 4
        if locale != 'all':
            clauses.append('locale IN (?,?)')
            args += [locale, 'global']
        if category:
            clauses.append('category=?')
            args.append(category)
        if favorites:
            clauses.append("assets.id IN (SELECT id FROM favorites WHERE kind='asset')")
        source = 'assets LEFT JOIN audio_labels ON audio_labels.id=assets.id'
        # 子分组数量遵循当前分类、语言与搜索，但不被自身选择缩窄。
        groups = [dict(r) for r in self.store.db.execute('SELECT subgroup, COUNT(*) AS count FROM ' + source +
            ' WHERE ' + ' AND '.join(clauses) + ' AND subgroup IS NOT NULL GROUP BY subgroup ORDER BY subgroup', args)]
        if subgroup:
            clauses.append('subgroup=?')
            args.append(subgroup)
        clause = ' AND '.join(clauses)
        total = self.store.db.execute('SELECT COUNT(*) FROM ' + source + ' WHERE ' + clause, args).fetchone()[0]
        rows = self.store.db.execute("SELECT assets.*,subgroup,annotation, EXISTS(SELECT 1 FROM favorites WHERE kind='asset' AND favorites.id=assets.id) AS favorite FROM " + source + ' WHERE ' + clause +
            " ORDER BY CASE locale WHEN 'zhcn' THEN 0 WHEN 'global' THEN 1 ELSE 2 END,name,assets.id LIMIT ? OFFSET ?",
            (*args, min(int(limit), 100), max(0, int(offset)))).fetchall()
        categories = [r[0] for r in self.store.db.execute('SELECT DISTINCT category FROM assets WHERE kind=? ORDER BY category', (kind,))]
        new_ids = {r[0] for r in self.store.db.execute("SELECT id FROM new_content WHERE kind='asset'")}
        return {'total': total, 'items': [{**dict(r), 'is_new':r['id'] in new_ids} for r in rows], 'categories': categories,
                'subgroups': groups, 'music_indexed': bool(self.store.get_meta('last_music_scan')),
                'index_complete': bool(self.store.get_meta('last_scan'))}

    def asset_object(self, assetid):
        row = self.store.db.execute('SELECT * FROM assets WHERE id=?', (assetid,)).fetchone()
        if not row:
            raise ValueError('资源未被索引，请先扫描')
        env = self.reader.load(row['bundle'])
        return next(o for o in env.objects if str(o.path_id) == row['pathid']), dict(row)

    def _audio(self, obj):
        key = hashlib.sha256(f'{obj.assets_file.name}:{obj.path_id}'.encode()).hexdigest()[:24]
        folder = self.cache / 'audio' / key
        metadata = folder / 'index.json'
        if metadata.exists():
            return json.loads(metadata.read_text('utf-8'))
        folder.mkdir(parents=True, exist_ok=True)
        audio = obj.read()
        log.info('解码音频 %s', audio.m_Name)
        samples = audio.samples
        if not samples:
            raise ValueError('音频解码器没有返回采样，请检查 FMOD 支持和资源完整性')
        results = []
        # 一个 AudioClip 可以有多个子采样；全部保留，不能 next(iter(...)) 丢弃其余内容。
        for i, (name, payload) in enumerate(samples.items()):
            data, rate = sf.read(io.BytesIO(payload), dtype='float32', always_2d=True)
            if not len(data):
                raise ValueError('音频没有有效采样')
            path = folder / f'{i:02d}_{safe_name(Path(name).stem)}.wav'
            temporary = path.with_suffix(f'.{os.getpid()}.tmp')
            sf.write(temporary, data, rate, subtype='PCM_16', format='WAV')
            publish_cache(temporary, path)
            # 预计算紧凑波形，避免浏览器反复解码长音乐来绘图。
            import numpy as np
            mono = np.max(np.abs(data), axis=1)
            peaks = [float(np.max(chunk)) for chunk in np.array_split(mono, min(160, len(mono)))]
            results.append({'name': name, 'path': str(path), 'url': path.as_uri(), 'duration': len(data) / rate,
                            'rate': rate, 'channels': data.shape[1], 'peaks': peaks})
        temporary = metadata.with_suffix(f'.{os.getpid()}.tmp')
        temporary.write_text(json.dumps(results), 'utf-8')
        publish_cache(temporary, metadata)
        return results

    def audio(self, assetid='', reference='', locale='zhcn'):
        if assetid:
            obj, row = self.asset_object(assetid)
        else:
            obj, bundle, actual = self.reader.resolve(reference, locale)
            row = {'bundle': bundle, 'locale': actual}
        if obj.type.name != 'AudioClip':
            raise ValueError('所选资源不是 AudioClip')
        return {'samples': self._audio(obj), 'source': row}

    def related_audio(self, reference, locale='zhcn', owner=''):
        key = (reference, locale, owner)
        if key in self.audio_relations:
            self.audio_relations.move_to_end(key)
            return self.audio_relations[key]
        root, _, _ = self.reader.resolve(reference, locale)
        result = self._collect_audio(root, locale, owner)
        if not result['errors']:
            self.audio_relations[key] = result
            while len(self.audio_relations) > 128:
                self.audio_relations.popitem(last=False)
        return result

    def _collect_audio(self, root, locale, owner=''):
        """收集一个对象图的声音，既供 GUID 预制体，也供皮肤内嵌 PPtr 配置使用。"""
        sounds, seen, errors, rows = [], set(), [], []
        from .voice_conditions import matches_owner, describe_condition
        tags = dict(self.store.db.execute('SELECT CAST(tag AS TEXT),value FROM card_tags WHERE id=?', (owner,))) if owner else {}
        iterator = iter(self.reader.walk(root, locale, errors=errors, with_conditions=True,
            with_timing=True,
            condition_match=(lambda c: matches_owner(c, owner, tags)) if owner else None))
        while True:
            try:
                obj, tree, condition, timing = next(iterator)
            except StopIteration:
                break
            except (ValueError, KeyError, FileNotFoundError) as exc:
                # 巨型商店预制体可能超出节点上限；保留已找到的音频并明确返回未完成原因。
                errors.append(str(exc))
                break
            if obj.type.name == 'AudioClip':
                audio = obj.read()
                name = audio.m_Name
                bundle = self.reader.cabs[Path(obj.assets_file.name).name.lower()]
                identity = hashlib.sha256(f'{bundle}:{obj.path_id}'.encode()).hexdigest()[:24]
                identity_key = (identity, json.dumps(condition, sort_keys=True))
                if identity_key in seen:
                    # 普通层级遍历可能先遇到同一个 Clip；后来找到明确时钟时补证据。
                    if timing and timing.get('resolved'):
                        for sound in sounds:
                            if sound['id'] == identity and sound.get('condition_raw') == condition and not (sound.get('timing') or {}).get('resolved'):
                                sound['timing'] = timing
                    continue
                seen.add(identity_key)
                actual_locale = bundle_locale(bundle)
                rows.append((identity, bundle, str(obj.path_id), '', name, 'AudioClip',
                    audio_category(name, bundle, audio.m_Length), actual_locale, audio.m_Length))
                # 语音严格匹配实际包语言，不把缺失语言回退后的基础音频冒充目标语言。
                if audio_category(name, bundle, audio.m_Length) == '角色语音' and actual_locale not in (locale, 'global'):
                    continue
                # 从其他卡牌借来的专属触发只保留命中条件的音源，不能带入默认随从声。
                if owner and not condition:
                    continue
                condition_text, targets = describe_condition(self.store, condition, locale)
                sounds.append({'id': identity, 'name': name, 'locale': actual_locale,
                    'timing': timing,
                    'condition': condition_text, 'condition_targets': targets, 'condition_raw': condition,
                    'text': (self.strings.get(locale, {}).get((condition or {}).get('m_GameStringKey', '').upper(), '')
                             or self.strings.get(locale, {}).get(audio_key(name), '')), 'bundle': bundle,
                    'kind': 'voice' if audio_category(name, bundle, audio.m_Length) == '角色语音' else 'sound'})
        # 所有 Unity I/O 完成后才开启短事务，避免解压时长时间占用数据库写锁。
        with self.store.db:
            self.store.db.executemany('INSERT OR REPLACE INTO assets VALUES (?,?,?,?,?,?,?,?,?)', rows)
            update_labels(self.store, rows)
        return {'items': sounds, 'errors': list(dict.fromkeys(errors))}

    def card_audio(self, cardid, locale='zhcn'):
        key = (cardid, locale)
        if key in self.card_voices:
            self.card_voices.move_to_end(key)
            return self.card_voices[key]
        # 引用解析比读取已有 WAV 更昂贵。缓存完整成功结果，按语言资源文件
        # 和字幕文件的大小/修改时间失效；失败和未安装语言不写入负缓存。
        if locale not in self.voice_cache_stamp and self.reader and self.root:
            files = [p for name, p in self.reader.source_paths().items()
                     if bundle_locale(name) in (locale, 'global')]
            files += list((self.root / 'Strings' / (locale[:2] + locale[2:].upper())).glob('*.txt'))
            self.voice_cache_stamp[locale] = fingerprint(files)
        cached_path = None
        if self.cache and locale in self.voice_cache_stamp:
            digest = hashlib.sha256(f'voices-v9.1:{cardid}:{locale}:{self.voice_cache_stamp[locale]}'.encode()).hexdigest()
            cached_path = self.cache / 'voice-queries' / (digest + '.json')
            try:
                cached = json.loads(cached_path.read_text('utf8'))
                if isinstance(cached.get('items'), list) and not cached.get('errors'):
                    self.card_voices[key] = cached
                    while len(self.card_voices) > 32:
                        self.card_voices.popitem(last=False)
                    return cached
            except (OSError, ValueError, TypeError, AttributeError):
                pass
        card = self.card(cardid, locale)
        items, errors, seen = [], [], set()
        from .voice_conditions import audible_effect, event_group, emote_condition
        effects = [e for e in card['effects'] if audible_effect(e)]
        if self.store and self.store.db.execute("SELECT 1 FROM sqlite_master WHERE name='voice_links'").fetchone():
            for r in self.store.db.execute('''SELECT v.reference,c.id,c.name,c.data FROM voice_links v
                    JOIN cards c ON c.id=v.card WHERE v.hero=? ORDER BY c.id''', (cardid,)):
                effects.append({'ref': r['reference'], 'field': '专属触发 · ' + r['name'],
                                'name': r['reference'].split(':')[0], 'trigger_card': r['id']})
        # 去重时保留第一次出现的事件与顺序，进度分母只包含实际要读取的引用。
        unique_effects = {}
        for effect in effects:
            unique_effects.setdefault((effect['ref'], event_group(effect['field'])), effect)
        effects = list(unique_effects.values())
        for index, effect in enumerate(effects):
            if getattr(self, 'cancel_detail', lambda: False)():
                raise InterruptedError('已切换详情，停止后续声音引用解析')
            event_key = (effect['ref'], event_group(effect['field']))
            if event_key in seen:
                continue
            self.emit({'event': 'detail_progress', 'cardid': cardid, 'done': index,
                       'total': len(effects), 'message': effect['name']})
            log.info('卡牌语音 %s [%s/%s] %s', cardid, index + 1, len(effects), effect['name'])
            seen.add(event_key)
            try:
                data = self.related_audio(effect['ref'], locale, owner=cardid) if effect.get('trigger_card') else self.related_audio(effect['ref'], locale)
                # 自定义英雄登场图会容纳友方/敌方模板及备用声音。若客户端提供
                # 与当前召唤预制体同名的完整音轨，只将该音轨作为自动配套；其他
                # 可达声音仍保留在音效页供单独检查，不把互斥模板一起叠放。
                summon_name = effect['name'].removesuffix('.prefab').removesuffix('_FX') + '_Sound'
                exact_summon = effect['field'] == 'm_CustomSummonSpellPath' and any(x['name'] == summon_name for x in data['items'])
                transcript = ''
                event_condition = ''
                match = re.match(r'm_EmoteDefs\[(\d+)\]', effect['field'])
                if match:
                    string_key = card['definition']['m_EmoteDefs'][int(match.group(1))].get('m_emoteGameStringKey', '')
                    transcript = self.strings.get(locale, {}).get(string_key.upper(), '')
                    event_condition = emote_condition(string_key or effect['name'])
                for sound in data['items']:
                    # 复刻卡 / 衍生卡经常直接引用原卡的声音。只有原卡同一事件也
                    # 引用相同预制体才采用其 DBF ID，不能仅因名称相似便借用台词。
                    source_name, source_cardid = card.get('name', ''), cardid
                    source_dbfid = card.get('record', {}).get('dbfid')
                    voice_card = re.match(r'^VO_([A-Z0-9]+_\d+[a-z]?)_', sound.get('name', ''), re.I)
                    if sound.get('kind') == 'voice' and voice_card and voice_card[1] != cardid and voice_card[1] in self.reader.cards:
                        try:
                            original = self.card(voice_card[1], locale)
                            if any(e['ref'] == effect['ref'] and e['field'].split('.')[0] == effect['field'].split('.')[0]
                                   for e in original['effects']):
                                source_name, source_cardid = original.get('name', ''), voice_card[1]
                                source_dbfid = original['record'].get('dbfid') or source_dbfid
                        except Exception as exc:
                            # 补充来源是可选元数据。原卡资源损坏时仍保留当前卡
                            # 已解析出的音频，不能因为查字幕而让试听条目消失。
                            log.warning('原卡台词来源未能核对 %s：%s', voice_card[1], exc)
                    items.append({**sound, 'event': effect['field'], 'trigger_card': effect.get('trigger_card'),
                                  'condition': sound.get('condition') or event_condition,
                                  'paired': not exact_summon or sound['name'] == summon_name,
                                  'group': event_group(effect['field']),
                                  'transcript_dbfid': source_dbfid, 'transcript_name': source_name, 'transcript_cardid': source_cardid,
                                  'text': (sound['text'] or (transcript if not sound.get('condition_raw') else '')) if sound.get('kind', 'voice') == 'voice' else ''})
                errors.extend(data['errors'])
            except Exception as exc:
                errors.append(f'{effect["name"]}: {exc}')
        # 传说英雄的内嵌配置可以通过 PPtr 而不是 GUID 字符串引用资源。
        config = card['definition'].get('m_LegendaryHeroSkinConfig', {})
        if config.get('m_PathID'):
            try:
                owner = self.reader.resolve(self.reader.cards[cardid], locale)[0]
                root = self.reader.pointer(owner, config)
                data = self._collect_audio(root, locale)
                existing = {s['id'] for s in items}
                items.extend({**s, 'event': '传说英雄内嵌配置'} for s in data['items'] if s['id'] not in existing)
                errors.extend(data['errors'])
            except Exception as exc:
                errors.append('传说英雄内嵌配置：' + str(exc))
        # 调酒师的场景台词没有挂在 CardDef；用选中语音所携带的角色键
        # 定位本地 AudioClip。只扫描目标语言的音频元数据，并缓存扫描完成标记。
        from .npc_audio import LICH_KING_CARDS
        if cardid in LICH_KING_CARDS or cardid.startswith('TB_BaconShopBob') or card.get('record', {}).get('npc'):
            from .npc_audio import npc_audio
            extra = npc_audio(self, card, locale)
            present = {s['id'] for s in items}
            items.extend(s for s in extra['items'] if s['id'] not in present)
            errors.extend(extra['errors'])
        result = {'items': items, 'errors': list(dict.fromkeys(errors)),
                  'audioInstalled': self.audio_installed(locale) if self.reader else False}
        if not result['errors']:
            if cached_path and result['audioInstalled']:
                cached_path.parent.mkdir(parents=True, exist_ok=True)
                temporary = cached_path.with_suffix('.tmp')
                temporary.write_text(json.dumps(result, ensure_ascii=False), 'utf8')
                temporary.replace(cached_path)
            self.card_voices[key] = result
            while len(self.card_voices) > 32:
                self.card_voices.popitem(last=False)
        return result

    def general_audio(self, cardid, assetid, locale='zhcn'):
        """按需解析通用音效，不要求用户先建立全量音频索引。

        入口重查本地卡牌和所选声音，前端不能提交标签来诱导错误配音。独立
        返回音轨，复用播放器的暂停、定位、尾声上限及停止行为。
        """
        card = self.card(cardid, locale)
        items = self.card_audio(cardid, locale)['items']
        selected = next((x for x in items if x['id'] == assetid and x.get('kind') == 'voice'), None)
        event = base_event(selected or {})
        if not event:
            return {'items': [], 'errors': []}
        if self.card_tags is None:
            env = self.reader.load('dbf.unity3d')
            tags = {}
            for row in env.container['Assets/Game/DBF-Asset/CARD_TAG.asset'].read_typetree()['Records']:
                if not row.get('m_isReferenceTag') and row['m_tagId'] in (202, 190, 194):
                    tags.setdefault(row['m_cardId'], {})[row['m_tagId']] = row['m_tagValue']
            self.card_tags = tags
        tags = self.card_tags.get(card['record'].get('dbfid'), {})
        if tags.get(202) != 4:
            return {'items': [], 'errors': []}
        result = [{**x, 'reason': '卡牌引用的种族 / 材质垫音'} for x in event_underlays(items, event)]
        errors = []
        if event == 'Play':
            try:
                if self.shared_spell_table is None:
                    # 从当前安装版本按资源名定位，再读取表中的 GUID；不固化某个
                    # 补丁的路径 ID，也不扫描与本次试听无关的全部资源包。
                    env = self.reader.load('essential_base_global-prefab-0.unity3d')
                    root = next(o for o in env.objects if o.type.name == 'GameObject'
                                and o.read().m_Name == 'Card_Play_Ally_SpellTable')
                    components = [self.reader.pointer(root, c['component'])
                                  for c in root.read_typetree()['m_Component']]
                    table = next(o.read_typetree()['m_Table'] for o in components
                                 if o.type.name == 'MonoBehaviour' and 'm_Table' in o.read_typetree())
                    self.shared_spell_table = {r['m_Type']: r['m_SpellPrefabName'] for r in table}
                for spell_type, label, names in shared_rules(tags):
                    try:
                        data = self.related_audio(self.shared_spell_table[spell_type], locale)
                        tracks = [x for x in data['items'] if x['name'] in names and x['kind'] == 'sound']
                        # 此预制体的零点是落地/盾牌展开事件，不能当成语音零点。
                        result.extend({**x, 'reason': label, 'timing': {
                            **(x.get('timing') or {}), 'resolved': False, 'anchor': 'game_event',
                            'label': label + '时触发；相对语音的时间由游戏动画/对局状态决定'}} for x in tracks)
                        errors.extend(data['errors'])
                        if not tracks:
                            errors.append(label + '：当前资源未找到已支持的音轨')
                    except Exception as exc:
                        errors.append(label + '：' + str(exc))
            except Exception as exc:
                errors.append('通用音效配置：' + str(exc))
        return {'items': list({x['id']: x for x in result}.values()), 'errors': errors}

    def audio_plan(self, cardid, assetid, locale='zhcn', paired_audio=False, general_audio=False):
        """试听与导出共用计划：可确定的时间保留，未知时间随主语音叠放。

        零秒回退只是试听约定，不修改资源证据。主语音有静态延迟时，未知
        配套跟随主语音起点，避免在尚未听见语音前先放完一个短音效。
        """
        from .audio_timing import describe_timing
        items = self.card_audio(cardid, locale)['items']
        selected = next((x for x in items if x['id'] == assetid and x.get('kind') == 'voice'), None)
        if selected is None:
            return {'main_delay': 0, 'tracks': [], 'skipped': [], 'errors': []}
        candidates = {x['id']: x for x in items if paired_audio and selected.get('group')
            and x.get('group') == selected['group'] and x.get('kind') == 'sound' and x.get('paired', True)}
        errors = []
        if general_audio:
            shared = self.general_audio(cardid, assetid, locale)
            # 同一音效可能同时属于配套与通用音效；只播放一次，并优先保留
            # 已解析的卡牌事件时间，不能被通用列表的未知触发时间覆盖。
            for item in shared['items']:
                previous = candidates.get(item['id'])
                if previous is None or (not (previous.get('timing') or {}).get('resolved')
                        and (item.get('timing') or {}).get('resolved')):
                    candidates[item['id']] = item
            errors.extend(shared['errors'])
        main_clock = selected.get('timing') or {}
        main_delay = main_clock.get('seconds', 0) if main_clock.get('resolved') else 0
        tracks, skipped = [], []
        for identity, item in candidates.items():
            if identity == assetid:
                continue
            clock = item.get('timing') or {}
            record = {'id': identity, 'name': item['name'], 'timing': clock, 'reason': describe_timing(item)}
            if clock.get('resolved') and (clock.get('anchor') == 'card_event'
                    or main_clock.get('resolved') and clock.get('anchor') == main_clock.get('anchor')):
                record['delay'] = clock['seconds']
                record['placement'] = 'resolved'
            else:
                record['delay'] = main_delay
                record['placement'] = 'overlay'
            tracks.append(record)
        return {'main_delay': main_delay,
                'main_timing': describe_timing(selected), 'tracks': tracks, 'skipped': skipped, 'errors': errors}

    def playback_audio(self, assetid, cardid='', locale='zhcn', paired_audio=False, general_audio=False):
        """生成一条共享媒体时钟的预览；暂停、定位、尾声和导出无需多个定时器同步。"""
        data = self.audio(assetid=assetid, locale=locale)
        if not cardid or not (paired_audio or general_audio):
            return data
        plan = self.audio_plan(cardid, assetid, locale, paired_audio, general_audio)
        companions = []
        for track in plan['tracks']:
            try:
                companions.extend({'path': sample['path'], 'delay': track['delay']}
                    for sample in self.audio(assetid=track['id'], locale=locale)['samples'])
            except Exception as exc:
                plan['errors'].append(track['name'] + '：' + str(exc))
        data['timeline'] = plan
        # 没有可用配套时保持原始语音，不人为添加无意义的前置静音。
        if not companions:
            return data
        from .audio_mix import mix_samples
        import numpy as np
        samples = []
        folder = self.cache / 'audio-timelines'
        folder.mkdir(exist_ok=True)
        for sample in data['samples']:
            digest = hashlib.sha256(json.dumps(['v1', sample['path'], companions, plan['main_delay']], sort_keys=True).encode()).hexdigest()
            path = folder / (digest + '.wav')
            if not path.exists():
                temporary = path.with_name(digest + '.tmp.wav')
                mix_samples(sample['path'], companions, temporary, main_delay=plan['main_delay'])
                publish_cache(temporary, path)
            values, rate = sf.read(path, dtype='float32', always_2d=True)
            mono = np.max(np.abs(values), axis=1)
            peaks = [float(np.max(chunk)) for chunk in np.array_split(mono, min(160, len(mono)))]
            samples.append({**sample, 'name': sample['name'] + ' · 配套试听', 'path': str(path),
                'url': path.as_uri(), 'rate': rate, 'channels': values.shape[1], 'duration': len(values)/rate, 'peaks': peaks})
        return {**data, 'samples': samples}

    def effect(self, reference='', assetid='', locale='zhcn'):
        key = (reference, assetid, locale)
        if key in self.effect_previews:
            self.effect_previews.move_to_end(key)
            return self.effect_previews[key]
        root = self.asset_object(assetid)[0] if assetid else self.reader.resolve(reference, locale)[0]
        particles, sounds, components, errors = [], [], {}, []
        for obj, tree in self.reader.walk(root, locale, errors=errors, include_visual=True):
            kind = obj.type.name
            components[kind] = components.get(kind, 0) + 1
            if kind == 'ParticleSystem':
                # 预览使用真实粒子参数，明确是二维近似；保留完整数据供检查导出。
                tree['_preview'] = {}
                try:
                    owner = self.reader.pointer(obj, tree['m_GameObject'])
                    for component in owner.read_typetree().get('m_Component', []):
                        sibling = self.reader.pointer(owner, component['component'])
                        if sibling and sibling.type.name == 'ParticleSystemRenderer':
                            renderer = sibling.read_typetree()
                            for pointer in renderer.get('m_Materials', [])[:1]:
                                material = self.reader.pointer(sibling, pointer)
                                if material:
                                    textures = material.read_typetree().get('m_SavedProperties', {}).get('m_TexEnvs', [])
                                    for slot, value in textures:
                                        if slot in ('_MainTex', '_Texture', '_BaseMap') and value['m_Texture']['m_PathID']:
                                            texture = self.reader.pointer(material, value['m_Texture'])
                                            tree['_preview']['texture'] = self._image(texture, True)['url']
                                            break
                except Exception as exc:
                    errors.append('粒子纹理：' + str(exc))
                particles.append(tree)
            elif kind == 'AudioClip':
                try:
                    sounds.extend(self._audio(obj))
                except Exception as exc:
                    errors.append(str(exc))
        result = {'name': root.read_typetree().get('m_Name', '特效'), 'particles': particles,
                'sounds': sounds, 'components': components, 'errors': list(dict.fromkeys(errors)),
                'note': '实验性二维粒子参数预览。未执行游戏脚本、专用 Shader、网格动画和战斗时序；声音是引用图中的关联音频，不保证原版触发时刻。'}
        self.effect_previews[key] = result
        while len(self.effect_previews) > 12:
            self.effect_previews.popitem(last=False)
        return result

    def favorite(self, kind, identity, enabled=True):
        with self.store.db:
            if enabled:
                self.store.db.execute('INSERT OR IGNORE INTO favorites VALUES (?,?)', (kind, identity))
            else:
                self.store.db.execute('DELETE FROM favorites WHERE kind=? AND id=?', (kind, identity))
        return {'enabled': enabled}

    def export(self, assetids=None, reference='', cardid='', locale='zhcn', image_path='', media_paths=None, context_cardid='', label='', mix_voice=False, paired_audio=False, general_audio=False):
        target = Path(self.settings['export_path']).resolve()
        # 设置中错误地选到安装目录时，不允许导出污染源资源。
        if target == self.root or self.root in target.parents:
            raise ValueError('导出目录不能位于炉石安装目录内')
        context = cardid or context_cardid
        if context:
            row = self.store.db.execute('SELECT name FROM cards WHERE id=?', (context,)).fetchone()
            title = (row['name'] + '_' + context) if row else context
        elif assetids and self.store:
            row = self.store.db.execute('SELECT name FROM assets WHERE id=?', (assetids[0],)).fetchone()
            title = row['name'] if row else '声音素材'
        else:
            title = label or '资源素材'
        title = safe_name(title)[:70]
        # 连续导出可能落在 Windows 同一个时钟刻度内；随机后缀不依赖计时精度。
        folder = target / (title + '_' + locale + '_' + time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:12])
        folder.mkdir(parents=True, exist_ok=False)
        report = {'files': [], 'errors': [], 'source': str(self.root), 'version': self.cache.name,
                  'name': title, 'locale': locale, 'media': []}
        def copy(path):
            source = Path(path).resolve()
            if self.cache not in source.parents:
                raise ValueError('只能导出当前索引生成的媒体')
            destination = folder / f'{title[:48]}_{len(report["files"]):03d}_{safe_name(label or source.stem)[:70]}{source.suffix}'
            shutil.copyfile(source, destination)
            report['files'].append(str(destination))
            if source.suffix.lower() == '.png':
                with Image.open(source) as image:
                    report['media'].append({'file': destination.name, 'width': image.width, 'height': image.height, 'resized': False})
        if image_path:
            copy(image_path)
        for path in media_paths or []:
            copy(path)
        if cardid:
            data = self.card(cardid, locale)
            (folder / f'{safe_name(cardid)}.json').write_text(json.dumps(data, ensure_ascii=False, indent=2), 'utf-8')
            for i, variant in enumerate(data['variants']):
                if variant['available']:
                    try:
                        portrait = self.portrait(cardid, i, locale)
                        report['errors'].extend(f'{variant["label"]}: {error}' for error in portrait['errors'])
                        for image in portrait['images']:
                            copy(image['path'])
                    except Exception as exc:
                        report['errors'].append(f'{variant["label"]}: {exc}')
            assetids = list(assetids or [])
            related = self.card_audio(cardid, locale)
            assetids.extend(x['id'] for x in related['items'])
            report['errors'].extend(related['errors'])
        if reference:
            related = self.related_audio(reference, locale)
            assetids = [x['id'] for x in related['items']]
            report['errors'].extend(related['errors'])
        identities = list(dict.fromkeys(assetids or []))
        for index, identity in enumerate(identities):
            self.emit({'event': 'export_progress', 'message': '解码并导出音频', 'done': index, 'total': len(identities)})
            try:
                companions = []
                plan = {'main_delay': 0}
                if mix_voice and context and (paired_audio or general_audio):
                    plan = self.audio_plan(context, identity, locale, paired_audio, general_audio)
                    report.setdefault('timelines', []).append({'assetid': identity, **plan})
                    report['errors'].extend(plan['errors'])
                    for track in plan['tracks']:
                        try:
                            companions.extend({'path': s['path'], 'delay': track['delay']}
                                for s in self.audio(assetid=track['id'])['samples'])
                        except Exception as exc:
                            report['errors'].append(f'配套音轨 {track["name"]}: {exc}')
                for sample in self.audio(assetid=identity)['samples']:
                    if companions:
                        from .audio_mix import mix_samples
                        destination = folder / f'{title[:48]}_{len(report["files"]):03d}_{safe_name(Path(sample["path"]).stem)[:65]}_合并.wav'
                        mix = mix_samples(sample['path'], companions, destination, main_delay=plan['main_delay'])
                        report['files'].append(str(destination))
                        report['media'].append({'file': destination.name, 'mix': mix})
                    else:
                        copy(sample['path'])
            except Exception as exc:
                report['errors'].append(f'{identity}: {exc}')
        (folder / 'export-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), 'utf-8')
        self.emit({'event': 'export_progress', 'message': '导出报告已写入', 'done': len(identities), 'total': len(identities)})
        log.info('导出完成 %s 文件，%s 错误：%s', len(report['files']), len(report['errors']), folder)
        return {'folder': str(folder), **report}

    def diagnostics(self):
        errors = [dict(r) for r in self.store.db.execute("SELECT name,error FROM bundles WHERE error<>''")]
        return {'status': self.status(), 'bundle_errors': errors}
