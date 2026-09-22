"""筛选持久化与混音行为回归；使用合成声音，不依赖游戏安装和网络。"""
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
import json
from unittest.mock import Mock

from pengpeng.audio_mix import mix_samples
from pengpeng.preferences import validate_views
from pengpeng.service import Service
from pengpeng.storage import Store
from pengpeng.card_details import card_metadata


def test_preferences_survive_restart_and_reset_only_one_view(tmp_path):
    service = Service(tmp_path)
    service.save_settings(view_state={'cards': {'query': '火车王', 'filters': {'cost': '0'},
                                               'order': {'sort': 'id', 'descending': True}},
                                      'heroes': {'query': '吉安娜'}},
                          ui_scale=1.25, font_scale=1.4, mix_voice_export=True)
    restored = Service(tmp_path)
    assert restored.settings['view_state']['cards']['filters']['cost'] == '0'
    assert restored.settings['view_state']['cards']['order']['descending']
    assert restored.settings['ui_scale'] == 1.25
    assert restored.settings['mix_voice_export'] is True
    views = restored.settings['view_state']
    views['cards'] = {}
    restored.save_settings(view_state=views)
    assert Service(tmp_path).settings['view_state']['heroes']['query'] == '吉安娜'
    assert Service(tmp_path).settings['view_state']['cards']['query'] == ''


@pytest.mark.parametrize('value', [0, 10, True, float('nan'), float('inf'), '1'])
def test_scale_rejects_invalid_values_without_overwriting(tmp_path, value):
    service = Service(tmp_path)
    with pytest.raises(ValueError):
        service.save_settings(ui_scale=value)
    assert service.settings['ui_scale'] == 1


def test_view_schema_discards_transient_and_unknown_fields():
    saved = validate_views({'cards': {'selected': ['x'], 'filters': {'cost': 0, 'sql': 'DROP'},
                                      'order': {'sort': 'bad'}, 'locale': 'bad'}, 'exports': {}})
    assert set(saved) == {'cards'}
    assert saved['cards']['filters'] == {'cost': '0'}
    assert saved['cards']['locale'] == 'zhcn'
    assert saved['cards']['order']['sort'] == 'default'


def test_mix_keeps_tail_deduplicates_and_prevents_clipping(tmp_path):
    main, tail, output = (tmp_path / p for p in ('main.wav', 'tail.wav', 'mix.wav'))
    sf.write(main, np.ones((8000, 1)) * 0.8, 8000, subtype='FLOAT')
    sf.write(tail, np.ones((32000, 2)) * 0.4, 16000, subtype='FLOAT')
    result = mix_samples(main, [str(tail), str(tail)], output)
    data, rate = sf.read(output, always_2d=True)
    assert rate == 16000 and data.shape == (32000, 2)
    assert len(result['tracks']) == 2
    assert result['gain'] == pytest.approx(0.98 / 1.2)
    assert np.max(np.abs(data)) <= 0.981
    assert data[-1, 0] > 0.3  # 主语音结束后保留配音尾声。


def test_companion_cap_does_not_truncate_longer_main_voice(tmp_path):
    main, tail, output = (tmp_path / p for p in ('main.wav', 'tail.wav', 'mix.wav'))
    sf.write(main, np.ones(17 * 8000) * 0.1, 8000)
    sf.write(tail, np.ones(20 * 8000) * 0.2, 8000)
    mix_samples(main, [tail], output)
    data, _ = sf.read(output)
    assert len(data) == 17 * 8000
    assert data[14 * 8000] == pytest.approx(0.3, abs=0.001)
    assert data[16 * 8000] == pytest.approx(0.1, abs=0.001)


def test_service_mixes_only_selected_voice_and_keeps_raw_export(tmp_path):
    service = Service(tmp_path / 'work')
    service.root = tmp_path / 'game'
    service.cache = service.workspace / 'cache' / 'test'
    service.cache.mkdir(parents=True)
    service.store = Store(service.workspace / 'index.db')
    service.store.db.execute("INSERT INTO cards VALUES ('CARD','卡牌','',0,'','{}',NULL)")
    main, tail = (service.cache / p for p in ('voice.wav', 'sound.wav'))
    sf.write(main, np.ones(8000) * 0.1, 8000)
    sf.write(tail, np.ones(16000) * 0.2, 8000)
    service.audio = Mock(side_effect=lambda assetid: {'samples': [{'path': str(main if assetid == 'v' else tail)}]})
    service.card_audio = Mock(return_value={'items': [
        {'id': 'v', 'kind': 'voice', 'group': 'play'}, {'id': 's', 'kind': 'sound', 'group': 'play'},
        {'id': 'other', 'kind': 'sound', 'group': 'death'}], 'errors': []})
    service.general_audio = Mock(return_value={'items': [{'id': 's'}], 'errors': []})
    raw = service.export(assetids=['v'], context_cardid='CARD')
    assert Path(raw['files'][0]).read_bytes() == main.read_bytes()
    mixed = service.export(assetids=['v'], context_cardid='CARD', mix_voice=True,
                           paired_audio=True, general_audio=True)
    assert len(mixed['files']) == 1 and not mixed['errors']
    assert len(mixed['media'][0]['mix']['tracks']) == 2
    assert 'other' not in [call.kwargs.get('assetid') for call in service.audio.call_args_list]
    service.store.close()


def test_metadata_relations_are_bidirectional_and_core_has_no_dust(tmp_path):
    store = Store(tmp_path / 'index.db')
    store.db.executescript('''
      CREATE TABLE card_details (id TEXT, data TEXT);
      CREATE TABLE card_filters (id TEXT, rarity INT, card_type INT, cost INT,
        collectible INT, standard INT, wild INT, bg INT);
      CREATE TABLE card_classes (id TEXT, class_id INT);
      CREATE TABLE card_sets (id TEXT, set_id INT);
      CREATE TABLE card_relations (id TEXT, related TEXT);
      INSERT INTO cards VALUES ('A','来源','',0,'','{}',NULL),('B','衍生','',0,'','{}',NULL);
      INSERT INTO card_filters VALUES ('A',5,4,0,1,1,1,0),('B',1,4,0,0,0,0,0);
      INSERT INTO card_sets VALUES ('A',1637);
      INSERT INTO card_classes VALUES ('A',4),('A',7);
      INSERT INTO card_relations VALUES ('A','B'),('B','A');
    ''')
    data = {'attack': 0, 'health': 1, 'durability': None, 'races': [14, 24],
            'crafting_event': 203, 'golden_crafting_event': -1}
    store.db.executemany('INSERT INTO card_details VALUES (?,?)', [(c, json.dumps(data)) for c in ('A', 'B')])
    metadata, related = card_metadata(store, 'A', 'zhcn')
    assert metadata['attack'] == 0 and metadata['cost'] == 0
    assert metadata['races'] == ['鱼人', '龙']
    assert metadata['classes'] == ['法师', '潜行者']
    assert metadata['dust'] == {'normal': None, 'golden': None}
    assert related == [{'id': 'B', 'name': '衍生'}]
    assert card_metadata(store, 'B', 'zhcn')[1] == [{'id': 'A', 'name': '来源'}]
    store.close()


def test_catalog_display_preferences():
    """新布局兼容旧设置，非法模式/尺寸回退且两个图鉴独立保存。"""
    saved = validate_views({'cards': {'display': {'mode': 'list', 'size': 180}},
                            'heroes': {'display': {'mode': 'grid', 'size': 300}}})
    assert saved['cards']['display'] == {'mode': 'list', 'size': 180}
    assert saved['heroes']['display'] == {'mode': 'grid', 'size': 300}
    for display in (None, [], {'mode': 'bad', 'size': 'large'}, {'size': True}):
        assert validate_views({'cards': {'display': display}})['cards']['display'] == {'mode': 'grid', 'size': 220}
    assert validate_views({'cards': {}})['cards']['display']['size'] == 220
    for size, expected in ((0, 180), (10000, 300)):
        assert validate_views({'cards': {'display': {'size': size}}})['cards']['display']['size'] == expected
