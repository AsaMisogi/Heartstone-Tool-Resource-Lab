"""模型切换、首次离线启动与兼容 API 的回归；不依赖外部服务。"""
from unittest.mock import Mock

import pytest
import requests

from pengpeng import speech, speech_api
from pengpeng.service import Service
from pengpeng.speech_config import DEFAULT_CONFIG, validate_config
from test_speech import wav


def model_folder(root, name):
    folder = root / name
    (folder / 'am').mkdir(parents=True)
    (folder / 'conf').mkdir()
    (folder / 'am/final.mdl').write_bytes(b'model')
    (folder / 'conf/model.conf').write_text('config')
    return folder


def test_frozen_first_run_uses_bundle_without_network(tmp_path, monkeypatch):
    model = model_folder(tmp_path / 'bundle', speech.MODELS['zhcn'])
    monkeypatch.setattr(speech, 'bundled_root', lambda: model.parent)
    monkeypatch.setattr(speech.sys, 'frozen', True, raising=False)
    network = Mock(side_effect=AssertionError('不得联网'))
    monkeypatch.setattr(speech, 'urlopen', network)
    assert speech.ensure_model(tmp_path / 'empty-workspace', 'zhcn') == model
    with pytest.raises(ValueError, match='完整解压'):
        speech.ensure_model(tmp_path, 'enus')
    network.assert_not_called()


def test_custom_model_switch_and_in_place_update_invalidates_cache(tmp_path, monkeypatch):
    import vosk
    first = model_folder(tmp_path, 'first')
    second = model_folder(tmp_path, 'second')
    monkeypatch.setattr(vosk, 'Model', Mock())
    recognizer = Mock()
    recognizer.AcceptWaveform.return_value = False
    recognizer.FinalResult.return_value = '{"text":"测试"}'
    factory = Mock(return_value=recognizer)
    monkeypatch.setattr(vosk, 'KaldiRecognizer', factory)
    engine = speech.Recognizer(tmp_path)
    paths = [wav(tmp_path)]
    config = {**DEFAULT_CONFIG, 'provider': 'local', 'zhcn_path': str(first)}
    assert not engine.recognize(paths, config=config)['cached']
    assert engine.recognize(paths, config=config)['cached']
    config['zhcn_path'] = str(second)
    assert not engine.recognize(paths, config=config)['cached']
    (second / 'am/final.mdl').write_bytes(b'new model version')
    assert not engine.recognize(paths, config=config)['cached']
    assert factory.call_count == 3


def test_settings_key_survives_workspace_migration(tmp_path):
    service = Service(tmp_path)
    config = {**DEFAULT_CONFIG, 'provider': 'api', 'api_url': 'https://example.com/v1/audio/transcriptions', 'api_model': 'whisper-1'}
    service.save_settings(speech_config=config, speech_api_key='test-key')
    assert Service(tmp_path).settings['speech_api_key'] == 'test-key'
    before = service.settings_path.read_bytes()
    with pytest.raises(ValueError):
        service.save_settings(speech_config={**config, 'api_model': ''})
    assert service.settings_path.read_bytes() == before
    service.save_settings(speech_api_key='')
    assert Service(tmp_path).settings['speech_api_key'] == ''


@pytest.mark.parametrize('url', ['file:///x', 'https://user:pass@example.com/x', 'http://example.com/x'])
def test_invalid_api_address(url):
    with pytest.raises(ValueError):
        validate_config({**DEFAULT_CONFIG, 'provider': 'api', 'api_url': url, 'api_model': 'test'})


def test_api_protocol_errors_and_no_redirect(tmp_path, monkeypatch):
    Service(tmp_path).save_settings(speech_api_key='test-key')
    response = Mock(status_code=200)
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.iter_content.return_value = [b'{"text":"hello"}']
    post = Mock(return_value=response)
    monkeypatch.setattr(speech_api.requests, 'post', post)
    config = {'api_url': 'https://example.com/v1/audio/transcriptions', 'api_model': 'test'}
    assert speech_api.transcribe(tmp_path, config, b'wav', 'enus') == 'hello'
    assert post.call_args.kwargs['allow_redirects'] is False
    assert post.call_args.kwargs['data']['model'] == 'test'
    assert post.call_args.kwargs['headers']['Authorization'] == 'Bearer test-key'
    response.status_code = 401
    with pytest.raises(ValueError, match='HTTP 401'):
        speech_api.transcribe(tmp_path, config, b'wav', 'enus')
    post.side_effect = requests.Timeout('private remote detail')
    with pytest.raises(ValueError, match='连接失败') as error:
        speech_api.transcribe(tmp_path, config, b'wav', 'enus')
    assert 'private' not in str(error.value)


def test_online_cache_separates_model_and_endpoint(tmp_path, monkeypatch):
    transcribe = Mock(return_value='远端台词')
    monkeypatch.setattr(speech_api, 'transcribe', transcribe)
    engine = speech.Recognizer(tmp_path)
    config = {**DEFAULT_CONFIG, 'provider': 'api', 'api_url': 'https://example.com/transcribe', 'api_model': 'one'}
    paths = [wav(tmp_path)]
    assert engine.recognize(paths, config=config)['text'] == '远端台词'
    assert engine.recognize(paths, config=config)['cached']
    assert not engine.recognize(paths, force=True, config=config)['cached']
    config['api_model'] = 'two'
    assert not engine.recognize(paths, config=config)['cached']
    config['api_url'] = 'https://other.example/transcribe'
    assert not engine.recognize(paths, config=config)['cached']
    assert transcribe.call_count == 4


def test_status_detects_native_load_failure(tmp_path, monkeypatch):
    import vosk
    monkeypatch.setattr(speech, 'selected_model', lambda *a: tmp_path)
    monkeypatch.setattr(vosk, 'Model', Mock(side_effect=RuntimeError('模型损坏')))
    result = speech.check_status(tmp_path, DEFAULT_CONFIG, lambda message: None)
    assert not result['ok'] and '模型损坏' in result['message']
