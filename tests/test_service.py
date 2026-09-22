import io
import json
from pathlib import Path
from types import SimpleNamespace
import wave
import pytest
import soundfile as sf
import numpy as np
from pengpeng.service import Service
from pengpeng.storage import Store


def test_audio_decodes_every_subsample_as_real_pcm_wav(tmp_path):
    stream = io.BytesIO()
    sf.write(stream, np.zeros((800, 2)), 8000, format='WAV', subtype='FLOAT')
    audio = SimpleNamespace(m_Name='中文音效', samples={'one.wav': stream.getvalue(), 'two.wav': stream.getvalue()})
    obj = SimpleNamespace(assets_file=SimpleNamespace(name='CAB-test'), path_id=2, read=lambda: audio)
    s = Service(tmp_path)
    s.cache = tmp_path / 'cache'
    result = s._audio(obj)
    assert len(result) == 2
    for sample in result:
        with wave.open(sample['path']) as wav:
            assert wav.getnchannels() == 2
            assert wav.getframerate() == 8000
            assert wav.getsampwidth() == 2
            assert wav.getnframes() == 800
    assert s._audio(obj) == result


def test_export_rejects_game_directory_and_external_file(tmp_path):
    s = Service(tmp_path / 'work')
    s.root = tmp_path / 'game'
    s.cache = tmp_path / 'work/cache/version'
    s.settings['export_path'] = str(s.root / 'exports')
    with pytest.raises(ValueError, match='安装目录'):
        s.export()
    s.settings['export_path'] = str(tmp_path / 'exports')
    with pytest.raises(ValueError, match='当前索引'):
        s.export(image_path=str(tmp_path / 'private.txt'))


def test_search_pagination_and_quotes_are_data(tmp_path):
    s = Service(tmp_path)
    s.store = Store(tmp_path / 'index.sqlite3')
    s.store.db.executemany('INSERT INTO cards VALUES (?,?,?,?,?,?,?)', [
        ('A', "Hero's text", 'en', 0, '', '{}', None),
        ('B', '英雄', 'hero', 1, '', '{}', None)])
    assert s.list_cards(query="'")['total'] == 1
    assert s.list_cards(query="' OR 1=1 --")['total'] == 0
    assert s.list_cards(hero=True)['items'][0]['id'] == 'B'
    s.favorite('card', 'B')
    assert s.list_cards(favorites=True)['total'] == 0
    assert s.list_cards(hero=True, favorites=True)['total'] == 1
    s.favorite('card', 'B', False)
    assert s.list_cards(hero=True, favorites=True)['total'] == 0
    s.store.close()


def test_empty_audio_is_error_not_success(tmp_path):
    s = Service(tmp_path)
    s.cache = tmp_path / 'cache'
    obj = SimpleNamespace(assets_file=SimpleNamespace(name='empty'), path_id=1,
                          read=lambda: SimpleNamespace(m_Name='empty', samples={}))
    with pytest.raises(ValueError, match='没有返回采样'):
        s._audio(obj)


def test_index_resume_and_changed_corrupt_bundle(tmp_path):
    """增量索引必须跳过已完成包，但不能继续展示损坏更新包的旧资源。"""
    source = tmp_path / 'sound.unity3d'
    source.write_bytes(b'first version')
    obj = SimpleNamespace(type=SimpleNamespace(name='AudioClip'), path_id=1,
        assets_file=SimpleNamespace(name='CAB-test'),
        read_typetree=lambda: {'m_Name': 'VO_Test', 'm_Length': 2.0})
    calls = []
    def load(name):
        calls.append(name)
        return SimpleNamespace(container={}, objects=[obj])
    s = Service(tmp_path / 'work')
    s.store = Store(tmp_path / 'index.sqlite3')
    s.reader = SimpleNamespace(source_paths=lambda: {'sound.unity3d': source}, load=load, cabs={})
    list(s.scan())
    list(s.scan())
    assert len(calls) == 1
    assert s.list_assets()['total'] == 1
    source.write_bytes(b'new corrupted version')
    def broken(_):
        raise ValueError('corrupt test bundle')
    s.reader.load = broken
    list(s.scan())
    assert s.list_assets()['total'] == 0
    assert 'corrupt' in s.store.db.execute('SELECT error FROM bundles').fetchone()[0]
    s.reader.load = load
    list(s.scan())
    assert s.list_assets()['total'] == 1
    s.store.close()


def test_partial_audio_graph_does_not_hold_database_lock(tmp_path):
    s = Service(tmp_path)
    s.store = Store(tmp_path / 'index.sqlite3')
    obj = SimpleNamespace(type=SimpleNamespace(name='AudioClip'), path_id=2,
        assets_file=SimpleNamespace(name='CAB-test'),
        read=lambda: SimpleNamespace(m_Name='VO_Test', m_Length=1))
    def walk(*args, **kwargs):
        yield obj, None
        assert not s.store.db.in_transaction, 'Unity 遍历期间不应占用 SQLite 写锁'
        raise ValueError('资源引用超过节点上限，未完成遍历')
    s.reader = SimpleNamespace(walk=walk, cabs={'cab-test': 'test.unity3d'})
    result = s._collect_audio(None, 'zhcn')
    assert len(result['items']) == 1
    assert '未完成' in result['errors'][0]
    assert not s.store.db.in_transaction
    s.store.close()


def test_emote_string_key_does_not_override_card_cache_key(tmp_path):
    s = Service(tmp_path)
    s.card = lambda *_: {'effects': [{'ref': 'emote.prefab', 'name': 'Emote', 'field': 'm_EmoteDefs[0]'}],
                         'definition': {'m_EmoteDefs': [{'m_emoteGameStringKey': 'HELLO'}]}}
    calls = []
    def related(*args):
        calls.append(1)
        return {'items': [{'id': 'sound', 'text': ''}], 'errors': []}
    s.related_audio = related
    s.strings = {'zhcn': {'HELLO': '你好'}}
    assert s.card_audio('HERO_TEST')['items'][0]['text'] == '你好'
    s.card_audio('HERO_TEST')
    assert len(calls) == 1
