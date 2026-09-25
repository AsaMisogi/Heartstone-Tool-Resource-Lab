"""场景角色的全局台词入口，不要求用户先建立整套资源索引。"""
import hashlib
import re

from .audio_strings import audio_key
from .common import bundle_locale, audio_category

# 已核对的巫妖王主体，不按中文同名自动绑定其他剧情实体。
LICH_KING_CARDS = {'HERO_11', 'ICC_314', 'CORE_ICC_314', 'ICCA01_001', 'ICCA08_001', 'TB_BaconShop_HERO_22'}


def adventure_event(name):
    """保留原事件键，同时给可确认的职业、回应增加中文索引词。"""
    key = name.upper().split('_MALE_HUMAN_', 1)[-1]
    classes = {'MAGE': '法师', 'SHAMAN': '萨满祭司', 'ROGUE': '潜行者',
               'PALADIN': '圣骑士', 'PRIEST': '牧师', 'DRUID': '德鲁伊',
               'HUNTER': '猎人', 'WARRIOR': '战士', 'WARLOCK': '术士'}
    label = next(('面对' + label for token, label in classes.items() if key.startswith(token + '_')), '')
    if 'RESPONSE' in key:
        label = '回应 / 互动'
    return '冰冠堡垒冒险 · ' + (label + ' · ' if label else '') + name.split('_Male_Human_')[-1]


def npc_audio(service, card, locale):
    prefixes = set()
    adventure = card['id'] in LICH_KING_CARDS
    if card['record'].get('npc') == 'innkeeper':
        prefixes.add('VO_INNKEEPER_')
    if card['id'] == 'TB_BaconShopBob':
        prefixes.update(('VO_BACON_BOB_', 'VO_DALA_BOSS_99H_', 'VO_BOB_'))
    for emote in card['definition'].get('m_EmoteDefs', []):
        key = emote.get('m_emoteGameStringKey', '').upper()
        match = re.match(r'(VO_BGBAR_\d+_)', key)
        if match:
            prefixes.add(match[1])
    if card['id'].startswith('TB_BaconShopBob_SKIN_'):
        prefixes.add('VO_' + card['id'].upper() + '_')
    if not prefixes and not adventure:
        return {'items': [], 'errors': []}
    errors = []
    marker = 'npc_audio_index_v1_' + locale
    if not service.store.get_meta(marker):
        bundles = [name for name in service.reader.source_paths()
                   if bundle_locale(name) == locale and '-audio-' in name]
        for index, bundle in enumerate(bundles):
            service.emit({'event': 'detail_progress', 'cardid': card['id'], 'done': index,
                          'total': len(bundles), 'message': '读取场景角色语音目录…'})
            # 已完整扫描的资源包不用再次打开。
            if service.store.db.execute("SELECT 1 FROM bundles WHERE name=? AND error=''", (bundle,)).fetchone():
                continue
            try:
                rows = []
                for obj in service.reader.load(bundle).objects:
                    if obj.type.name != 'AudioClip':
                        continue
                    audio = obj.read()
                    identity = hashlib.sha256(f'{bundle}:{obj.path_id}'.encode()).hexdigest()[:24]
                    rows.append((identity, bundle, str(obj.path_id), '', audio.m_Name, 'AudioClip',
                                 audio_category(audio.m_Name, bundle, audio.m_Length), locale, audio.m_Length))
                with service.store.db:
                    service.store.db.executemany('INSERT OR REPLACE INTO assets VALUES (?,?,?,?,?,?,?,?,?)', rows)
            except (OSError, KeyError, ValueError) as exc:
                errors.append(f'{bundle}: {exc}')
        if not errors:
            service.store.set_meta(marker, True)
        service.emit({'event': 'detail_progress', 'cardid': card['id'], 'done': len(bundles),
                      'total': len(bundles), 'message': '场景角色语音目录读取完成'})
    # 用 startswith 保证角色边界；SQL LIKE 的下划线不是字面字符。
    items = []
    for row in service.store.db.execute("SELECT * FROM assets WHERE kind='AudioClip' AND locale=?", (locale,)):
        is_adventure = adventure and re.match(r'^VO_ICC\d{2}_LICHKING_MALE_HUMAN_', row['name'].upper())
        if is_adventure or any(row['name'].upper().startswith(prefix) for prefix in prefixes):
            items.append({'id': row['id'], 'name': row['name'], 'locale': locale, 'bundle': row['bundle'],
                          'kind': 'voice', 'event': adventure_event(row['name']) if is_adventure else '场景 / 全局台词',
                          'adventure': bool(is_adventure),
                          'text': service.strings.get(locale, {}).get(audio_key(row['name']), '')})
    return {'items': items, 'errors': errors}
