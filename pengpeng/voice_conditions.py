"""声音条件与事件归属；保留客户端证据，不根据台词内容猜测角色。"""
import json


def emote_condition(key):
    """节日及镜像条件来自 CardDef 的事件键，不从字幕猜测对局对象。"""
    key = key.upper()
    for token, label in (
        ('MIRROR_START','镜像对局开场：面对相同英雄'),
        ('MIRROR_GREETINGS','镜像对局中使用问候表情'),
        ('HAPPY_NEW_YEAR_LUNAR','客户端春节问候事件'),
        ('LUNARNEWYEAR','客户端春节问候事件'),
        ('HAPPY_NEW_YEAR','客户端新年问候事件'),
        ('HAPPY_HOLIDAYS','客户端冬幕节问候事件'),
        ('WINTERVEIL','客户端冬幕节问候事件'),
        ('FIRE_FESTIVAL','客户端火焰节问候事件'),
        ('PIRATE_DAY','客户端海盗日问候事件'),
        ('HAPPY_HALLOWEEN','客户端万圣节问候事件'),
        ('HALLOWSEND','客户端万圣节问候事件'),
        ('NOBLEGARDEN','客户端复活节问候事件')):
        if token in key:
            return label
    return ''


def event_group(field):
    """英雄牌自定义登场动画与登场语音属于同一试听事件。"""
    if field.startswith(('m_PlayEffectDef', 'm_CustomSummonSpellPath')):
        return 'play'
    return field.split('.')[0]


def audible_effect(effect):
    """商店、收藏展示及肖像场景可能引用其他角色模板，不是角色事件。"""
    return 'Phone' not in effect['field'] and effect['field'].startswith(('m_PlayEffectDef', 'm_AttackEffectDef', 'm_DeathEffectDef',
        'm_LifetimeEffectDef', 'm_AdditionalPlayEffectDefs', 'm_TriggerEffectDefs', 'm_SubSpellEffectDefs',
        'm_CustomSummonSpellPath', 'm_EmoteDefs', 'm_AnnouncerLinePath', 'm_SpecialEvents',
        'm_SocketInEffect', 'm_SpellTableOverrides'))


def matches_owner(condition, cardid, tags):
    """专属语音索引中的条件指向说话英雄；只选择该英雄实际满足的分支。"""
    if condition.get('m_SideToSearch') != 3:
        return False
    owner = condition.get('m_CardId')
    if owner:
        return owner == cardid
    tag = condition.get('m_RequireTag')
    return bool(tag) and tags.get(str(tag)) == condition.get('m_TagValue')


def describe_condition(store, condition, locale='zhcn'):
    if not condition:
        return '', []
    from .common import localized
    cid = condition.get('m_CardId')
    if cid:
        rows = store.db.execute('SELECT id,name,data FROM cards WHERE id=?', (cid,)).fetchall()
    elif condition.get('m_RequireTag') and store.db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='card_tags'").fetchone():
        rows = store.db.execute('''SELECT c.id,c.name,c.data FROM cards c JOIN card_tags t ON t.id=c.id
            WHERE t.tag=? AND t.value=? ORDER BY c.id''',
            (condition['m_RequireTag'], condition.get('m_TagValue', 0))).fetchall()
    else:
        rows = []
    targets = [{'id':r['id'], 'name':localized(json.loads(r['data']), 'm_name', locale) or r['name']} for r in rows]
    names = list(dict.fromkeys(r['name'] for r in targets))
    # 来自本机 Blizzard.T5.Game.dll 的 SpellPlayerSide / SpellZoneTag。
    # SOURCE/TARGET 是事件双方，不能误用 Power.log 的 Zone 或 Side 枚举。
    side = {0:'中立方',1:'友方',2:'对手',3:'来源方（使用者）',4:'目标方（触发对象）',5:'双方'}.get(condition.get('m_SideToSearch'), '条件对象')
    zones = {1:'战场随从',2:'英雄',3:'英雄技能',4:'武器',5:'牌库',6:'手牌',7:'墓地',8:'奥秘',9:'战棋伙伴',10:'任务奖励',11:'饰品'}
    locations = '、'.join(zones.get(z, f'区域 {z}') for z in condition.get('m_ZonesToSearch', []))
    target = (' / '.join(names[:3]) + (f' 等 {len(names)} 个外观' if len(names) > 3 else '')) or (cid or f"标签 {condition.get('m_RequireTag')} = {condition.get('m_TagValue')}")
    return f"{side}：{target}" + (f'；检查区域：{locations}' if locations else ''), targets
