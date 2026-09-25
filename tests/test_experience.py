"""0.6 回归：更新边界、近白裁切、场景角色匹配与持久化设置。"""
import io
import json
from unittest.mock import patch
from unittest.mock import Mock
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest
from PIL import Image, ImageDraw

from pengpeng.images import portrait_preview
from pengpeng.npc_audio import npc_audio
from pengpeng.service import Service
from pengpeng.storage import Store
from pengpeng.updates import check_update, version_tuple, RELEASES_URL


def test_version_comparison_is_numeric():
    assert version_tuple('v0.10.0') > version_tuple('0.9.9')
    for invalid in ('v0.6.0-rc1', 'latest', 'v1.0', 'https://other.example'):
        with pytest.raises(ValueError):
            version_tuple(invalid)


def test_update_only_opens_constructed_repository_link():
    payload = {'tag_name': 'v99.0.0', 'html_url': 'https://other.example', 'draft': False, 'prerelease': False}
    with patch('pengpeng.updates.urlopen', return_value=io.BytesIO(json.dumps(payload).encode())) as request:
        result = check_update()
    assert result['available']
    assert result['url'] == RELEASES_URL + '/tag/v99.0.0'
    assert request.call_args.kwargs['timeout'] == 10


@pytest.mark.parametrize('code', [404, 403, 429, 500])
def test_update_http_errors_do_not_report_false_success(code):
    with patch('pengpeng.updates.urlopen', side_effect=HTTPError('https://api.github.com', code, '', {}, None)):
        if code == 404:
            assert '尚未' in check_update()['message']
        else:
            with pytest.raises((ValueError, HTTPError)):
                check_update()


def test_portrait_preserves_smooth_colored_sky_and_upper_subject():
    image = Image.new('RGB', (128, 128), '#b0d6df')
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 70, 127, 127), fill='#625141')
    draw.rectangle((52, 10, 76, 40), fill='#ff7030')
    result = portrait_preview(image)
    assert result.size == (128, 128)
    assert result.getpixel((60, 20)) == (255, 112, 48)


def test_adventure_speaker_boundary_excludes_other_bosses(tmp_path):
    service = Service(tmp_path)
    service.store = Store(tmp_path / 'index.db')
    service.store.set_meta('npc_audio_index_v1_zhcn', True)
    names = ['VO_ICC08_LichKing_Male_Human_Mage_01',
             'VO_ICC08_LichKing_Male_Human_EmoteResponse_01',
             'VO_ICC10_Deathwhisper_Female_Lich_EmoteResponse_01',
             'VO_ICC09_Saurfang_Male_Orc_LichKing_01']
    with service.store.db:
        for index, name in enumerate(names):
            service.store.db.execute('INSERT INTO assets VALUES (?,?,?,?,?,?,?,?,?)',
                (str(index), 'test', str(index), '', name, 'AudioClip', 'voice', 'zhcn', 1))
    result = npc_audio(service, {'id': 'HERO_11', 'record': {}, 'definition': {}}, 'zhcn')
    assert len(result['items']) == 2
    assert result['items'][0]['adventure']
    assert '面对法师' in result['items'][0]['event']
    assert '回应' in result['items'][1]['event']
    service.store.close()


def test_voice_page_and_update_preferences_persist(tmp_path):
    service = Service(tmp_path)
    service.save_settings(voice_page_size=48, auto_check_updates=False)
    restored = Service(tmp_path)
    assert restored.settings['voice_page_size'] == 48
    assert restored.settings['auto_check_updates'] is False
    for invalid in (0, 10000, True, '24'):
        with pytest.raises(ValueError):
            service.save_settings(voice_page_size=invalid)


def test_voice_query_survives_restart_and_resource_change_invalidates(tmp_path):
    root = tmp_path / 'game'
    root.mkdir()
    resource = root / 'resource.unity3d'
    resource.write_bytes(b'old')
    work = tmp_path / 'work'
    def make_service():
        service = Service(work)
        service.root = root
        service.cache = work / 'cache/test'
        service.store = Store(service.cache / 'index.db')
        service.reader = SimpleNamespace(source_paths=lambda: {'resource.unity3d': resource})
        service.audio_installed = Mock(return_value=True)
        service.card = Mock(return_value={'id': 'TEST', 'record': {}, 'definition': {}, 'effects': []})
        return service
    first = make_service()
    expected = first.card_audio('TEST')
    first.store.close()
    second = make_service()
    assert second.card_audio('TEST') == expected
    second.card.assert_not_called()
    second.store.close()
    resource.write_bytes(b'new version')
    third = make_service()
    third.card_audio('TEST')
    third.card.assert_called_once()
    third.store.close()
