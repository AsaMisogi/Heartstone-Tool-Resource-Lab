"""0.13：Actor 注册表隔离、导航取消与详情偏好升级。"""
from types import SimpleNamespace

import pytest

from pengpeng.preferences import validate_views
from pengpeng.service import Service
from pengpeng.unity import UnityReader


def test_actor_registry_is_not_an_audio_dependency_but_explicit_table_still_works():
    """最小复现：卡牌动画引用展示 Actor，它又引用整套通用声音注册表。"""
    objects = {}
    def make(identity, kind, tree):
        obj = SimpleNamespace(path_id=identity, type=SimpleNamespace(name=kind),
                              assets_file=SimpleNamespace(name='CAB-test'), read_typetree=lambda: tree)
        objects[identity] = obj
        return obj
    ptr = lambda n: {'m_FileID': 0, 'm_PathID': n}
    registry = 'Shared.prefab:' + 'a' * 32
    make(1, 'MonoBehaviour', {'actor': ptr(2), 'sound': ptr(3)})
    make(2, 'MonoBehaviour', {'m_spellTablePrefab': registry})
    make(3, 'AudioClip', {})
    make(4, 'MonoBehaviour', {'m_Table': [{'sound': ptr(5)}]})
    make(5, 'AudioClip', {})
    reader = UnityReader.__new__(UnityReader)
    reader.pointer = lambda _, p: objects.get(p.get('m_PathID'))
    calls = []
    def resolve(ref, locale):
        calls.append(ref)
        return objects[4], 'shared', 'global'
    reader.resolve = resolve
    assert [o.path_id for o, _ in reader.walk(objects[1]) if o.type.name == 'AudioClip'] == [3]
    assert calls == []  # 必须在读取大注册表之前截断，而不是收集完再过滤。
    assert [o.path_id for o, _ in reader.walk(objects[4]) if o.type.name == 'AudioClip'] == [5]


def test_navigation_cancels_before_next_object_and_does_not_cache_partial_audio(tmp_path):
    reader = UnityReader.__new__(UnityReader)
    with pytest.raises(InterruptedError):
        list(reader.walk(None, cancelled=lambda: True))
    s = Service(tmp_path)
    s.card = lambda *args: {'effects': [{'field': 'm_PlayEffectDef', 'ref': 'a', 'name': 'test'}]}
    def cancelled(*args):
        raise InterruptedError('导航已改变')
    s.related_audio = cancelled
    with pytest.raises(InterruptedError):
        s.card_audio('A')
    assert not s.card_voices


def test_detail_width_persists_and_rejects_invalid_values(tmp_path):
    s = Service(tmp_path)
    assert s.settings['detail_width'] == 610
    s.save_settings(detail_width=920)
    assert Service(tmp_path).settings['detail_width'] == 920
    for width in (True, 359, 2401, 600.5, '800', None):
        with pytest.raises(ValueError):
            s.save_settings(detail_width=width)


def test_removed_hero_filters_do_not_survive_preferences_migration():
    filters = {'race': '24', 'keyword': '嘲讽', 'hero_group': 'mage'}
    result = validate_views({'heroes': {'filters': filters}, 'cards': {'filters': filters}})
    assert result['heroes']['filters'] == {'hero_group': 'mage'}
    assert result['cards']['filters']['race'] == '24'
