"""详情展示层：复用本地图鉴索引，不为打开侧栏扫描资源包。"""
import json

from .catalog import CLASSES, RARITIES, TYPES
from .common import localized

# 客户端 Race 枚举；未收录的种族保留编号，避免新版本被错误归类。
RACES = {1: '血精灵', 2: '德莱尼', 3: '矮人', 4: '侏儒', 5: '地精',
         6: '人类', 7: '暗夜精灵', 8: '兽人', 9: '牛头人', 10: '巨魔',
         11: '亡灵', 12: '狼人', 14: '鱼人', 15: '恶魔', 17: '机械',
         18: '元素', 20: '野兽', 21: '图腾', 23: '海盗', 24: '龙',
         26: '全部', 43: '野猪人', 92: '纳迦', 95: '半人马'}
# 普通打造 / 分解，金卡打造 / 分解；常规价格，不推断服务器限时返尘。
DUST = {1: (40, 5, 400, 50), 3: (100, 20, 800, 100),
        4: (400, 100, 1600, 400), 5: (1600, 400, 3200, 1600)}


def catalog_summaries(store, cardids):
    """只批量读取当前页的展示字段；不逐卡查询详情、关系或解码 Unity。

    多职业和多系列分开查询，避免联表产生笛卡尔积。每页最多 100 张，
    查询次数固定；旧索引无详情表时返回空摘要，沿用原有列表浏览能力。
    """
    if not cardids or not store.db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='card_details'").fetchone():
        return {}
    marks = ','.join('?' for _ in cardids)
    labels = {int(s['value']): s['label'] for s in store.get_meta('filter_sets', [])}
    result = {}
    for row in store.db.execute(f'''SELECT f.*, d.data FROM card_filters f
            LEFT JOIN card_details d ON d.id=f.id WHERE f.id IN ({marks})''', cardids):
        raw = json.loads(row['data']) if row['data'] else {}
        result[row['id']] = {
            'type': TYPES.get(row['card_type'], '其他'),
            'rarity': RARITIES.get(row['rarity'], '未标注'),
            'cost': row['cost'], 'attack': raw.get('attack'),
            'health': raw.get('health'), 'durability': raw.get('durability'),
            'races': [RACES.get(r, f'种族 {r}') for r in raw.get('races', [])],
            'classes': [], 'sets': [], 'battlegrounds': bool(row['bg']),
        }
    for row in store.db.execute(
            f'SELECT id,class_id FROM card_classes WHERE id IN ({marks}) ORDER BY class_id', cardids):
        if row['id'] in result:
            result[row['id']]['classes'].append(CLASSES.get(row['class_id'], f"职业 {row['class_id']}"))
    for row in store.db.execute(
            f'SELECT id,set_id FROM card_sets WHERE id IN ({marks}) ORDER BY set_id', cardids):
        if row['id'] in result:
            result[row['id']]['sets'].append(labels.get(row['set_id'], f"系列 {row['set_id']}"))
    return result


def card_metadata(store, cardid, locale):
    """旧测试索引 / 尚未升级的索引允许无详情；正常初始化会升级目录版本。"""
    db = store.db
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='card_details'").fetchone():
        return {}, []
    row = db.execute('SELECT data FROM card_details WHERE id=?', (cardid,)).fetchone()
    facet = db.execute('SELECT * FROM card_filters WHERE id=?', (cardid,)).fetchone()
    if not row or not facet:
        return {}, []
    raw, facet = json.loads(row[0]), dict(facet)
    sets = [r[0] for r in db.execute('SELECT set_id FROM card_sets WHERE id=?', (cardid,))]
    labels = {int(s['value']): s['label'] for s in store.get_meta('filter_sets', [])}
    formats = [label for key, label in [('standard', '标准（本地轮换参考）'), ('wild', '狂野'),
                                      ('bg', '酒馆战棋')] if facet[key]]
    rarity = facet['rarity']
    dust = DUST.get(rarity)
    unavailable = not facet['collectible'] or bool(set(sets) & {1637, 1810}) or dust is None
    # m_craftingEvent 是服务器活动条件，非负只表示有条件可打造，不代表当前账号已解锁。
    normal = not unavailable and raw['crafting_event'] not in (-1, 0, 164)
    # 旧卡金卡条件常为 UNKNOWN(-1)，没有独立覆写时展示常规金卡参考价。
    golden = not unavailable and (normal if raw['golden_crafting_event'] == -1
                                 else raw['golden_crafting_event'] not in (0, 164))
    metadata = {'rarity': RARITIES.get(rarity, '未标注'), 'type': TYPES.get(facet['card_type'], '其他'),
                'cost': facet['cost'], 'attack': raw['attack'], 'health': raw['health'],
                'durability': raw['durability'],
                'races': [RACES.get(r, f'种族 {r}') for r in raw['races']],
                'classes': [CLASSES.get(r[0], f'职业 {r[0]}') for r in db.execute(
                    'SELECT class_id FROM card_classes WHERE id=?', (cardid,))],
                'sets': [labels.get(s, f'系列 {s}') for s in sets], 'formats': formats,
                'dust': {'normal': list(dust[:2]) if normal else None,
                         'golden': list(dust[2:]) if golden else None},
                'dust_note': '常规奥术之尘参考（打造 / 分解）；活动、获取方式和账号限制以游戏为准。'}
    # 同时显示来源牌，衍生牌可以自然回溯；UNION 消除双向记录的重复项。
    related = db.execute('''SELECT c.id,c.name,c.data FROM cards c JOIN (
        SELECT related AS id FROM card_relations WHERE id=?
        UNION SELECT id FROM card_relations WHERE related=?
        ) r ON c.id=r.id ORDER BY c.id''', (cardid, cardid)).fetchall()
    return metadata, [{'id': r['id'], 'name': localized(json.loads(r['data']), 'm_name', locale) or r['name']}
                      for r in related]
