"""本轮需求的行为回归：命名、原始像素、筛选、语言与联网卡图缓存。"""
import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image, ImageDraw
import pytest

from pengpeng.service import Service
from pengpeng.storage import Store


def test_first_run_has_no_machine_specific_directory(tmp_path):
    service = Service(tmp_path)
    assert service.status()['settings']['game_path'] == ''
    assert service.settings['paired_audio'] is False
    service.save_settings(game_path='D:/Hearthstone', paired_audio=True)
    restored = Service(tmp_path)
    assert restored.settings['paired_audio'] is True
    assert restored.settings['game_path'] == 'D:/Hearthstone'


def test_thumbnail_trims_white_padding_but_export_preserves_original(tmp_path):
    original = Image.new('RGBA', (1024, 1024), 'white')
    ImageDraw.Draw(original).rectangle((160, 0, 863, 1023), fill='#245678')
    obj = SimpleNamespace(assets_file=SimpleNamespace(name='CAB'), path_id=4,
                          read=lambda: SimpleNamespace(image=original.copy(), m_Name='中文原画'))
    service = Service(tmp_path / 'work')
    service.root = tmp_path / 'game'
    service.cache = tmp_path / 'work/cache/version'
    thumb, full = service._image(obj, True), service._image(obj)
    assert thumb['width'] < thumb['height']
    assert (full['width'], full['height']) == original.size
    service.store = Store(tmp_path / 'index.db')
    service.store.db.execute('INSERT INTO cards VALUES (?,?,?,?,?,?,?)',
        ('HERO_TEST', '测试英雄', '', 1, '', '{}', None))
    result = service.export(image_path=full['path'], context_cardid='HERO_TEST', label='普通原画')
    assert Path(result['folder']).name.startswith('测试英雄_HERO_TEST_zhcn_')
    assert '测试英雄' in Path(result['files'][0]).name
    assert Path(result['files'][0]).read_bytes() == Path(full['path']).read_bytes()
    assert result['media'][0]['width'] == 1024
    service.store.close()


def test_audio_presence_uses_files_not_locale_manifest(tmp_path):
    service = Service(tmp_path)
    service.reader = SimpleNamespace(locales={'enus': {}}, source_paths=lambda: {
        'soundspell_base_zhcn-audio-0.unity3d': tmp_path / 'chinese',
        'cardasset_enus-texture-0.unity3d': tmp_path / 'text',
    })
    assert service.audio_installed('zhcn')
    assert not service.audio_installed('enus')


def test_filters_combine_zero_cost_dual_class_and_hero_group(tmp_path):
    service = Service(tmp_path)
    service.store = Store(tmp_path / 'index.db')
    db = service.store.db
    db.executescript('''
        CREATE TABLE card_filters (id TEXT, cost INT, rarity INT, card_type INT,
          hero_group TEXT, bg INT, collectible INT, standard INT, wild INT);
        CREATE TABLE card_sets (id TEXT, set_id INT);
        CREATE TABLE card_classes (id TEXT, class_id INT);
        INSERT INTO cards VALUES ('ZERO','零费','','0','','{}',NULL);
        INSERT INTO cards VALUES ('TEN','十费','','0','','{}',NULL);
        INSERT INTO cards VALUES ('BOSS','敌人','','1','','{}',NULL);
        INSERT INTO card_filters VALUES ('ZERO',0,3,5,'12',0,1,1,1);
        INSERT INTO card_filters VALUES ('TEN',12,5,4,'12',1,0,0,0);
        INSERT INTO card_filters VALUES ('BOSS',0,0,3,'enemy',0,0,0,0);
        INSERT INTO card_classes VALUES ('ZERO',4),('ZERO',7),('TEN',12);
        INSERT INTO card_sets VALUES ('ZERO',1001),('TEN',1453);
    ''')
    for cls in ('4', '7'):
        result = service.list_cards(filters={'cost': '0', 'class': cls, 'type': '5', 'set': '1001'})
        assert [x['id'] for x in result['items']] == ['ZERO']
    assert service.list_cards(filters={'cost': '10+'})['items'][0]['id'] == 'TEN'
    assert service.list_cards(hero=True, filters={'hero_group': 'enemy', 'battlegrounds': 'exclude'})['total'] == 1
    assert service.list_cards(filters={'rarity': "3' OR 1=1"})['total'] == 0
    service.store.close()


def test_full_render_validates_response_and_reuses_cache(tmp_path):
    service = Service(tmp_path)
    service.cache = tmp_path / 'cache'
    payload = io.BytesIO()
    Image.new('RGBA', (512, 768)).save(payload, 'PNG')
    with patch('urllib.request.urlopen', return_value=io.BytesIO(payload.getvalue())) as fetch:
        first = service.card_render('EX1_116')
        assert service.card_render('EX1_116') == first
        assert fetch.call_count == 1
        assert first['width'] == 512
    with pytest.raises(ValueError, match='无效'):
        service.card_render('../outside')
    with patch('urllib.request.urlopen', return_value=io.BytesIO(b'<html>error</html>')):
        with pytest.raises(ValueError, match='暂不可用'):
            service.card_render('MISSING')
    assert not (service.cache / 'renders/zhcn/MISSING.png').exists()
