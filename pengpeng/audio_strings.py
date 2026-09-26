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
    """AUDIOFILE、COMMENT 中的明确文件名也可作键；歧义别名不覆盖准确 TAG。"""
    # enUS/PRESENCE 在表头前带维护注释；只跳过前导空行/注释，
    # 不能过滤整个文件，否则会破坏带换行的引号字段。
    stream = io.StringIO(text.lstrip('\ufeff'))
    while True:
        position = stream.tell()
        line = stream.readline()
        if not line or (line.strip() and not line.lstrip().startswith('#')):
            stream.seek(position)
            break
    rows = csv.DictReader(stream, delimiter='\t')
    if not {'TAG', 'TEXT'}.issubset(rows.fieldnames or []):
        raise ValueError('字幕来源不是 TAG/TEXT 格式的字符串表')
    tags, aliases = {}, {}
    for row in rows:
        key = audio_key(row.get('TAG') or '')
        value = (row.get('TEXT') or '').replace('\x00', '').strip()
        # 客户端以 <_死亡_> 等标记尚未填写的台词；它不是可朗读字幕。
        # 留空才能继续尝试网站精确匹配，不能让占位符抢占本地字幕优先级。
        if not key or key.startswith('#') or not value or re.fullmatch(r'<_.*_>', value):
            continue
        tags[key] = value
        # 英文客户端常将真实音频键放在独立 AUDIOFILE 列，且部分行不带扩展名。
        # 与 COMMENT 别名同样检测歧义，最终准确 TAG 始终拥有最高优先级。
        file_key = audio_key(row.get('AUDIOFILE') or '')
        if file_key:
            aliases.setdefault(file_key, set()).add(value)
        for alias in re.findall(r'([\w-]+)\.(?:wav|ogg|mp3)', row.get('COMMENT') or '', re.I):
            aliases.setdefault(audio_key(alias), set()).add(value)
    return {**{key: next(iter(values)) for key, values in aliases.items() if len(values) == 1}, **tags}
