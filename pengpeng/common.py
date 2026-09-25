"""与界面、Unity 无关的规范化规则，便于单独验证。"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

# DBF LocalizedString 的序列顺序。不是按用户界面的显示顺序排列。
LOCALES = ('enus', 'dede', 'eses', 'esmx', 'frfr', 'itit', 'jajp',
           'kokr', 'plpl', 'ptbr', 'ruru', 'thth', 'zhcn', 'zhtw')
LOCALE_NAMES = dict(zip(LOCALES, ('English', 'Deutsch', 'Español (ES)', 'Español (MX)',
    'Français', 'Italiano', '日本語', '한국어', 'Polski', 'Português', 'Русский', 'ไทย', '简体中文', '繁體中文')))


def guid(value: str) -> str:
    """资源引用通常是“可读文件名:32 位 GUID”；拒绝把普通文本当资源 ID。"""
    candidate = value.rsplit(':', 1)[-1] if isinstance(value, str) else ''
    return candidate.lower() if re.fullmatch(r'[0-9a-fA-F]{32}', candidate) else ''


def localized(record: dict, field: str, locale: str = 'zhcn') -> str:
    values = record.get(field, {}).get('m_locValues', [])
    index = LOCALES.index(locale) if locale in LOCALES else 12
    return (values[index] if index < len(values) else '') or (values[0] if values else '')


def safe_name(value: str) -> str:
    """允许中文，同时规避 Windows 保留名、非法字符和尾部点空格。"""
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', value).strip(' .')[:110] or 'asset'
    if re.fullmatch(r'(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?', value):
        value = '_' + value
    return value


def fingerprint(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        stat = path.stat()
        digest.update(f'{path.resolve()}:{stat.st_size}:{stat.st_mtime_ns}'.encode())
    return digest.hexdigest()[:20]


def executable_version(path: Path) -> str:
    """读取 Windows 可执行文件版本资源；失败时保留快照指纹，不猜版本号。"""
    import ctypes
    import sys
    if sys.platform != 'win32':
        return ''
    try:
        from ctypes import wintypes as w
        api = ctypes.WinDLL('version', use_last_error=True)
        api.GetFileVersionInfoSizeW.argtypes = [w.LPCWSTR, ctypes.POINTER(w.DWORD)]
        api.GetFileVersionInfoW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p]
        api.VerQueryValueW.argtypes = [ctypes.c_void_p, w.LPCWSTR, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(w.UINT)]
        size = api.GetFileVersionInfoSizeW(str(path), None)
        if not size:
            return ''
        buffer = ctypes.create_string_buffer(size)
        if not api.GetFileVersionInfoW(str(path), 0, size, buffer):
            return ''
        pointer, length = ctypes.c_void_p(), w.UINT()
        if not api.VerQueryValueW(buffer, '\\', ctypes.byref(pointer), ctypes.byref(length)):
            return ''
        values = ctypes.cast(pointer, ctypes.POINTER(w.DWORD))
        # VS_FIXEDFILEINFO 的 dwProductVersionMS / LS 位于第 4、5 项。
        return '.'.join(str(v) for v in (values[4] >> 16, values[4] & 65535, values[5] >> 16, values[5] & 65535))
    except (OSError, ValueError):
        return ''


def audio_category(name: str, bundle: str, duration: float = 0) -> str:
    """先看片段用途，再以专用音乐包和时长补充；不让包名覆盖音效证据。

    时长仅在已确认音乐来源后用于区分短曲，绝不单凭时长把环境循环算作音乐。
    保留四个用户可理解的大类，具体场景由 audio_library 的子分组表达。
    """
    from .music_titles import BACKGROUND_TITLES
    clip, package = name.lower().strip(), bundle.lower()
    if re.search(r'(?:^|[_\-])(?:vo|voice|dialog)[_\-]', clip):
        return '角色语音'
    if 'stinger' in clip or re.search(r'(?:^|[_ ])(?:jingle|sting)(?:[_ ]|$)', clip):
        return '短音乐 / 登场曲'
    # 动作/环境证据先于包名。MusicBox 等道具、音乐主题系列的施法音效
    # 以及音乐包夹带的交互音，不应因名字中含 music 而成为背景音乐。
    effect = re.search(r'(?:^|[_ ])(?:sfx|fx|amb|ambient|ambience|wallah|impact|attack|'
                       r'death|damage|click|button|poke|cast|precast|fizzle|sound)(?:[_ ]|$)', clip)
    if effect:
        return '音效'
    music = (clip in BACKGROUND_TITLES or clip.startswith('heromusic_')
             or re.search(r'(?:^|[_ ])(?:music|mus)(?:[_ ]|$)', clip)
             or package.startswith(('musicexpansion_', 'heromusic_')) and duration >= 30)
    if music:
        # 前奏/尾奏、胜负提示和登场乐属于短音乐，不将所有英雄曲一概当短曲。
        return '短音乐 / 登场曲' if 0 < duration < 30 else '背景音乐'
    return '音效'


def bundle_locale(bundle: str) -> str:
    match = re.search(r'_(' + '|'.join(LOCALES) + r')(?:-|_)', bundle.lower())
    return match.group(1) if match else 'global'


def references(value, path: str = ''):
    """保留字段路径，供界面解释资源为何关联到某张卡牌。"""
    if isinstance(value, dict):
        for key, item in value.items():
            yield from references(item, f'{path}.{key}' if path else key)
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            yield from references(item, f'{path}[{index}]')
    elif isinstance(value, str) and guid(value):
        yield path, value
