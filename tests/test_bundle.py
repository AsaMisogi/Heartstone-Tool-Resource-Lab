"""按块读取的边界及压缩格式回归，不依赖游戏安装。"""
from pathlib import Path
import struct
import pytest
from UnityPy.helpers import CompressionHelper
from pengpeng.bundle import BundleDirectory, NodeStream, UnsupportedBundle


def make_bundle(path, mode, tail, padding):
    chunks = [b'abcdefgh', b'ijklmnop', b'qrstuvwx']
    compress = (lambda b: b) if mode == 0 else CompressionHelper.compress_lzma if mode == 1 else CompressionHelper.compress_lz4
    packed = [compress(b) for b in chunks]
    info = bytes(16) + struct.pack('>i', len(chunks))
    info += b''.join(struct.pack('>IIH', len(a), len(b), mode) for a,b in zip(chunks,packed))
    info += struct.pack('>iqqI', 1, 3, 18, 0) + b'CAB-test.resource\0'
    encoded = compress(info)
    flags = 64 | mode | (128 if tail else 0) | (512 if padding else 0)
    header = b'UnityFS\0' + struct.pack('>I', 8) + b'5.x.x\0' + b'6000.3.11f1\0'
    def build(size):
        result = header + struct.pack('>qIII', size, len(encoded), len(info), flags)
        result += bytes(-len(result) % 16)
        if not tail: result += encoded
        if padding: result += bytes(-len(result) % 16)
        result += b''.join(packed)
        if tail: result += encoded
        return result
    path.write_bytes(build(len(build(0))))


@pytest.mark.parametrize('mode', [0,1,2,3])
@pytest.mark.parametrize('tail', [False, True])
@pytest.mark.parametrize('padding', [False, True])
def test_random_reads_across_blocks(tmp_path, mode, tail, padding):
    path = tmp_path/'test.unity3d'
    make_bundle(path, mode, tail, padding)
    directory = BundleDirectory(path)
    assert not directory.cache  # 仅解析目录绝不能触碰正文。
    stream = NodeStream(directory, 3, 18)
    assert stream.read(2) == b'de'
    assert len(directory.cache) == 1
    stream.seek(3)
    assert stream.read(10) == b'ghijklmnop'
    stream.seek(-3, 2)
    assert stream.read() == b'stu'
    assert stream.read() == b''
    stream.seek(100)
    assert stream.read() == b''
    with pytest.raises(ValueError): stream.seek(-1)


def test_old_format_uses_fallback(tmp_path):
    path = tmp_path/'old'
    path.write_bytes(b'UnityFS\0' + struct.pack('>I',6))
    with pytest.raises(UnsupportedBundle): BundleDirectory(path)


def test_container_does_not_expand_preload_table():
    """GUID 查询只能取显式映射；展开预加载表会触发无关文件系统搜索。"""
    from types import SimpleNamespace
    from UnityPy.files import SerializedFile
    from pengpeng.bundle import DirectEnvironment
    class ExplicitContainer:
        def items(self):
            return [('guid', 'pointer')]
        def parse_preload_table(self):
            raise AssertionError('不应解析预加载依赖')
    file = object.__new__(SerializedFile)
    file._container = ExplicitContainer()
    env = DirectEnvironment()
    env.cabs['cab-test'] = file
    assert env.container == {'guid': 'pointer'}


def test_truncated_bundle_rejected(tmp_path):
    path = tmp_path/'bad.unity3d'
    make_bundle(path, 2, False, True)
    path.write_bytes(path.read_bytes()[:-3])
    with pytest.raises(ValueError, match='长度异常'):
        BundleDirectory(path)
