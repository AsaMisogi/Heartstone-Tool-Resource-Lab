"""通用声音试听规则：以本地卡牌标签和真实资源引用为依据。

不模拟战斗状态机。登场时叠加落地、初始嘲讽和初始圣盾；种族/材质的
Underlay 必须由该卡牌自己的同一事件引用，不以“机械”等文字搜索猜测。
"""
import re


def base_event(item):
    """只接受 CardDef 基础事件，触发、英雄表情不借用登场音效。"""
    match = re.match(r'^m_(Play|Attack|Death)EffectDef(?:\.|$)', item.get('event', ''))
    return match[1] if match else None


def shared_rules(tags):
    """标签来自 CARD_TAG：202=类型、190=嘲讽、194=圣盾。

    名称白名单用于排除同一特效内的破盾、升级及持续音轨，不能把整个特效
    引用图里的音频同时播放。落地使用普通随从的默认预览，不猜战斗中身材。
    """
    if tags.get(202) != 4:
        return []
    rules = [(2, '随从落地', {'FX_MinionSummon_Drop'})]
    if tags.get(190):
        rules.append((8, '嘲讽盾牌展开', {'taunt_shield_up'}))
    if tags.get(194):
        rules.append((9, '圣盾展开', {'spell_DivineShield_target_1'}))
    return rules


def event_underlays(items, event):
    """种族、武器、材质垫音已经有卡牌级引用；保持其真实事件关联。"""
    return [item for item in items if item.get('kind') == 'sound'
            and base_event(item) == event and re.search(r'underlay', item.get('name', ''), re.I)]
