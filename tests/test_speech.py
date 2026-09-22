"""离线识别回归：不用联网/真实模型，验证缓存、输入边界和进程生命周期。"""
import io
import json
import queue
from unittest.mock import Mock
import zipfile

import numpy as np
import pytest
import soundfile as sf

from pengpeng import speech
from pengpeng.service import Service
from pengpeng.speech_process import SpeechProcess


def wav(tmp_path, seconds=0.1, name='test.wav'):
    path = tmp_path / 'cache' / name
    path.parent.mkdir(exist_ok=True)
    sf.write(path, np.zeros((int(16000 * seconds), 2)), 16000)
    return str(path)


def test_defaults_migrate_and_user_choice_survives(tmp_path):
    service = Service(tmp_path)
    assert [(s['id'], s['enabled']) for s in service.settings['transcript_sources']] == [
        ('baidu', True), ('huiji', True), ('hsdata', False), ('wikigg', False)]
    chosen = list(reversed(service.settings['transcript_sources']))
    chosen[0]['enabled'] = True
    service.save_settings(transcript_sources=chosen, speech_recognition=False)
    assert Service(tmp_path).settings['transcript_sources'] == chosen
    assert Service(tmp_path).settings['speech_recognition'] is False
    before = service.settings_path.read_bytes()
    with pytest.raises(ValueError):
        service.save_settings(speech_recognition='false')
    assert service.settings_path.read_bytes() == before


def test_audio_bounds_before_model_download(tmp_path, monkeypatch):
    download = Mock()
    monkeypatch.setattr(speech, 'ensure_model', download)
    engine = speech.Recognizer(tmp_path)
    with pytest.raises(ValueError, match='30 秒'):
        engine.recognize([wav(tmp_path, 31)])
    with pytest.raises(ValueError, match='缓存'):
        engine.recognize([str(tmp_path / 'outside.wav')])
    with pytest.raises(ValueError, match='目前支持'):
        engine.recognize([], locale='jajp')
    download.assert_not_called()


def test_cache_force_empty_and_content_identity(tmp_path, monkeypatch):
    import vosk
    model = Mock()
    monkeypatch.setattr(vosk, 'Model', model)
    monkeypatch.setattr(speech, 'ensure_model', Mock(return_value=tmp_path))
    recognizer = Mock()
    recognizer.AcceptWaveform.return_value = False
    recognizer.FinalResult.return_value = json.dumps({'text': '测 试'})
    factory = Mock(return_value=recognizer)
    monkeypatch.setattr(vosk, 'KaldiRecognizer', factory)
    engine = speech.Recognizer(tmp_path)
    paths = [wav(tmp_path)]
    assert engine.recognize(paths)['text'] == '测试'
    assert engine.recognize(paths)['cached']
    assert factory.call_count == 1
    recognizer.FinalResult.return_value = '{"text":""}'
    assert engine.recognize(paths, force=True)['text'] == ''
    assert engine.recognize(paths)['cached']
    assert factory.call_count == 2
    sf.write(paths[0], np.ones(1600) * 0.1, 16000)
    assert not engine.recognize(paths)['cached']
    assert factory.call_count == 3 and model.call_count == 1


def test_multisample_preserved(tmp_path, monkeypatch):
    import vosk
    monkeypatch.setattr(vosk, 'Model', Mock())
    monkeypatch.setattr(speech, 'ensure_model', Mock(return_value=tmp_path))
    recognizer = Mock()
    recognizer.AcceptWaveform.return_value = False
    recognizer.FinalResult.side_effect = ['{"text":"first"}', '{"text":"second"}']
    monkeypatch.setattr(vosk, 'KaldiRecognizer', Mock(return_value=recognizer))
    result = speech.Recognizer(tmp_path).recognize([wav(tmp_path), wav(tmp_path, name='second.wav')], 'enus')
    assert result['text'] == 'first second'


def test_model_download_rejects_zip_escape_and_cleans_partial(tmp_path, monkeypatch):
    content = io.BytesIO()
    with zipfile.ZipFile(content, 'w') as zipped:
        zipped.writestr('../escaped.txt', 'bad')
    monkeypatch.setattr(speech, 'urlopen', lambda *a, **kw: io.BytesIO(content.getvalue()))
    with pytest.raises(ValueError, match='路径异常'):
        speech.ensure_model(tmp_path, 'zhcn')
    assert not (tmp_path / 'escaped.txt').exists()
    assert not list((tmp_path / 'models').glob('*.part'))


def process_fixture(tmp_path):
    messages = []
    controller = SpeechProcess(tmp_path, messages.append)
    controller.process = Mock()
    controller.process.is_alive.return_value = True
    controller.inbox = Mock()
    controller.outbox = Mock()
    controller.outbox.get_nowait.side_effect = queue.Empty
    return controller, messages


def test_timeout_idle_release_and_busy_rejection(tmp_path):
    controller, messages = process_fixture(tmp_path)
    controller.request_id = 3
    controller.deadline = 0
    controller.request({'id': 4})
    assert messages[-1]['id'] == 4 and '已有语音' in messages[-1]['error']
    process = controller.process
    controller.poll()
    process.terminate.assert_called_once()
    assert controller.process is None and messages[-1]['id'] == 3
    controller, messages = process_fixture(tmp_path)
    controller.activity = 0
    controller.poll()
    assert controller.process is None and not messages


def test_disabled_worker_never_loads_model(tmp_path, monkeypatch):
    (tmp_path / 'settings.json').write_text('{"speech_recognition":false}')
    engine = Mock()
    monkeypatch.setattr(speech, 'Recognizer', Mock(return_value=engine))
    incoming, outgoing = Mock(), Mock()
    incoming.get.side_effect = [{'id': 1, 'params': {}}, KeyboardInterrupt]
    with pytest.raises(KeyboardInterrupt):
        speech.speech_worker(incoming, outgoing, tmp_path)
    engine.recognize.assert_not_called()
    assert '已在设置中关闭' in outgoing.put.call_args.args[0]['error']
