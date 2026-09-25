from pathlib import Path
from pengpeng.common import audio_category, bundle_locale, guid, localized, references, safe_name


def test_localized_chinese_and_english_fallback():
    record = {'m_name': {'m_locValues': ['English'] + [''] * 11 + ['中文']}}
    assert localized(record, 'm_name') == '中文'
    assert localized(record, 'm_name', 'jajp') == 'English'
    assert localized({}, 'm_name') == ''


def test_safe_windows_export_names():
    assert safe_name('../英雄:语音?.wav') == '_英雄_语音_.wav'
    assert safe_name('CON.wav') == '_CON.wav'
    assert safe_name(' . ') == 'asset'


def test_guid_reference_does_not_capture_text():
    valid = 'a' * 32
    assert guid('Attack.prefab:' + valid) == valid
    assert guid('some:text') == ''
    assert list(references({'effects': [{'path': 'a.prefab:' + valid}]})) == [('effects[0].path', 'a.prefab:' + valid)]


def test_audio_categories_and_locales():
    assert audio_category('VO_HERO_01_ATTACK', 'essential') == '角色语音'
    assert audio_category('amb_forest', '') == '音效'
    assert audio_category('something_unknown', '') == '音效'
    assert bundle_locale('essential_base_zhcn-content-0.unity3d') == 'zhcn'
    assert bundle_locale('music_global-audio-0.unity3d') == 'global'
