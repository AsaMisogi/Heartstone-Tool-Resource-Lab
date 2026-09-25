"""图鉴筛选元数据。只读取 DBF，不依赖全量资源扫描或远程卡牌数据库。

数值来自游戏的 GameTag 枚举；显示名称优先使用客户端中文字符串表。
独立表升级不删除 cards / favorites，旧工作区可直接继续使用。
"""
import csv
import re
import json
import html
from collections import defaultdict
from datetime import date
from .releases import release_reference
from .common import localized

CATALOG_VERSION = 8
KEYWORD_VERSION = 1


def has_immune_keyword(record):
    """词条筛选表达卡面机制，包含授予/条件免疫，但排除隐藏无敌标签。

    客户端 240 标签是运行时不可受伤状态：它既不会列出脚本在攻击期间
    授予免疫的卡牌，也会命中剧情中不可摧毁的实体。这里只读取简体卡面
    正文，不检索名称、趣味描述、衍生牌或另一语言的旧文本。
    """
    text = html.unescape(re.sub(r'<[^>]*>', '', localized(record, 'm_textInHand')))
    return '免疫' in text


def update_immune_keywords(store):
    """轻量修复已有工作区；不重建卡牌关系，也不改变收藏和完整索引状态。"""
    with store.db:
        store.db.execute("DELETE FROM card_keywords WHERE keyword='免疫'")
        store.db.executemany('INSERT OR IGNORE INTO card_keywords VALUES (?,?)',
            [(row['id'], '免疫') for row in store.db.execute('SELECT id,data FROM cards')
             if has_immune_keyword(json.loads(row['data']))])
    store.set_meta('immune_keyword_version', KEYWORD_VERSION)

CLASSES = {12: '通用英雄 / 中立', 1: '死亡骑士', 14: '恶魔猎手', 2: '德鲁伊',
           3: '猎人', 4: '法师', 5: '圣骑士', 6: '牧师', 7: '潜行者', 8: '萨满祭司',
           9: '术士', 10: '战士'}
TYPES = {4: '随从', 5: '法术', 7: '装备 / 武器', 3: '英雄牌', 39: '地标', 10: '英雄技能'}
RARITIES = {2: '免费', 1: '普通', 3: '稀有', 4: '史诗', 5: '传说'}
# 系列 ID 是稳定枚举；未知新系列保留编号，不凭卡牌 ID 猜系列。
SET_KEYS = dict(zip(
    [2,3,4,5,12,13,14,15,20,21,23,25,27,1001,1004,1125,1127,1129,1130,1158,1347,
     1403,1414,1443,1463,1466,1525,1578,1626,1635,1637,1646,1658,1691,1776,1809,
     1858,1869,1892,1897,1898,1905,1935,1941,1946,1952,1957,1980,1988,1994,1961],
    'BASIC EXPERT1 HOF TUT NAXX GVG BRM TGT LOE OG KARA GANGS UNGORO ICECROWN LOOTAPALOOZA GILNEAS BOOMSDAY TROLL DALARAN ULDUM DRG YOD BT SCH DHI DMF BAR SW AV LEGACY CORE VANILLA TSC REV RLK ETC TTN PA WST TOY WON VAC GDB EVE EDR TLC TIME CATA JAIL BE PET'.split()))


def build_catalog(service):
    """一次读取标签、系列和皮肤关系，避免滚动图鉴时逐张读取 Unity CardDef。"""
    db = service.store.db
    env = service.reader.load('dbf.unity3d')
    record_cache = {}
    def records(name):
        # CARD / HERO 等表在百科阶段还要读取，复用类型树，避免重复解码。
        if name in record_cache:
            return record_cache[name]
        service.emit({'event': 'progress', 'message': '读取数据库表 · ' + name, 'done': 0, 'total': 0})
        key = f'Assets/Game/DBF-Asset/{name}.asset'
        record_cache[name] = env.container[key].read_typetree()['Records'] if key in env.container else []
        return record_cache[name]
    tags, sets = defaultdict(dict), defaultdict(list)
    for r in records('CARD_TAG'):
        if not r.get('m_isReferenceTag'):
            tags[r['m_cardId']][r['m_tagId']] = r['m_tagValue']
    for r in records('CARD_SET_TIMING'):
        sets[r['m_cardId']].append(r['m_cardSetId'])
    skins = {r['m_cardId'] for r in records('CARD_HERO')}
    guides = {r['m_skinCardId'] for r in records('BATTLEGROUNDS_GUIDE_SKIN')}
    battlegrounds = set()
    for r in records('BATTLEGROUNDS_HERO_SKIN'):
        battlegrounds.update((r['m_skinCardId'], r['m_baseCardId']))
    names = {}
    path = service.root / 'Strings/zhCN/GLOBAL.txt'
    if path.exists():
        with path.open(encoding='utf-8-sig') as stream:
            names = {r.get('TAG'): r.get('TEXT') for r in csv.DictReader(stream, delimiter='\t')}
    keyword_tags = {}
    for keyword in records('KEYWORD_TEXT'):
        label = re.split(r'[：:（(]', names.get(keyword['m_name'], ''))[0].strip()
        if label and keyword.get('m_tagId'):
            keyword_tags[keyword['m_tagId']] = label
    set_records = {r['m_ID']: r for r in records('CARD_SET')}
    event_map = env.container['Assets/Game/DBF-Asset/EventMap.asset'].read_typetree()
    events = dict(zip(event_map['m_Values'], event_map['m_Keys']))
    # 离线客户端不包含服务器活动开关。保留轮换年份，让界面明确这是本地规则参考。
    year = date.today().year
    standard_sets = set()
    for sid, r in set_records.items():
        event = events.get(r['m_standardEvent'], '')
        match = re.fullmatch(r'pre_set_rotation_(\d+)', event)
        if r['m_isCoreCardSet'] or (match and year < int(match[1])) or event == 'always':
            standard_sets.add(sid)
    db.executescript('''
        CREATE TABLE IF NOT EXISTS card_details (id TEXT PRIMARY KEY, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS card_relations (id TEXT, related TEXT, PRIMARY KEY(id,related));
        CREATE INDEX IF NOT EXISTS relation_reverse ON card_relations(related,id);
        CREATE TABLE IF NOT EXISTS card_filters (
            id TEXT PRIMARY KEY, class_id INTEGER, rarity INTEGER, cost INTEGER,
            card_type INTEGER, collectible INTEGER, bg INTEGER, hero_group TEXT,
            standard INTEGER, wild INTEGER);
        CREATE TABLE IF NOT EXISTS card_classes (id TEXT, class_id INTEGER, PRIMARY KEY(id,class_id));
        CREATE INDEX IF NOT EXISTS filter_classes ON card_classes(class_id,id);
        CREATE TABLE IF NOT EXISTS card_sets (id TEXT, set_id INTEGER, PRIMARY KEY(id,set_id));
        CREATE INDEX IF NOT EXISTS filter_facets ON card_filters(card_type,class_id,cost);
        CREATE INDEX IF NOT EXISTS filter_sets ON card_sets(set_id,id);
        CREATE TABLE IF NOT EXISTS card_tags(id TEXT,tag INTEGER,value INTEGER,PRIMARY KEY(id,tag));
        CREATE INDEX IF NOT EXISTS tags_value ON card_tags(tag,value,id);
        CREATE TABLE IF NOT EXISTS card_keywords(id TEXT,keyword TEXT,PRIMARY KEY(id,keyword));
        CREATE INDEX IF NOT EXISTS keywords_name ON card_keywords(keyword,id);
    ''')
    with db:
        db.execute('DELETE FROM card_details')
        db.execute('DELETE FROM card_relations')
        card_records = records('CARD')
        identities = {r['m_ID']: r['m_noteMiniGuid'] for r in card_records if r['m_noteMiniGuid']}
        # 显式关系来自客户端表，不用相似名称或 ID 前缀推断衍生牌。
        relation_key = 'Assets/Game/DBF-Asset/RELATED_CARDS.asset'
        if relation_key in env.container:
            for relation in records('RELATED_CARDS'):
                source = identities.get(relation['m_cardId'])
                target = identities.get(relation['m_relatedCardDatabaseId'])
                if source and target and source != target:
                    db.execute('INSERT OR IGNORE INTO card_relations VALUES (?,?)', (source, target))
        race_key = 'Assets/Game/DBF-Asset/CARD_RACE.asset'
        race_tags = records('CARD_RACE') if race_key in env.container else []
        db.execute('DELETE FROM card_filters')
        db.execute('DELETE FROM card_sets')
        db.execute('DELETE FROM card_classes')
        db.execute('DELETE FROM card_releases')
        db.execute('DELETE FROM card_tags')
        db.execute('DELETE FROM card_keywords')
        for index, r in enumerate(card_records):
            if index % 500 == 0:
                service.emit({'event': 'progress', 'message': '建立筛选与属性索引', 'done': index, 'total': len(card_records)})
            cid, rid = r['m_noteMiniGuid'], r['m_ID']
            if not cid:
                continue
            t = tags[rid]
            db.executemany('INSERT INTO card_tags VALUES (?,?,?)', [(cid, k, v) for k, v in t.items()])
            # KEYWORD_TEXT 把本地化名称映射到实际标签；引用其他卡牌的词条
            # 不作为自身能力，也不会把加粗的数字、“你的”等误做筛选项。
            keywords = {label for tag, label in keyword_tags.items() if t.get(tag)}
            db.executemany('INSERT OR IGNORE INTO card_keywords VALUES (?,?)',
                [(cid, word) for word in keywords if 1 < len(word) <= 12])
            # 新版客户端用独立标签表示多种族，兼容旧版主种族标签。
            races = {race['m_ID'] for race in race_tags if race['m_isRaceTagId'] and t.get(race['m_isRaceTagId'])}
            if t.get(200):
                races.add(t[200])
            detail = {'attack': t.get(47), 'health': t.get(45), 'durability': t.get(187),
                      'tier': t.get(1440), 'bg_pool': bool(t.get(1456)), 'bg_golden': bool(t.get(1471)),
                      'races': sorted(races), 'crafting_event': r.get('m_craftingEvent', -1),
                      'golden_crafting_event': r.get('m_goldenCraftingEvent', -1)}
            db.execute('INSERT INTO card_details VALUES (?,?)', (cid, json.dumps(detail)))
            bg_hero = rid in battlegrounds or cid.startswith(('TB_BaconShop_HERO_', 'BG_HERO_'))
            bg = bg_hero or bool(t.get(1440) or t.get(1456)) or 1453 in sets[rid]
            hero = rid in skins or rid in guides or bg_hero or (t.get(202) == 3 and not t.get(321))
            group = 'npc' if rid in guides else (str(t.get(199, 12)) if rid in skins or bg or cid.startswith('HERO_') else 'enemy')
            # MULTIPLE_CLASSES 使用职业枚举减一作为位序，双职业不应归入中立。
            mask = t.get(476, 0)
            classes = [c for c in CLASSES if mask & (1 << (c - 1))] if mask else [t.get(199, 12)]
            db.executemany('INSERT OR IGNORE INTO card_classes VALUES (?,?)', [(cid, c) for c in classes])
            collectible = bool(t.get(321))
            cardsets = set(sets[rid])
            db.execute('INSERT INTO card_filters VALUES (?,?,?,?,?,?,?,?,?,?)',
                (cid, t.get(199, 12), t.get(203, 0), t.get(48, 0), t.get(202, 0), collectible,
                 bg, group, collectible and bool(cardsets & standard_sets),
                 collectible and any(set_records.get(sid, {}).get('m_isCollectible') for sid in cardsets)
                 and not bg and not cardsets.issubset({1646,1586,1810})))
            db.executemany('INSERT OR IGNORE INTO card_sets VALUES (?,?)', [(cid, sid) for sid in cardsets])
            db.execute('UPDATE cards SET hero=? WHERE id=?', (hero, cid))
            # 战棋普通/金色随从有明确的 DBF 互引，详情可直接跳到对照版本。
            for tag in (1429, 1471, 1452):
                target = identities.get(t.get(tag))
                if target and target != cid:
                    db.execute('INSERT OR IGNORE INTO card_relations VALUES (?,?)', (cid, target))
            release_date, source = release_reference(cid, cardsets, hero)
            db.execute('INSERT INTO card_releases VALUES (?,?,?)', (cid, release_date, source))
    labels = {sid: names.get('GLOBAL_CARD_SET_' + key) or key for sid, key in SET_KEYS.items()}
    labels.update({17: '英雄皮肤', 18: '乱斗 / 冒险', 1453: '酒馆战棋', 1586: '佣兵战纪',
                   1810: '旧核心', 16: '制作人员', 1143: '时光酒馆', 1904: '教程'})
    service.store.set_meta('filter_sets', [{'value': str(sid), 'label': labels.get(sid, f'系列 {sid}')}
        for sid in sorted({r[0] for r in db.execute('SELECT DISTINCT set_id FROM card_sets')},
                          key=lambda sid: set_records.get(sid, {}).get('m_releaseOrder', 0), reverse=True)])
    # 全局播报员并非可用卡牌，使用明确的工具内实体，不冒用某张卡牌的 DBF ID。
    with db:
        db.execute("INSERT OR REPLACE INTO cards VALUES ('NPC_INNKEEPER','旅店老板 · 全局播报','Innkeeper',1,'',?,NULL)",
                   (json.dumps({'npc': 'innkeeper', 'hero_description': {'m_locValues': []}}),))
        db.execute("INSERT OR REPLACE INTO card_filters VALUES ('NPC_INNKEEPER',12,0,0,3,0,0,'npc',0,0)")
    from .encyclopedia import build_encyclopedia
    build_encyclopedia(service, records, tags)
    service.store.set_meta('filters_version', CATALOG_VERSION)
    service.store.set_meta('rotation_year', year)


def options(store):
    from .card_details import RACES
    def pairs(mapping):
        return [{'value': str(k), 'label': v} for k, v in mapping.items()]
    sets = []
    editions = store.get_meta('filter_editions', [])
    for parent in store.get_meta('filter_sets', []):
        sets.append(parent)
        sets.extend({**e, 'label': '↳ ' + e['label']} for e in editions if e['parent'] == parent['value'])
    sets.extend(e for e in editions if e['parent'] not in {s['value'] for s in sets})
    return {'sets': sets, 'classes': pairs(CLASSES),
            'rarities': pairs(RARITIES), 'types': pairs(TYPES),
            'formats': pairs({'standard': '标准（本地轮换参考）', 'wild': '狂野'}),
            'races': pairs(RACES),
            'keywords': [{'value': r[0], 'label': r[0]} for r in store.db.execute('SELECT DISTINCT keyword FROM card_keywords ORDER BY keyword')],
            'hero_groups': pairs({**CLASSES, 'npc': '调酒师 / 全局播报', 'enemy': '敌人 / 冒险角色'})}
