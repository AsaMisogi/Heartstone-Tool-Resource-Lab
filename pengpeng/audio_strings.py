"""读取客户端 / hsdata 的 TSV 字幕，使用相同规则建立精确音频键索引。"""
import csv
import io
import re
from pathlib import PureWindowsPath


def audio_key(value):
    """统一大小写、路径与已知音频扩展名，保留序号和条件分支。"""
    value = PureWindowsPath(value.strip().replace('\x00', '')).name
    return re.sub(r'\.(wav|ogg|mp3)$', '', value, flags=re.I).upper()


def parse_audio_strings(text):
    """COMMENT 中的明确文件名也可作键；歧义别名不覆盖准确 TAG。"""
    rows = csv.DictReader(io.StringIO(text.lstrip('\ufeff')), delimiter='\t')
    if not {'TAG', 'TEXT'}.issubset(rows.fieldnames or []):
        raise ValueError('字幕来源不是 TAG/TEXT 格式的字符串表')
    tags, aliases = {}, {}
    for row in rows:
        key = audio_key(row.get('TAG') or '')
        value = (row.get('TEXT') or '').replace('\x00', '').strip()
        if not key or key.startswith('#') or not value:
            continue
        tags[key] = value
        for alias in re.findall(r'([\w-]+)\.(?:wav|ogg|mp3)', row.get('COMMENT') or '', re.I):
            aliases.setdefault(audio_key(alias), set()).add(value)
    return {**{key: next(iter(values)) for key, values in aliases.items() if len(values) == 1}, **tags}
