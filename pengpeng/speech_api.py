"""兼容 multipart /audio/transcriptions：上传 WAV，读取 JSON text 字段。"""
import io
import json
import wave
import requests
from .speech_config import load_key


def transcribe(workspace, config, audio, locale):
    """禁用重定向以免密钥或音频外流；不回显响应正文，不重试收费请求。"""
    key = load_key(workspace)
    headers = {'Authorization': 'Bearer ' + key} if key else {}
    try:
        with requests.post(config['api_url'], headers=headers,
                           data={'model': config['api_model'], 'language': {'zhcn': 'zh', 'enus': 'en'}[locale],
                                 'response_format': 'json'},
                           files={'file': ('speech.wav', audio, 'audio/wav')},
                           timeout=(10, 60), allow_redirects=False, stream=True) as response:
            if response.status_code != 200:
                raise ValueError(f'语音 API 返回 HTTP {response.status_code}；请检查地址、密钥、模型与服务额度')
            content = bytearray()
            for chunk in response.iter_content(16384):
                content.extend(chunk)
                if len(content) > 1024 * 1024:
                    raise ValueError('语音 API 响应过大')
            try:
                data = json.loads(content)
            except ValueError:
                raise ValueError('语音 API 未返回有效 JSON') from None
            if not isinstance(data, dict) or not isinstance(data.get('text'), str):
                raise ValueError('语音 API 响应缺少 text 字符串，请确认转写接口兼容性')
            return data['text'].strip()
    except requests.RequestException:
        raise ValueError('语音 API 连接失败或超时，请检查网络、证书及服务地址') from None


def check_api(workspace, config):
    """用户主动检查时发送一秒静音，真实验证认证、模型与转写协议。"""
    audio = io.BytesIO()
    with wave.open(audio, 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b'\0' * 32000)
    transcribe(workspace, config, audio.getvalue(), 'enus')
    return {'ok': True, 'message': '在线 API 检查通过：连接、认证、模型与转写响应正常。'}
