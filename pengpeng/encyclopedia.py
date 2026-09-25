"""本地百科索引：版本、发行批次、词条和可回溯的卡牌关系。

只在客户端版本或目录结构升级时构建；翻页只读 SQLite。原始卡牌 ID 永远
保留，合并只是展示分组，不影响收藏、导出、筛选以及资源定位。
"""
import json
import logging
import re
from collections import defaultdict

from .common import localized, references, guid

log = logging.getLogger(__name__)

# 3000 型的八种组装外壳使用不同原画，但属于同一构筑选择器。
# 这是已核对的客户端实体集合，不将任意 t 后缀（常见衍生牌）全都折叠。
ASSEMBLY_APPEARANCES = {'TOY_330', *(f'TOY_330t{i}' for i in range(5, 13))}


def build_encyclopedia(service, records, tags):
    db, reader = service.store.db, service.reader
    db.executescript('''
        CREATE TABLE IF NOT EXISTS card_groups(id TEXT PRIMARY KEY, group_key TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS groups_key ON card_groups(group_key,id);
        CREATE TABLE IF NOT EXISTS card_editions(id TEXT, edition TEXT, PRIMARY KEY(id,edition));
        CREATE INDEX IF NOT EXISTS editions_key ON card_editions(edition,id);
        CREATE TABLE IF NOT EXISTS voice_links(hero TEXT, card TEXT, reference TEXT,
            condition TEXT, PRIMARY KEY(hero,card,reference));
        CREATE INDEX IF NOT EXISTS voice_links_card ON voice_links(card,hero);
    ''')
    cards = {r['id']: dict(r) for r in db.execute('SELECT id,name,hero,data FROM cards')}
    identities = {json.loads(r['data']).get('dbfid'): cid for cid, r in cards.items()}
    # 卡面引号是明确命名的实体引用。只有名称在本地唯一时才补充，避免将
    # “火花机器人”等同名但不同属性的多个实体猜成同一张牌。
    named = defaultdict(list)
    for cid, r in cards.items():
        named[r['name']].append(cid)
    relations = []
    for cid, r in cards.items():
        text = localized(json.loads(r['data']), 'm_textInHand')
        for name in re.findall(r'[“「"]([^”」"\n]+)[”」"]', text):
            targets = named.get(name, [])
            if len(targets) == 1 and targets[0] != cid:
                relations.append((cid, targets[0]))
    # MINI_SET 是客户端的产品批次表，当前同时容纳迷你系列和职业系列。
    # 用 DECK_CARD 的实际成员关联大系列，不依赖前缀、年份或固定营销策略。
    editions, memberships = [], []
    decks = {r['m_ID']: r for r in records('DECK')}
    deck_cards = defaultdict(set)
    for r in records('DECK_CARD'):
        if r['m_cardId'] in identities:
            deck_cards[r['m_deckId']].add(identities[r['m_cardId']])
    for r in records('MINI_SET'):
        members = sorted(deck_cards[r['m_deckId']])
        if not members:
            continue
        title = localized(decks.get(r['m_deckId'], {}), 'm_name') or localized(r, 'm_goldenName')
        title = re.sub(r'^\d+\.\d+\s*', '', title).replace('金色', '').strip()
        key = 'edition:' + str(r['m_ID'])
        parent = db.execute('SELECT set_id,COUNT(*) AS n FROM card_sets WHERE id IN (' +
                            ','.join('?' for _ in members) + ') GROUP BY set_id ORDER BY n DESC', members).fetchone()
        editions.append({'value': key, 'label': title or f'补充系列 {r["m_ID"]}',
                         'parent': str(parent[0]) if parent else '', 'kind': '职业系列' if '职业' in title else '补充系列'})
        memberships.extend((cid, key) for cid in members)
    keywords = [{'name_key': r['m_name'], 'text_key': r['m_text']} for r in records('KEYWORD_TEXT')]
    # 按包排序减少反复解压。只读取 CardDef 和声音条件组件，不碰整张原画和音频。
    group_rows, sound_cards = [], defaultdict(set)
    ordered = sorted(reader.cards, key=lambda cid: (reader.catalog.get(reader.cards[cid], ''), cid))
    for index, cid in enumerate(ordered):
        if cid not in cards:
            continue
        if index % 250 == 0:
            service.emit({'event': 'progress', 'message': '建立卡牌版本与专属语音关系…',
                          'done': index, 'total': len(ordered)})
        try:
            definition = reader.definition(cid)
            portrait = guid(definition.get('m_PortraitTexturePath', ''))
            if cid in ASSEMBLY_APPEARANCES:
                portrait = 'assembly:TOY_330'
            record = cards[cid]
            tag = tags.get(json.loads(record['data']).get('dbfid'), {})
            key = json.dumps([record['name'], portrait or cid, record['hero'], tag.get(202),
                              tag.get(1440), bool(tag.get(1471))], ensure_ascii=False)
            group_rows.append((cid, key))
            for field, ref in references(definition):
                if '.prefab:' in ref.lower() and ('Sound' in field or 'Emote' in field):
                    sound_cards[ref].add(cid)
        except (KeyError, ValueError, FileNotFoundError) as exc:
            log.warning('版本关系未读取 %s: %s', cid, exc)
    # 条件依赖实际 GameTag，而不是从 VO 文件名猜皮肤。Scarlet 的多个皮肤
    # 共享角色标签，正是不能只匹配 HERO_ 前缀的原因。
    tag_heroes = defaultdict(set)
    for rid, values in tags.items():
        cid = identities.get(rid)
        if cid and cards[cid]['hero']:
            for tag, value in values.items():
                tag_heroes[(tag, value)].add(cid)
    links = []
    candidates = [(ref, sources) for ref, sources in sound_cards.items()
                  if any(not cards[cid]['hero'] for cid in sources)]
    # CardDef 所在包与声音预制体所在包并不一致。按声音包再次排序，
    # 避免两万余次跨包访问挤掉 64 MiB 热缓存、反复解压相同目录。
    candidates.sort(key=lambda pair: (reader.catalog.get(guid(pair[0]), ''), pair[0]))
    for index, (ref, sources) in enumerate(candidates):
        if index % 25 == 0:
            service.emit({'event': 'progress', 'message': '解析英雄专属语音条件',
                          'done': index, 'total': len(candidates)})
        if not any(not cards[cid]['hero'] for cid in sources):
            continue
        try:
            root, _, _ = reader.resolve(ref, 'global')
            # 条件表位于声音预制体的 MonoBehaviour 中；不用 walk 解码音频叶节点。
            tree = root.read_typetree()
            for comp in tree.get('m_Component', []):
                child = reader.pointer(root, comp['component'])
                if not child or child.type.name != 'MonoBehaviour':
                    continue
                for condition in child.read_typetree().get('m_CardSpecificVoDataList', []):
                    # 4 是目标方分支：面对某英雄说话的随从并不属于该英雄的声音。
                    # 专属英雄触发仅接受友方英雄条件，避免倒置说话者和对话对象。
                    if condition.get('m_SideToSearch') != 3 or 2 not in condition.get('m_ZonesToSearch', []):
                        continue
                    owner = condition.get('m_CardId')
                    owners = {owner} if owner in cards else tag_heroes.get(
                        (condition.get('m_RequireTag'), condition.get('m_TagValue')), set())
                    for hero in owners:
                        for cid in sources:
                            if cid != hero and not cards[cid]['hero']:
                                links.append((hero, cid, ref, json.dumps(condition)))
        except (KeyError, ValueError, FileNotFoundError) as exc:
            log.warning('专属语音条件未读取 %s: %s', ref, exc)
    with db:
        db.execute('DELETE FROM card_groups')
        db.executemany('INSERT INTO card_groups VALUES (?,?)', group_rows)
        db.execute('DELETE FROM card_editions')
        db.executemany('INSERT OR IGNORE INTO card_editions VALUES (?,?)', memberships)
        db.executemany("UPDATE card_releases SET release_date=NULL,source='补充系列首发日期未收录' WHERE id=?",
                       [(cid,) for cid in {cid for cid, _ in memberships}])
        db.execute('DELETE FROM voice_links')
        db.executemany('INSERT OR IGNORE INTO voice_links VALUES (?,?,?,?)', links)
        db.executemany('INSERT OR IGNORE INTO card_relations VALUES (?,?)', relations)
        from .card_relations import supplement_relations
        supplement_relations(db)
    service.store.set_meta('filter_editions', editions)
    service.store.set_meta('keywords', keywords)


def versions(store, cardid, locale):
    """返回完整组员，不受当前列表筛选约束，便于解释核心/原版/冒险差异。"""
    if not store.db.execute("SELECT 1 FROM sqlite_master WHERE name='card_groups'").fetchone():
        return []
    rows = store.db.execute('''SELECT c.id,c.name,c.data FROM cards c JOIN card_groups g ON c.id=g.id
        WHERE g.group_key=(SELECT group_key FROM card_groups WHERE id=?) ORDER BY c.id''', (cardid,)).fetchall()
    from .card_details import catalog_summaries
    summaries = catalog_summaries(store, [r['id'] for r in rows])
    result = []
    for r in rows:
        cid = r['id']
        summary = summaries.get(cid, {})
        context = ('核心版本' if cid.startswith('CORE_') else '经典怀旧' if cid.startswith('VAN_') else
                   '剧情 / 冒险' if cid.startswith(('Story_', 'BOM_', 'RLK_Prologue_')) else
                   '乱斗 / 特殊模式' if cid.startswith('TB_') else '客户端版本')
        if cid in ASSEMBLY_APPEARANCES:
            context = '构筑选择器' if cid == 'TOY_330' else '拼装外观 ' + cid.removeprefix('TOY_330t')
        result.append({'id': cid, 'name': localized(json.loads(r['data']), 'm_name', locale) or r['name'],
                       'context': context, 'summary': summary,
                       'text': localized(json.loads(r['data']), 'm_textInHand', locale)})
    return result
