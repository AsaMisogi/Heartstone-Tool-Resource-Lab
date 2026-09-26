"""语言包升级：复现真实英文表头、单文件故障隔离和持久偏好。"""
from pathlib import Path
from types import SimpleNamespace

import pytest

from pengpeng import __version__
from pengpeng.audio_strings import parse_audio_strings
from pengpeng.service import Service


def test_english_comments_and_multiline_text():
    text = '\ufeff# NOTE: keys are used as IDs\n#\n\nTAG\tTEXT\tCOMMENT\nVOICE\t"Hello\n# still dialogue"\t\n'
    assert parse_audio_strings(text) == {'VOICE': 'Hello\n# still dialogue'}
    with pytest.raises(ValueError, match='TAG/TEXT'):
        parse_audio_strings('# comment\nnot a table')


def test_bad_string_file_does_not_discard_other_languages(tmp_path):
    service = Service(tmp_path / 'work')
    service.root = tmp_path / 'game'
    for locale in ('enUS', 'zhCN'):
        folder = service.root / 'Strings' / locale
        folder.mkdir(parents=True)
        (folder / 'AUDIO.txt').write_text('TAG\tTEXT\nVOICE\t' + locale, 'utf8')
    bad = service.root / 'Strings/enUS/BAD.txt'
    bad.write_text('invalid table', 'utf8')
    service._load_strings()
    assert service.strings['enus']['VOICE'] == 'enUS'
    assert service.strings['zhcn']['VOICE'] == 'zhCN'
    assert len(service.string_warnings) == 1
    assert 'enUS/BAD.txt'.lower() in service.string_warnings[0].lower()
    bad.unlink()
    service._load_strings()
    assert not service.string_warnings


def test_language_preferences_survive_restart(tmp_path):
    service = Service(tmp_path)
    assert service.settings['sync_voice_locale'] is True
    assert service.settings['show_other_voice_locales'] is False
    service.save_settings(locale='zhcn', voice_locale='enus', sync_voice_locale=False,
                          show_other_voice_locales=True)
    restored = Service(tmp_path)
    assert restored.settings['locale'] == 'zhcn'
    assert restored.settings['voice_locale'] == 'enus'
    assert restored.settings['sync_voice_locale'] is False
    assert restored.settings['show_other_voice_locales'] is True
    for values in ({'voice_locale': 'bad'}, {'sync_voice_locale': 'true'},
                   {'show_other_voice_locales': 1}):
        with pytest.raises(ValueError):
            restored.save_settings(**values)


def test_failed_initialize_is_not_ready(tmp_path, monkeypatch):
    service = Service(tmp_path)
    closed = []
    def fail(*args):
        service.store = SimpleNamespace(close=lambda: closed.append(True))
        service.reader = object()
        raise ValueError('partial connection')
    monkeypatch.setattr(service, '_initialize', fail)
    with pytest.raises(ValueError):
        service.initialize()
    assert closed and service.reader is None
    assert service.status()['ready'] is False
    assert service.status()['app_version'] == __version__


def test_english_audiofile_alias_and_ambiguity():
    text = ('TAG\tTEXT\tCOMMENT\tAUDIOFILE\n'
            'EMOTE\tHello\t\tVO_HERO_Greetings.wav\n'
            'MIRROR\tYou again\t\tVO_MIRROR\n'
            'ONE\tFirst\t\tVO_CONFLICT.wav\n'
            'TWO\tSecond\t\tVO_CONFLICT.wav\n')
    values = parse_audio_strings(text)
    assert values['VO_HERO_GREETINGS'] == 'Hello'
    assert values['VO_MIRROR'] == 'You again'
    assert 'VO_CONFLICT' not in values
