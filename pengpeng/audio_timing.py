"""声音时间证据。时间的坐标系必须明确，不能将状态内延迟冒充全局时间。

CardSoundSpell.m_DelaySec 是相对该卡牌事件的静态延迟，可以用于试听时间轴。
PlayMaker 的 m_Delay 是相对状态进入时刻；缺少对局/动画状态时，只展示证据，
由试听计划采用随主语音叠放的回退约定，不把回退起点写成已确认的资源时间。
"""
import math
import struct


def card_timing(value):
    """只认可客户端明确写入的非负有限秒数，缺失字段保持未知。"""
    delay = value.get('m_DelaySec')
    if type(delay) not in (float, int) or not math.isfinite(delay) or not 0 <= delay <= 60:
        return None
    return {'seconds': round(delay, 6), 'resolved': True, 'anchor': 'card_event',
            'source': 'CardSoundData.m_DelaySec', 'label': f'卡牌事件开始后 {delay:g} 秒'}


def fsm_audio_actions(tree):
    """解码 PlayMaker 序列化参数；只读有明确类型的声音动作，不执行状态机。"""
    for state in tree.get('fsm', {}).get('states', []):
        data = state.get('actionData', {})
        names, starts = data.get('actionNames', []), data.get('actionStartIndex', [])
        fields = data.get('paramName', [])
        for index, name in enumerate(names):
            if name.rsplit('.', 1)[-1] not in ('AudioPlaythroughAction', 'AudioPlayClipAction'):
                continue
            if index >= len(starts) or not data.get('actionEnabled', [1] * len(names))[index]:
                continue
            params = {}
            try:
                for n in range(starts[index], starts[index + 1] if index + 1 < len(starts) else len(fields)):
                    field, pos, kind = fields[n], data['paramDataPos'][n], data['paramDataType'][n]
                    if field == 'm_Delay' and kind == 2:
                        params['delay'] = struct.unpack_from('<f', bytes(data['byteData']), pos)[0]
                    elif field in ('m_OneShotClip', 'm_OneShotSound') and kind == 24:
                        value = data['fsmObjectParams'][pos]
                        if not value.get('useVariable') and value.get('value', {}).get('m_PathID'):
                            params[field] = value['value']
                delay = params.get('delay')
                if delay is not None and math.isfinite(delay) and 0 <= delay <= 60:
                    yield params, {'seconds': round(delay, 6), 'resolved': False,
                        'anchor': 'state_entry', 'state': state['name'], 'source': name + '.m_Delay',
                        'label': f'进入 {state["name"]} 状态后 {delay:g} 秒；状态进入时刻由游戏决定'}
            except (KeyError, IndexError, TypeError, ValueError, struct.error):
                # 新客户端变更序列化格式时退回未知，不用错位参数生成错误时间轴。
                continue


def describe_timing(item):
    timing = item.get('timing') or {}
    return timing.get('label', '触发时刻依赖游戏状态，尚无可确认的静态时间')
