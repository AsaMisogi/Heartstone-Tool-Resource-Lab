"""交互修复的后端行为回归；离线运行，不依赖本机游戏或公开站点。"""
import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from pengpeng.common import audio_category
from pengpeng.transcript_sources import DEFAULT_SOURCES
HUIJI = [s for s in DEFAULT_SOURCES if s["id"] == "huiji"]
from pengpeng.service import Service, publish_cache
from pengpeng.transcripts import parse_quotes, supplement, fetch_quotes
from pengpeng.unity import UnityReader


def test_sound_bundle_does_not_turn_music_or_sfx_into_voice():
    bundle = 'soundotherminion_base_zhcn-audio-0.unity3d'
    assert audio_category('VO_EX1_116_Play_01', bundle) == '角色语音'
    assert audio_category('EX1_116_MusicStinger', bundle) == '短音乐 / 登场曲'
    assert audio_category('Minion_Impact', bundle) == '音效'
    assert audio_category('ui_button', 'playsounds_base_zhcn-audio-0.unity3d') == '音效'


def test_settings_persist_pagination_mode_and_validate_capacity(tmp_path):
    s = Service(tmp_path)
    assert s.settings['infinite_scroll'] is False
    s.save_settings(infinite_scroll=True, page_size=48)
    assert Service(tmp_path).settings['page_size'] == 48
    assert Service(tmp_path).settings['infinite_scroll'] is True
    with pytest.raises(ValueError):
        s.save_settings(page_size=100000)


def test_local_text_aliases_and_non_audio_files(tmp_path):
    folder = tmp_path / 'Strings/zhCN'
    folder.mkdir(parents=True)
    (folder / 'GLUE.txt').write_text('TAG\tTEXT\tCOMMENT\nEVENT\t测试台词\tVO_TEST_Play_01.wav\n', 'utf-8')
    s = Service(tmp_path / 'work')
    s.root = tmp_path
    s._load_strings()
    assert s.strings['zhcn']['VO_TEST_PLAY_01'] == '测试台词'


def test_builtin_pointer_never_scans_bundle_dependencies():
    reader = UnityReader.__new__(UnityReader)
    reader.load = lambda _: pytest.fail('内建资源不能扫描依赖包')
    obj = SimpleNamespace(assets_file=SimpleNamespace(externals=[SimpleNamespace(path='unity default resources')]))
    with pytest.raises(FileNotFoundError, match='内建'):
        reader.pointer(obj, {'m_FileID': 1, 'm_PathID': 1})


def test_particle_visual_walk_does_not_follow_mesh_or_collision_pointers():
    reader = UnityReader.__new__(UnityReader)
    reader.pointer = lambda *_: pytest.fail('参数预览不应读取网格依赖')
    tree = {'m_Mesh': {'m_FileID': 1, 'm_PathID': 20}}
    obj = SimpleNamespace(assets_file=SimpleNamespace(name='test'), path_id=1,
                          type=SimpleNamespace(name='ParticleSystem'), read_typetree=lambda: tree)
    assert list(reader.walk(obj, include_visual=True)) == [(obj, tree)]


def test_wiki_parser_only_accepts_unique_supported_quotes():
    text = '|台词=*登场\n{{quote|测试登场<br>Test}}\n*攻击\n{{quote|攻击一}}\n{{quote|攻击二}}\n*死亡\n{{quote|[[其他条目]]}}\n|背景=其他'
    assert parse_quotes(text) == {'Play': '测试登场'}
    assert parse_quotes('|背景=*登场\n{{quote|无关文本}}') == {}


def clip(identity, event='Play', text='', locale='zhcn'):
    return {'id': identity, 'kind': 'voice', 'locale': locale, 'text': text,
            'event': f'm_{event}EffectDef.m_SoundSpellPaths[0]'}


def test_supplement_preserves_local_text_and_rejects_random_branches(tmp_path):
    source = {'quotes': {'Play': '登场', 'Attack': '攻击', 'Death': '死亡'},
              'source': 'https://hearthstone.huijiwiki.com/wiki/Card/559', 'source_name': '维基'}
    items = [clip('p', text='本地'), clip('a1', 'Attack'), clip('a2', 'Attack'), clip('d', 'Death')]
    with patch('pengpeng.transcripts.fetch_quotes', return_value=source):
        result = supplement(tmp_path, 559, items, providers=HUIJI)
    assert [r['id'] for r in result['items']] == ['d']
    assert items[0]['text'] == '本地'
    assert not supplement(tmp_path, 559, items, 'enus')['items']


def test_quote_cache_and_offline_fallback(tmp_path):
    payload = {'parse': {'wikitext': {'*': '|台词=*登场\n{{quote|测试文字<br>Test}}\n|背景='}}}
    with patch('pengpeng.transcripts.urlopen', return_value=io.BytesIO(json.dumps(payload).encode())) as fetch:
        first = fetch_quotes(tmp_path, 559)
        assert fetch_quotes(tmp_path, 559) == first
        assert fetch.call_count == 1
    with patch('pengpeng.transcripts.time.time', return_value=first['fetched_at'] + 31 * 86400), \
            patch('pengpeng.transcripts.urlopen', side_effect=OSError('离线')):
        assert fetch_quotes(tmp_path, 559)['stale'] is True


def test_effect_result_is_reused_without_reading_unity_again(tmp_path):
    s = Service(tmp_path)
    root = SimpleNamespace(read_typetree=lambda: {'m_Name': 'test'})
    calls = []
    def resolve(*_):
        calls.append(True)
        return root, '', ''
    s.reader = SimpleNamespace(resolve=resolve, walk=lambda *_, **__: [])
    first = s.effect(reference='test')
    assert s.effect(reference='test') is first
    assert len(calls) == 1


def test_cache_publication_preserves_completed_other_process_result(tmp_path):
    target = tmp_path / 'clip.wav'
    first, second = tmp_path / 'clip.1.tmp', tmp_path / 'clip.2.tmp'
    first.write_bytes(b'complete audio')
    second.write_bytes(b'complete audio')
    publish_cache(first, target)
    publish_cache(second, target)
    assert target.read_bytes() == b'complete audio'
    assert not first.exists() and not second.exists()
