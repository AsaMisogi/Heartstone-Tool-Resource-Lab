"""随从台词与通用音效回归：网络响应固定，正常测试不访问外部站点。"""
import io
import json
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError

import pytest

from pengpeng import transcripts
from pengpeng.transcript_sources import DEFAULT_SOURCES
HUIJI = [s for s in DEFAULT_SOURCES if s["id"] == "huiji"]
from pengpeng.service import Service
from pengpeng.shared_audio import shared_rules, event_underlays


@pytest.fixture(autouse=True)
def reset_circuit(monkeypatch):
    monkeypatch.setattr(transcripts, '_blocked_until', 0)


def voice(identity='v', event='Play', **extra):
    return {'id': identity, 'kind': 'voice', 'locale': 'zhcn', 'text': '',
            'event': f'm_{event}EffectDef.m_SoundSpellPaths[0]', **extra}


def test_multiline_quote_does_not_end_section_at_nested_template():
    page = '|台词=*登场\n{{quote|测试登场<br/>Entry\n}}\n*攻击\n{{quote|测试攻击<br>Attack}}\n|背景=其他'
    assert transcripts.parse_quotes(page) == {'Play': '测试登场', 'Attack': '测试攻击'}


def test_expanded_html_parses_transclusion_but_rejects_ambiguous_events():
    html = '''<h2>背景</h2><ul><li>登场</li></ul><blockquote>不应匹配</blockquote>
      <h2><span id="台词">台词</span></h2><ul><li>登场</li></ul>
      <blockquote><p>测试<b>登场</b>！<br/>English</p></blockquote>
      <ul><li>攻击</li></ul><blockquote>分支一</blockquote><blockquote>分支二</blockquote>
      <ul><li>特殊条件</li></ul><blockquote>不应借用上一个事件</blockquote>
      <h2>趣闻</h2><ul><li>死亡</li></ul><blockquote>不应匹配</blockquote>'''
    assert transcripts.parse_html_quotes(html) == {'Play': '测试登场！'}


def test_core_card_supplement_uses_verified_original_source(tmp_path):
    result = {'quotes': {'Play': '原卡台词'}, 'source': 'https://hearthstone.huijiwiki.com/wiki/Card/49184',
              'source_name': '维基'}
    with patch.object(transcripts, 'fetch_quotes', return_value=result) as fetch:
        data = transcripts.supplement(tmp_path, 99999, [voice(transcript_dbfid=49184)], providers=HUIJI)
    fetch.assert_called_once_with(tmp_path, 49184)
    assert data['items'][0]['text'] == '原卡台词'


def test_failed_network_never_becomes_empty_cache_and_has_recovery(tmp_path):
    error = HTTPError(transcripts.SOURCE, 403, 'Forbidden', {}, None)
    with patch.object(transcripts, 'urlopen', side_effect=error) as fetch:
        first = transcripts.supplement(tmp_path, 559, [voice()], providers=HUIJI)
        second = transcripts.supplement(tmp_path, 724, [voice()], providers=HUIJI)
    assert first['sources'] == [559] and second['sources'] == [724]
    assert '验证' in first['note']
    assert fetch.call_count == 1
    assert not list(tmp_path.rglob('*.json'))
    # 用户在浏览窗口读到台词后，熔断期间仍立即使用缓存，不再次请求网络。
    transcripts.cache_quotes(tmp_path, 724, {'Play': '已读取'})
    assert transcripts.supplement(tmp_path, 724, [voice()], providers=HUIJI)['items'][0]['text'] == '已读取'


def test_old_negative_cache_invalidated_and_expanded_response_cached(tmp_path):
    folder = tmp_path / 'cache/transcripts-v1'
    folder.mkdir(parents=True)
    (folder / '724.json').write_text(json.dumps({'quotes': {}, 'fetched_at': transcripts.time.time()}))
    payload = {'parse': {'text': {'*': '<h2>台词</h2><ul><li>攻击</li></ul><blockquote>新解析结果<br>English</blockquote>'}}}
    with patch.object(transcripts, 'urlopen', return_value=io.BytesIO(json.dumps(payload).encode())) as fetch:
        assert transcripts.fetch_quotes(tmp_path, 724)['quotes'] == {'Attack': '新解析结果'}
        transcripts.fetch_quotes(tmp_path, 724)
    assert fetch.call_count == 1
    assert (tmp_path / 'cache/transcripts-v2/724.json').is_file()


def test_general_sound_is_opt_in_and_independent_of_paired_setting(tmp_path):
    service = Service(tmp_path)
    assert service.settings['general_audio'] is False
    service.save_settings(general_audio=True, paired_audio=False)
    restored = Service(tmp_path)
    assert restored.settings['general_audio'] is True
    assert restored.settings['paired_audio'] is False
    with pytest.raises(ValueError):
        restored.save_settings(general_audio='false')


def test_shared_rules_do_not_add_keyword_sounds_to_other_cards():
    assert shared_rules({202: 3, 190: 1, 194: 1}) == []
    assert [r[0] for r in shared_rules({202: 4})] == [2]
    assert [r[0] for r in shared_rules({202: 4, 190: 1, 194: 1})] == [2, 8, 9]


def test_shared_sound_filters_break_and_upgrade_tracks(tmp_path):
    service = Service(tmp_path)
    service.card = lambda *_: {'record': {'dbfid': 5}}
    service.card_audio = lambda *_: {'items': [voice()]}
    service.card_tags = {5: {202: 4, 190: 1, 194: 1}}
    service.shared_spell_table = {2: 'drop', 8: 'taunt', 9: 'shield'}
    names = {'drop': ['FX_MinionSummon_Drop'], 'taunt': ['taunt_shield_up', 'taunt_shield_break'],
             'shield': ['spell_DivineShield_target_1', 'DivineShield_Upgrade_GetHit_Sound']}
    service.related_audio = lambda ref, _: {'items': [{'id': n, 'name': n, 'kind': 'sound'} for n in names[ref]], 'errors': []}
    data = service.general_audio('CARD', 'v')
    assert {x['name'] for x in data['items']} == {'FX_MinionSummon_Drop', 'taunt_shield_up', 'spell_DivineShield_target_1'}
    service.card_audio = lambda *_: {'items': [voice(event='Attack')]}
    assert service.general_audio('CARD', 'v')['items'] == []


def test_underlays_use_same_event_and_never_include_music_or_voice():
    items = [voice(name='Mech_Play_Underlay', kind='sound'),
             voice('a', 'Attack', name='Mech_Attack_Underlay', kind='sound'),
             voice('m', name='MusicStinger', kind='sound'), voice('speech', name='VO_Play_Underlay')]
    assert [x['id'] for x in event_underlays(items, 'Play')] == ['v']


def test_original_card_mapping_requires_same_event_reference(tmp_path):
    service = Service(tmp_path)
    cards = {'CORE_TEST_123': {'record': {'dbfid': 2}, 'definition': {}, 'effects': [
        {'field': 'm_PlayEffectDef.m_SoundSpellPaths[0]', 'ref': 'shared', 'name': 'Play'}]},
        'TEST_123': {'record': {'dbfid': 1}, 'definition': {}, 'effects': [
        {'field': 'm_PlayEffectDef.m_SoundSpellPaths[0]', 'ref': 'shared', 'name': 'Play'}]}}
    service.reader = SimpleNamespace(cards=cards, source_paths=lambda: {})
    service.audio_installed = lambda _: True
    service.card = lambda cid, _: cards[cid]
    service.related_audio = lambda *_: {'items': [voice(name='VO_TEST_123_Play_01')], 'errors': []}
    assert service.card_audio('CORE_TEST_123')['items'][0]['transcript_dbfid'] == 1
    service.card_voices.clear()
    cards['TEST_123']['effects'][0]['ref'] = 'different'
    assert service.card_audio('CORE_TEST_123')['items'][0]['transcript_dbfid'] == 2
