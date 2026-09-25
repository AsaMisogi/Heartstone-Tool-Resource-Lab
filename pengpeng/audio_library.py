"""可解释的中文声音索引：保留原始名称，不将命名注释冒充官方曲名。"""
import re
from .music_titles import CLASSIC_MUSIC

VERSION = 4
SETS = {'EX1':'经典', 'CORE':'核心', 'NAX':'纳克萨玛斯', 'GVG':'地精大战侏儒',
    'BRM':'黑石山', 'AT':'冠军的试炼', 'LOE':'探险者协会', 'OG':'上古之神',
    'KARA':'卡拉赞', 'CFM':'龙争虎斗加基森', 'UNG':'安戈洛', 'ICC':'冰封王座',
    'LOOT':'狗头人与地下世界', 'K&C':'狗头人与地下世界', 'GIL':'女巫森林',
    'BOT':'砰砰计划', 'TRL':'拉斯塔哈的大乱斗', 'DAL':'暗影崛起', 'ULD':'奥丹姆',
    'DRG':'巨龙降临', 'BT':'外域', 'SCH':'通灵学园', 'DMF':'暗月马戏团',
    'BAR':'贫瘠之地', 'SW':'暴风城', 'AV':'奥特兰克', 'ONY':'奥妮克希亚',
    'TSC':'探寻沉没之城', 'REV':'纳斯利亚堡', 'RLK':'巫妖王', 'ETC':'音乐节',
    'TTN':'泰坦诸神', 'WW':'荒芜之地', 'TOY':'威兹班', 'VAC':'胜地历险',
    'GDB':'深暗领域', 'EDR':'翡翠梦境', 'TLC':'安戈洛龟途', 'TIME':'时间主题',
    'CATA':'大地的裂变', 'JAIL':'监狱主题'}


def labels(name, bundle, category):
    """用途规则优先；不能判断场景时明确归入待细分，不编造标题。"""
    key = name.lower()
    tokens = re.split(r'[_\-]', name.upper())
    theme = SETS.get(tokens[0], '')
    if tokens[0] == 'BOARD':
        theme = next((SETS[t] for t in tokens[1:3] if t in SETS), '')
    if category in ('背景音乐', '短音乐 / 登场曲'):
        if key in CLASSIC_MUSIC:
            group = '经典默认音乐'
        elif 'heromusic' in bundle or key.startswith(('heromusic_', 'heroskin_')):
            group = '英雄主题音乐'
        elif any(x in key for x in ('find_opponent', 'mulligan', 'victory', 'defeat', 'win_stinger')):
            group = '匹配 / 起手 / 结算'
        elif 'store' in key or 'shop' in key:
            group = '商店'
        elif 'menu' in key or 'ui_music' in key or 'main_title' in key:
            group = '主界面 / 模式菜单'
        elif 'board' in key:
            group = '棋盘互动音乐'
        elif 'stinger' in key:
            group = '卡牌登场短曲'
        elif 'jingle' in key:
            group = '界面提示短曲'
        elif category == '短音乐 / 登场曲':
            group = '音乐片段 / 前奏尾奏'
        elif 'bacon' in key or 'battleground' in key or key.startswith('bg_') or '_bgs_' in bundle:
            group = '酒馆战棋'
        elif theme or 'musicexpansion' in bundle or '_adventure_' in bundle:
            group = '冒险 / 扩展主题'
        else:
            group = '其他音乐 / 待细分'
    elif category == '音效' and any(x in key for x in ('ambient', 'amb_', 'ambience', 'wallah', 'board', 'tavern')):
        group = ('棋盘 / ' + theme if theme else '棋盘互动') if 'board' in key else '酒馆环境' if 'tavern' in key else '环境氛围'
    elif category == '音效' and any(x in key for x in ('ui_', 'button', 'menu', 'collection', 'pack', 'store', 'shop')):
        group = ('卡包开启' if 'pack' in key else '收藏 / 组牌' if 'collection' in key
                 else '商店' if 'store' in key or 'shop' in key else '菜单 / 操作反馈')
    else:
        group = '角色台词' if category == '角色语音' else '战斗 / 法术音效' if any(x in key for x in ('spell', 'attack', 'impact', 'death', 'damage', 'cast', 'summon')) else '其他音效 / 待细分'
    descriptors = [zh for token, zh in [('loop','循环段'),('intro','前奏'),('outro','尾奏'),
        ('ending','收尾'),('boss','首领'),('mulligan','起手换牌'),('windup','开始'),
        ('winddown','结束'),('ambient','环境氛围')] if token in key]
    annotation = ' · '.join(dict.fromkeys(x for x in (theme, group, *descriptors) if x))
    return {'subgroup': group, 'annotation': annotation, 'annotation_source': '资源命名规则'}


def update_labels(store, rows):
    """与资产写入共用事务；不改变原 assets 列数和资源 ID。"""
    if not hasattr(store, '_audio_card_names'):
        # 中英文卡名只做去空格/标点后的精确匹配；同英文对应多个中文时放弃。
        # 不用相似度将人物、地点或音轨标题误译成另一张卡牌。
        names, ids = {}, {}
        for card in store.db.execute('SELECT id,name,ename FROM cards'):
            ids[card['id']] = card['name']
            key = re.sub(r'[^a-z0-9]', '', (card['ename'] or '').lower())
            if key:
                names.setdefault(key, set()).add(card['name'])
        store._audio_card_names = {k: next(iter(v)) for k, v in names.items() if len(v) == 1}
        store._audio_card_ids = ids
    values = []
    for row in rows:
        if row[5] == 'AudioClip':
            data = labels(row[4], row[1], row[6])
            if row[6] in ('背景音乐', '短音乐 / 登场曲'):
                candidates = re.split(r'(?i)(?:legendary)?stinger[_\-]?|heromusic_|heroskin_', row[4])
                name = next((store._audio_card_names.get(re.sub(r'[^a-z0-9]', '', c.lower()))
                             for c in candidates if re.sub(r'[^a-z0-9]', '', c.lower()) in store._audio_card_names), '')
                match = re.match(r'([A-Z]+_\d+[a-z]?)_', row[4])
                if not name and match:
                    name = store._audio_card_ids.get(match[1], '')
                if name:
                    data['annotation'] = name + ' · ' + data['annotation']
            values.append((row[0], data['subgroup'], data['annotation']))
    store.db.executemany('INSERT OR REPLACE INTO audio_labels VALUES (?,?,?)', values)
