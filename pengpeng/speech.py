"""轻量离线台词识别：固定小模型、按需下载、流式解码和独立结果缓存。

此模块不接触 Unity/SQLite，也不写客户端字幕。由可终止的独立进程调用，
同一时刻只处理一个 AudioClip。中文和英文各自按需安装，不自动装大模型。
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import time
from urllib.request import urlopen
import zipfile

MODELS = {'zhcn': 'vosk-model-small-cn-0.22', 'enus': 'vosk-model-small-en-us-0.15'}
MAX_SECONDS = 30
MAX_DOWNLOAD = 64 * 1024 * 1024


def ensure_model(workspace, locale, progress=lambda message: None):
    """仅从官方 HTTPS 下载固定版本；限大小、限时并原子发布完整目录。

    中断不会留下被误认为可用的模型。下次重试清理本模型的临时目录，
    解压前验证所有成员路径，避免压缩包越界写入工作区之外。
    """
    name = MODELS[locale]
    root = Path(workspace) / 'models'
    target = root / name
    if (target / 'am/final.mdl').is_file() and (target / 'conf/model.conf').is_file():
        return target
    root.mkdir(parents=True, exist_ok=True)
    archive = root / (name + '.part')
    staging = root / (name + '.unpacking')
    if staging.exists():
        shutil.rmtree(staging)
    progress('首次使用：正在下载离线小模型（约 40–42 MB），可继续试听；关闭语音页可取消…')
    started = time.monotonic()
    try:
        with urlopen(f'https://alphacephei.com/vosk/models/{name}.zip', timeout=12) as response, archive.open('wb') as output:
            size = 0
            while block := response.read(256 * 1024):
                size += len(block)
                if size > MAX_DOWNLOAD or time.monotonic() - started > 110:
                    raise ValueError('模型下载超出大小或时间限制，请稍后重试')
                output.write(block)
        with zipfile.ZipFile(archive) as zipped:
            members = zipped.infolist()
            if len(members) > 500 or sum(m.file_size for m in members) > 160 * 1024 * 1024:
                raise ValueError('模型解压大小异常')
            for member in members:
                destination = (staging / member.filename).resolve()
                if not destination.is_relative_to((staging / name).resolve()):
                    raise ValueError('模型压缩包路径异常')
            zipped.extractall(staging)
        prepared = staging / name
        if not (prepared / 'am/final.mdl').is_file() or not (prepared / 'conf/model.conf').is_file():
            raise ValueError('下载的模型不完整')
        # target 仅指向上述固定名称，绝不使用调用方传入的任意删除路径。
        if target.exists():
            shutil.rmtree(target)
        prepared.replace(target)
        return target
    finally:
        archive.unlink(missing_ok=True)
        if staging.exists():
            shutil.rmtree(staging)


def checked_audio(workspace, paths):
    """只接受应用已解码的缓存 WAV；先检查总时长，再加载任何识别模型。"""
    import soundfile as sf
    if not isinstance(paths, list) or not 1 <= len(paths) <= 16:
        raise ValueError('语音子采样数量无效')
    cache = (Path(workspace) / 'cache').resolve()
    files, duration = [], 0
    for value in paths:
        path = Path(value).resolve()
        if not path.is_relative_to(cache) or path.suffix.lower() != '.wav':
            raise ValueError('只能识别应用缓存中的 WAV')
        info = sf.info(path)
        if not 16000 <= info.samplerate <= 96000 or not 1 <= info.channels <= 2:
            raise ValueError('暂不支持此采样率或声道数')
        duration += info.duration
        if duration > MAX_SECONDS or path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError('仅自动识别总长不超过 30 秒的短台词')
        files.append(path)
    return files


class Recognizer:
    def __init__(self, workspace):
        self.workspace = Path(workspace)
        self.model = None
        self.locale = None

    def recognize(self, paths, locale='zhcn', force=False, progress=lambda message: None):
        """音频内容和模型版本共同组成缓存键，避免游戏更新后命中旧台词。

        空识别也缓存，避免呻吟/笑声每次打开都消耗 CPU；手动重试跳过缓存。
        子采样分别结束识别再拼接，不丢掉一个 AudioClip 中的后续片段。
        """
        if locale not in MODELS:
            raise ValueError('轻量语音识别目前支持简体中文和英语；其他语言仍可使用原有台词与试听')
        files = checked_audio(self.workspace, paths)
        digest = hashlib.sha256(('vosk-0.3.45/pcm-v1/' + MODELS[locale]).encode())
        for path in files:
            digest.update(path.stat().st_size.to_bytes(8, 'little'))
            with path.open('rb') as audio:
                while block := audio.read(256 * 1024):
                    digest.update(block)
        cache = self.workspace / 'cache/speech' / (digest.hexdigest() + '.json')
        if not force:
            try:
                data = json.loads(cache.read_text('utf8'))
                if isinstance(data, dict) and isinstance(data.get('text'), str):
                    return {**data, 'cached': True}
            except (OSError, ValueError, TypeError):
                pass
        try:
            from vosk import Model, KaldiRecognizer, SetLogLevel
        except ImportError as exc:
            raise ValueError('语音识别组件未安装，请运行 Install-Dependencies.bat 后重新启动') from exc
        import numpy as np
        import soundfile as sf
        SetLogLevel(-1)
        model_path = ensure_model(self.workspace, locale, progress)
        progress('正在离线识别，结果不保证准确…')
        if self.locale != locale:
            self.model = None  # 切语言先释放旧模型，避免双份常驻。
            self.model = Model(str(model_path))
            self.locale = locale
        started, parts = time.monotonic(), []
        for path in files:
            with sf.SoundFile(path) as audio:
                recognizer = KaldiRecognizer(self.model, audio.samplerate)
                # Vosk 内部完成抗混叠重采样；不用引入 ffmpeg / PyTorch / scipy。
                for frames in audio.blocks(blocksize=4096, dtype='float32', always_2d=True):
                    mono = np.clip(frames.mean(axis=1), -1, 1)
                    pcm = (mono * 32767).astype('<i2').tobytes()
                    if recognizer.AcceptWaveform(pcm):
                        parts.append(json.loads(recognizer.Result()).get('text', ''))
                parts.append(json.loads(recognizer.FinalResult()).get('text', ''))
        text = ' '.join(p for p in parts if p).strip()
        if locale == 'zhcn':
            text = text.replace(' ', '')
        result = {'text': text, 'model': MODELS[locale], 'cached': False,
                  'seconds': round(time.monotonic() - started, 3)}
        cache.parent.mkdir(parents=True, exist_ok=True)
        temporary = cache.with_suffix('.tmp')
        temporary.write_text(json.dumps(result, ensure_ascii=False), 'utf8')
        temporary.replace(cache)
        return result


def speech_worker(inbox, outbox, workspace):
    """原生识别只在此子进程加载，限制数值库线程且降低 Windows 调度优先级。"""
    os.environ['OMP_NUM_THREADS'] = '1'
    os.environ['OPENBLAS_NUM_THREADS'] = '1'
    if os.name == 'nt':
        import ctypes
        api = ctypes.windll.kernel32
        api.GetCurrentProcess.restype = ctypes.c_void_p
        api.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        api.SetPriorityClass(api.GetCurrentProcess(), 0x4000)  # BELOW_NORMAL_PRIORITY_CLASS
    engine = Recognizer(workspace)
    while True:
        request = inbox.get()
        identity = request['id']
        try:
            settings = json.loads((Path(workspace) / 'settings.json').read_text('utf8'))
            if not settings.get('speech_recognition', True):
                raise ValueError('语音识别已在设置中关闭')
            def progress(message):
                outbox.put({'event': 'speech_progress', 'request_id': identity, 'message': message})
            result = engine.recognize(**request['params'], progress=progress)
            outbox.put({'id': identity, 'result': result})
        except Exception as exc:
            outbox.put({'id': identity, 'error': str(exc)})
