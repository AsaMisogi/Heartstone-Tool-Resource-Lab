"""验证升级、精准字幕及主动重试；所有远程响应均由固定样本提供。"""
import io
import hashlib
import json
from unittest.mock import patch

import pytest

from pengpeng.audio_strings import parse_audio_strings
from pengpeng import transcript_sources as sources, transcripts
from pengpeng.service import Service


def provider(identity):
    return next(s for s in sources.DEFAULT_SOURCES if s['id'] == identity)


def voice(identity='v', name='VO_TEST_Play_01', event='Play', **extra):
    return dict(id=identity, name=name, event=f'm_{event}EffectDef.m_SoundSpellPaths[0]',
                kind='voice', locale='zhcn', text='', **extra)


def test_settings_migrate_once_preserve_disabled_and_order(tmp_path):
    old = [{**provider('huiji'), 'enabled': False}, provider('baidu')]
    (tmp_path / 'settings.json').write_text(json.dumps({'transcript_sources': old}))
    service = Service(tmp_path)
    assert [s['id'] for s in service.settings['transcript_sources']] == ['baidu', 'huiji', 'hsdata', 'wikigg']
    assert not service.settings['transcript_sources'][1]['enabled']
    assert not any(s['enabled'] for s in service.settings['transcript_sources'][-2:])
    chosen = list(reversed(service.settings['transcript_sources']))
    service.save_settings(transcript_sources=chosen)
    assert Service(tmp_path).settings['transcript_sources'] == chosen
    assert not any(s['enabled'] for s in sources.migrate_sources([]))


def test_tsv_explicit_keys_and_ambiguous_aliases():
    text = '\ufeffTAG\tTEXT\tCOMMENT\nEVENT\t甲\tVO_TEST_Play_01.wav\nOTHER\t乙\tVO_TEST_Play_01.ogg\nVO_TEST_Play_02.wav\t精确分支\t\n'
    result = parse_audio_strings(text)
    assert 'VO_TEST_PLAY_01' not in result
    assert result['VO_TEST_PLAY_02'] == '精确分支'
    with pytest.raises(ValueError):
        parse_audio_strings('<html>challenge</html>')


def test_hsdata_matches_random_branches_without_event_guessing(tmp_path):
    items = [voice(), voice('v2', 'VO_TEST_Play_02'), voice('v3', 'VO_TEST_Play_03')]
    data = {'clips': {'VO_TEST_PLAY_02': '第二分支'}, 'source': 'https://example.org', 'source_name': 'hsdata'}
    with patch.object(sources, 'fetch_source', return_value=data):
        result = transcripts.supplement(tmp_path, 1, items, providers=[{**provider('hsdata'), 'enabled': True}])
    assert [(r['id'], r['text'], r['match_method']) for r in result['items']] == [('v2', '第二分支', 'audio_key')]


def test_wikigg_identity_definition_list_and_section_boundary():
    html = '<div data-source="id"><div><code>TEST_1</code></div></div><h2>语音[编辑 | 编辑源代码]</h2><dl><dt>召唤</dt><dd>测试登场<br>English</dd><dt>攻击</dt><dd>甲</dd><dd>乙</dd></dl><h2>背景</h2><dt>死亡</dt><dd>无关文字</dd>'
    assert sources.parse_wikigg(html, 'TEST_1') == {'Play': '测试登场'}
    with pytest.raises(ValueError):
        sources.parse_wikigg(html, 'TEST_2')


def test_manual_retry_bypasses_empty_cache_and_cooldown(tmp_path):
    source = provider('wikigg')
    identity = dict(dbfid=1, cardid='TEST_1', name='测试', locale='zhcn')
    def response(body):
        return io.BytesIO(json.dumps({'parse': {'text': {'*': '<div data-source="id">TEST_1</div>' + body}}}).encode())
    with patch.object(sources, 'urlopen', return_value=response('')) as request:
        assert not sources.fetch_source(tmp_path, source, identity)['quotes']
        sources.fetch_source(tmp_path, source, identity)
        assert request.call_count == 1
    key = hashlib.sha256(json.dumps(source, sort_keys=True).encode()).hexdigest()
    with patch.object(sources, '_cooldowns', {key: sources.time.monotonic() + 60}), patch.object(sources, 'urlopen', return_value=response('<h2>台词</h2><dt>攻击</dt><dd>重试成功</dd>')):
        result = sources.fetch_source(tmp_path, source, identity, force=True)
        assert result['quotes'] == {'Attack': '重试成功'}
    with patch.object(sources, 'urlopen', side_effect=OSError('offline')):
        result = sources.fetch_source(tmp_path, source, identity, force=True)
        assert result['stale'] and result['quotes']['Attack'] == '重试成功'


def test_hsdata_shared_cache_retry_and_local_text_preserved(tmp_path):
    payload = b'TAG\tTEXT\tCOMMENT\nVO_TEST_Play_01\tExact text\t\n'
    with patch.object(sources, 'urlopen', return_value=io.BytesIO(payload)) as request:
        sources.fetch_hsdata(tmp_path)
        sources.fetch_hsdata(tmp_path)
        assert request.call_count == 1
    item = voice()
    item['text'] = '本地优先'
    with patch.object(sources, 'fetch_source') as fetch:
        assert not transcripts.supplement(tmp_path, 1, [item], providers=[provider('hsdata')], force=True)['items']
        fetch.assert_not_called()


def test_retry_passes_force_to_all_enabled_sources(tmp_path):
    with patch.object(sources, 'fetch_source', side_effect=OSError('offline')) as fetch:
        transcripts.supplement(tmp_path, 1, [voice()], force=True)
        assert fetch.call_count == 2
        assert [c.args[1]['id'] for c in fetch.call_args_list] == ['baidu', 'huiji']
        assert all(call.kwargs == {'force': True} for call in fetch.call_args_list)


def test_local_credits_are_not_subtitle_tables(tmp_path):
    folder = tmp_path / 'Strings/zhCN'
    folder.mkdir(parents=True)
    (folder / 'CREDITS_2026.txt').write_text('游戏设计\n制作人员名单', encoding='utf8')
    (folder / 'GAMEPLAY_AUDIO.txt').write_text('TAG\tTEXT\tCOMMENT\nVO_TEST_Play_01.wav\t本地台词\t\n', encoding='utf8')
    service = Service(tmp_path / 'work')
    service.root = tmp_path
    service._load_strings()
    assert service.strings['zhcn']['VO_TEST_PLAY_01'] == '本地台词'
