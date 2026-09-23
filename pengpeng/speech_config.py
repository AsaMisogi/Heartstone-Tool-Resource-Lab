"""语音后端配置校验；API 密钥按用户选择直接保存在本地设置。"""
import os
from urllib.parse import urlsplit

DEFAULT_CONFIG = {'provider': 'bundled', 'zhcn_path': '', 'enus_path': '',
                  'api_url': '', 'api_model': ''}


def validate_config(value):
    """只支持明确的 Vosk 目录或 multipart 转写协议，不猜测任意模型格式。"""
    if not isinstance(value, dict):
        raise ValueError('语音配置必须为对象')
    config = {key: value.get(key, default) for key, default in DEFAULT_CONFIG.items()}
    if any(not isinstance(v, str) or len(v) > 2048 for v in config.values()):
        raise ValueError('语音配置字段无效')
    config = {k: v.strip() for k, v in config.items()}
    if config['provider'] not in ('bundled', 'local', 'api'):
        raise ValueError('请选择内置模型、自定义 Vosk 或在线 API')
    if config['provider'] == 'local' and not any(config[k] for k in ('zhcn_path', 'enus_path')):
        raise ValueError('请至少选择一个自定义模型目录')
    if config['provider'] == 'api':
        url = urlsplit(config['api_url'])
        if (url.scheme != 'https' and not (url.scheme == 'http' and url.hostname in ('localhost', '127.0.0.1', '::1'))) or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError('API 地址须为完整 HTTPS 转写地址（本机服务可用 HTTP），不能含账号、查询参数或片段')
    if config['provider'] == 'api' and not config['api_model']:
        raise ValueError('请填写服务商的语音模型名称')
    return config


def load_key(workspace):
    """跟随工作区迁移；可选环境变量供命令行部署使用。"""
    import json
    from pathlib import Path
    settings = json.loads((Path(workspace) / 'settings.json').read_text('utf8'))
    return settings.get('speech_api_key', '') or os.environ.get('PENGPENG_SPEECH_API_KEY', '')
