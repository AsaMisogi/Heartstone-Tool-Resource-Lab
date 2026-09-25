"""补齐客户端关系表遗漏的具名衍生物，并为集合式发现提供正文入口。"""
import json
import re
from collections import defaultdict
from .common import localized


def supplement_relations(db):
    rows = list(db.execute('''SELECT c.id,c.name,c.data,f.collectible,f.card_type
        FROM cards c JOIN card_filters f ON f.id=c.id'''))
    named, sets, trie = defaultdict(list), defaultdict(set), {}
    for r in db.execute('SELECT id,set_id FROM card_sets'):
        sets[r[0]].add(r[1])
    for r in rows:
        # 仅补充非收集的具体实体；不把“发现”“突袭”等机制名当卡牌引用。
        if not r['collectible'] and r['card_type'] in (3,4,5,7,39) and len(r['name']) >= 3:
            named[r['name']].append(r['id'])
    for name in named:
        node = trie
        for ch in name:
            node = node.setdefault(ch, {})
        node[''] = name
    additions = []
    for r in rows:
        text = re.sub(r'<[^>]+>', '', localized(json.loads(r['data']), 'm_textInHand'))
        if not re.search('召唤|置入|获得|变形|发现|洗入|替换', text):
            continue
        cursor = 0
        while cursor < len(text):
            node, end, name = trie, cursor, None
            for index in range(cursor, len(text)):
                node = node.get(text[index])
                if node is None:
                    break
                if '' in node:
                    name, end = node[''], index + 1
            if name and name != r['name']:
                candidates = [cid for cid in named[name] if cid != r['id']]
                same_set = [cid for cid in candidates if sets[cid] & sets[r['id']]]
                candidates = same_set or candidates
                # 同一版本唯一实体才建立关系；同名歧义不能任意挑一张。
                if len(candidates) == 1:
                    additions.append((r['id'], candidates[0]))
            cursor = end if name else cursor + 1
    db.executemany('INSERT OR IGNORE INTO card_relations VALUES (?,?)', additions)
    # 核心/怀旧等同名同图版本共享明确关系，但不复制同组自身，避免关系爆炸。
    db.execute('''INSERT OR IGNORE INTO card_relations
        SELECT b.id,r.related FROM card_relations r JOIN card_groups a ON a.id=r.id
        JOIN card_groups b ON b.group_key=a.group_key
        WHERE b.id<>r.related AND NOT EXISTS(SELECT 1 FROM card_groups t
            WHERE t.id=r.related AND t.group_key=b.group_key)''')


def text_links(cardid, text, related):
    """具名衍生物直接跳转；“神器/宝藏”打开可选列表，不虚构单一目标。"""
    named = defaultdict(list)
    for r in related:
        if r['name'] in text:
            named[r['name']].append(r)
    links = [{'label':name, 'cards':cards} for name,cards in named.items()]
    pools = {'LOE_092': ('神器', lambda cid:cid in ('LOEA16_3','LOEA16_4','LOEA16_5')),
             'DAL_417': ('神奇宝藏', lambda cid:cid.startswith('LOOT_998')),
             'ONY_005': ('宝藏', lambda cid:cid.startswith('ONY_005t'))}
    # 当前客户端的蛋不直接写出凯洛斯名字，而写“20/20…野兽”；入口指向
    # 明确的最终衍生物，仍由当前关系表确认该实体实际存在。
    if cardid.startswith('DINO_410') and '野兽' in text:
        targets = [r for r in related if r['id'] == 'DINO_410t']
        if targets:
            links.append({'label':'野兽', 'cards':targets})
    base = cardid.removeprefix('CORE_').removeprefix('VAN_')
    if base in pools:
        label, accept = pools[base]
        targets = [r for r in related if accept(r['id'])]
        if targets and label in text:
            links.append({'label':label, 'cards':targets})
    return links
