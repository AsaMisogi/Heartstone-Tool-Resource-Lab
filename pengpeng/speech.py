"""轻量离线台词识别：内置小模型、自定义目录及在线接口、流式解码和独立结果缓存。

此模块不接触 Unity/SQLite，也不写客户端字幕。由可终止的独立进程调用，
同一时刻只处理一个 AudioClip。正式发行版默认离线使用内置模型；仅构建工具下载固定模型。
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import time
import sys
from contextlib import chdir
from .speech_config import DEFAULT_CONFIG, validate_config
from urllib.request import urlopen
import zipfile

MODELS = {'zhcn': 'vosk-model-small-cn-0.22', 'enus': 'vosk-model-small-en-us-0.15'}
MAX_SECONDS = 30
MAX_DOWNLOAD = 64 * 1024 * 1024


def download_model(workspace, locale, progress=lambda message: None):
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
    progress('构建准备：正在下载官方离线小模型（约 40–42 MB）…')
    started = time.monotonic()
    try:
        with urlopen(f'https://alphacephei.com/vosk/models/{name}.zip', timeout=12) as response, archive.open('wb') as output:
            size = 0
            while block := response.read(256 * 1024):
                size += len(block)
                if size > MAX_DOWNLOAD or time.monotonic() - started > 900:
                    raise ValueError('模型下载超出大小或时间限制，请稍后重试')
                output.write(block)
                progress(f"正在准备 {name}：已下载 {size / 1024 / 1024:.1f} MiB")
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


def bundled_root():
    """源码与 PyInstaller 均以模块资源位置定位，不依赖当前工作目录。"""
    return Path(__file__).resolve().parent / 'models'


def ensure_model(workspace, locale, progress=lambda message: None):
    """运行时绝不隐式联网。发行包缺文件时给出可以执行的修复方法。"""
    roots = [bundled_root()]
    if not getattr(sys, 'frozen', False):
        roots.append(Path(workspace) / 'models')  # 兼容开发者已有模型。
    for root in roots:
        candidate = root / MODELS[locale]
        if (candidate / 'am/final.mdl').is_file() and (candidate / 'conf/model.conf').is_file():
            return candidate
    raise ValueError('内置语音模型缺失：请完整解压新版发行包，或在设置中选择自定义 Vosk 模型；源码运行请先执行 tools/prepare_models.py')


def selected_model(workspace, locale, config, progress):
    if config['provider'] == 'bundled' or not config[locale + '_path']:
        return ensure_model(workspace, locale, progress)
    path = Path(config[locale + '_path']).expanduser().resolve()
    if not (path / 'am/final.mdl').is_file() or not (path / 'conf/model.conf').is_file():
        raise ValueError('自定义模型不完整：请选择解压后直接包含 am 和 conf 的 Vosk 模型目录')
    return path


def model_identity(path):
    """目录、文件大小和修改时间共同标记模型，原地替换模型也会使缓存失效。"""
    digest = hashlib.sha256(str(path).encode('utf8'))
    for file in sorted(path.rglob('*')):
        if file.is_file():
            info = file.stat()
            digest.update(f'{file.relative_to(path)}:{info.st_size}:{info.st_mtime_ns}'.encode('utf8'))
    return digest.hexdigest()


def load_model(path):
    """绕过 Vosk Windows 原生库对中文绝对路径的窄字符处理。

    Python 用 Unicode 切换工作目录，原生库仅接收 ASCII 相对路径 '.'。
    模型构造同步读取全部资源，完成后恢复目录；音频路径已规范化为绝对路径。
    仅在串行识别子进程或构建检查进程中调用，不在共享工作目录的线程中调用。
    """
    from vosk import Model
    with chdir(path):
        return Model('.')


def check_status(workspace, config, progress):
    if config['provider'] == 'api':
        from .speech_api import check_api
        progress('正在检查在线 API（发送一秒静音）…')
        return check_api(workspace, config)
    from vosk import KaldiRecognizer, SetLogLevel
    SetLogLevel(-1)
    rows = []
    for locale, label in (('zhcn', '简体中文'), ('enus', '英语')):
        try:
            path = selected_model(workspace, locale, config, progress)
            progress(f'正在加载{label}模型，请稍候…')
            model = load_model(path)
            recognizer = KaldiRecognizer(model, 16000)
            recognizer.AcceptWaveform(b'\0' * 3200)
            recognizer.FinalResult()
            del recognizer, model  # 检查下一种语言前释放原生对象。
            rows.append({'ok': True, 'message': f'{label}：加载与识别检查通过 · {path}'})
        except Exception as exc:
            rows.append({'ok': False, 'message': f'{label}：{exc}'})
    return {'ok': all(row['ok'] for row in rows), 'message': '\n'.join(row['message'] for row in rows)}


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

    def recognize(self, paths, locale='zhcn', force=False, progress=lambda message: None, config=None):
        """音频内容和模型版本共同组成缓存键，避免游戏更新后命中旧台词。

        空识别也缓存，避免呻吟/笑声每次打开都消耗 CPU；手动重试跳过缓存。
        子采样分别结束识别再拼接，不丢掉一个 AudioClip 中的后续片段。
        """
        if locale not in MODELS:
            raise ValueError('轻量语音识别目前支持简体中文和英语；其他语言仍可使用原有台词与试听')
        files = checked_audio(self.workspace, paths)
        config = validate_config(config or DEFAULT_CONFIG)
        if config['provider'] == 'api':
            identity = 'api/' + config['api_url'] + '/' + config['api_model'] + '/' + locale
            model_name = config['api_model']
        else:
            model_path = selected_model(self.workspace, locale, config, progress)
            identity = 'vosk/pcm-v1/' + locale + '/' + model_identity(model_path)
            model_name = model_path.name
        digest = hashlib.sha256(identity.encode())
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
        if config['provider'] == 'api':
            from .speech_api import transcribe
            progress('正在通过已配置的 API 上传并识别语音…')
            started = time.monotonic()
            parts = []
            for path in files:
                with path.open('rb') as audio:
                    parts.append(transcribe(self.workspace, config, audio, locale))
            text = ' '.join(part for part in parts if part)
        else:
            try:
                from vosk import KaldiRecognizer, SetLogLevel
            except ImportError as exc:
                raise ValueError('语音识别组件未安装，请运行 Install-Dependencies.bat 后重新启动') from exc
            import numpy as np
            import soundfile as sf
            SetLogLevel(-1)
            progress('正在加载离线模型…')
            if self.locale != identity:
                self.model = None  # 切语言先释放旧模型，避免双份常驻。
                self.model = load_model(model_path)
                self.locale = identity
            progress('正在离线识别，结果不保证准确…')
            started, parts = time.monotonic(), []
            for file_index, path in enumerate(files):
                with sf.SoundFile(path) as audio:
                    recognizer = KaldiRecognizer(self.model, audio.samplerate)
                    processed, reported = 0, -1
                    # Vosk 内部完成抗混叠重采样；不用引入 ffmpeg / PyTorch / scipy。
                    for frames in audio.blocks(blocksize=4096, dtype='float32', always_2d=True):
                        processed += len(frames)
                        percent = int(processed * 10 / max(1, audio.frames))
                        if percent != reported:
                            reported = percent
                            progress(f'离线识别 · 采样 {file_index + 1}/{len(files)} · 音频帧 {processed}/{audio.frames}')
                        mono = np.clip(frames.mean(axis=1), -1, 1)
                        pcm = (mono * 32767).astype('<i2').tobytes()
                        if recognizer.AcceptWaveform(pcm):
                            parts.append(json.loads(recognizer.Result()).get('text', ''))
                    parts.append(json.loads(recognizer.FinalResult()).get('text', ''))
            text = ' '.join(p for p in parts if p).strip()
            if locale == 'zhcn':
                text = text.replace(' ', '')
        result = {'text': text, 'model': model_name, 'cached': False,
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
            checking = request.get('method') == 'speech_status'
            if not checking and not settings.get('speech_recognition', True):
                raise ValueError('语音识别已在设置中关闭')
            def progress(message):
                outbox.put({'event': 'speech_progress', 'request_id': identity, 'message': message})
            config = validate_config(settings.get('speech_config', DEFAULT_CONFIG))
            result = check_status(workspace, config, progress) if checking else engine.recognize(**request['params'], progress=progress, config=config)
            outbox.put({'id': identity, 'result': result})
        except Exception as exc:
            outbox.put({'id': identity, 'error': str(exc)})
