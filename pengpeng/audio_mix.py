"""语音导出混音：主语音完整保留，配音与试听一样从零开始、最长 15 秒。"""
from pathlib import Path

import numpy as np
import soundfile as sf


def mix_samples(main_path, companion_paths, destination):
    """按最高输入采样率输出；单声道复制至双声道，峰值超限时统一衰减。

    不分别归一化音轨，保留素材间原有响度差。配音在读取时即限制帧数，
    避免长音乐文件占用大块内存；逐条累加，不建立音轨 × 时长的大数组。
    不同采样率通过线性插值上采样，原始素材单独导出仍保持原采样率。
    """
    paths = [Path(main_path), *dict.fromkeys(Path(p) for p in companion_paths if Path(p) != Path(main_path))]
    infos = [sf.info(p) for p in paths]
    if any(i.channels not in (1, 2) for i in infos):
        raise ValueError('合并导出仅支持单声道和双声道素材；请关闭合并导出保留原声道')
    rate = max(i.samplerate for i in infos)
    channels = max(i.channels for i in infos)
    lengths = [i.frames / i.samplerate if n == 0 else min(15, i.frames / i.samplerate)
               for n, i in enumerate(infos)]
    mixed = np.zeros((max(1, round(max(lengths) * rate)), channels), dtype=np.float32)
    for n, (path, info) in enumerate(zip(paths, infos)):
        data, _ = sf.read(path, frames=-1 if n == 0 else 15 * info.samplerate,
                          dtype='float32', always_2d=True)
        if not len(data):
            continue
        if info.samplerate != rate:
            positions = np.arange(round(len(data) * rate / info.samplerate)) * info.samplerate / rate
            data = np.column_stack([np.interp(positions, np.arange(len(data)), data[:, c])
                                    for c in range(info.channels)]).astype(np.float32)
        mixed[:len(data)] += data  # NumPy 将单声道广播到双声道。
    peak = float(np.max(np.abs(mixed)))
    gain = min(1.0, 0.98 / peak) if peak else 1.0
    mixed *= gain
    sf.write(destination, mixed, rate, subtype='PCM_16')
    return {'sample_rate': rate, 'channels': channels, 'gain': gain,
            'companion_limit_seconds': 15, 'tracks': [str(p) for p in paths]}
