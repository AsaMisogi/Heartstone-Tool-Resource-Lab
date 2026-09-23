"""UnityFS 按块读取：索引只读取对象元数据，播放时才读取对应采样块。

兼容范围刻意限定为现代、未加密的 UnityFS；其他格式交回 UnityPy。
不修改 UnityPy 全局类，也不生成解压副本，便携版首次运行即可受益。
"""
from bisect import bisect_right
from collections import OrderedDict
from io import RawIOBase

import UnityPy
from UnityPy.helpers import CompressionHelper
from UnityPy.streams import EndianBinaryReader


class UnsupportedBundle(ValueError):
    """让调用方对旧格式使用原有读取路径。"""


class DirectEnvironment(UnityPy.Environment):
    @property
    def container(self):
        """只查显式 GUID 映射，不展开预加载表。

        UnityPy 默认会为了反查对象名称解析所有预加载依赖，甚至递归搜索
        文件系统。本工具用 manifest + CAB 精确寻址，无需这份反向映射。
        """
        return {key: pointer for file in self.cabs.values()
                if isinstance(file, UnityPy.files.SerializedFile)
                for key, pointer in file.container.items()}


def decompress(data, size, flags):
    mode = flags & 63
    if mode == 0:
        result = data
    elif mode == 1:
        result = CompressionHelper.decompress_lzma(data)
    elif mode in (2, 3):
        result = CompressionHelper.decompress_lz4(data, size)
    else:
        raise UnsupportedBundle(f'不支持的压缩方式：{mode}')
    if len(result) != size:
        raise ValueError('资源块解压长度不符')
    return result


class BundleDirectory:
    """目录解析不碰资源正文；CAB 定位和实际读取共用同一份块表。"""
    def __init__(self, path):
        self.path = path
        self.cache = OrderedDict()
        with path.open('rb') as stream:
            r = EndianBinaryReader(stream)
            if r.read_string_to_null() != 'UnityFS' or r.read_u_int() < 7:
                raise UnsupportedBundle('非现代 UnityFS')
            r.read_string_to_null()
            engine = r.read_string_to_null()
            size = r.read_long()
            compressed, plain, flags = r.read_u_int(), r.read_u_int(), r.read_u_int()
            if not (engine == '0.0.0' or engine.startswith(('2022.', '6000.'))) or flags & ~0x2ff:
                raise UnsupportedBundle('旧引擎或加密资源包')
            if size != path.stat().st_size or compressed > size or plain > 64 * 1024 * 1024:
                raise ValueError('资源包目录长度异常')
            r.align_stream(16)
            start = r.Position
            if flags & 128:
                r.Position = size - compressed
            info = EndianBinaryReader(decompress(r.read_bytes(compressed), plain, flags))
            data_start = start if flags & 128 else r.Position
            if flags & 512:
                data_start = (data_start + 15) // 16 * 16
            info.read_bytes(16)
            count = info.read_int()
            if not 0 <= count <= plain // 10:
                raise ValueError('资源块数量异常')
            self.blocks, self.starts = [], []
            offset = 0
            for _ in range(count):
                unpacked, packed, mode = info.read_u_int(), info.read_u_int(), info.read_u_short()
                if not unpacked or data_start + packed > (size - compressed if flags & 128 else size):
                    raise ValueError('资源块越界或长度为零')
                if mode & 63 not in (0, 1, 2, 3):
                    raise UnsupportedBundle('未知资源块压缩')
                self.starts.append(offset)
                self.blocks.append((data_start, packed, unpacked, mode))
                offset += unpacked
                data_start += packed
            count = info.read_int()
            if not 0 <= count <= plain // 21:
                raise ValueError('资源文件数量异常')
            self.nodes = []
            for _ in range(count):
                begin, length, node_flags = info.read_long(), info.read_long(), info.read_u_int()
                name = info.read_string_to_null()
                if begin < 0 or length < 0 or begin + length > offset:
                    raise ValueError('资源文件越界')
                self.nodes.append((name, begin, length, node_flags))

    def read(self, offset, size):
        result = []
        index = bisect_right(self.starts, offset) - 1
        # 每次请求只打开一次文件；缓存不保留句柄，淘汰环境后可直接释放。
        with self.path.open('rb') as stream:
            while size > 0:
                location, packed, plain, flags = self.blocks[index]
                if index not in self.cache:
                    stream.seek(location)
                    self.cache[index] = decompress(stream.read(packed), plain, flags)
                self.cache.move_to_end(index)
                block = self.cache[index]
                begin = offset - self.starts[index]
                take = min(size, plain - begin)
                result.append(block[begin:begin + take])
                offset += take
                size -= take
                index += 1
                # 同时按数量和字节控制缓存，避免少数巨块撑大常驻内存。
                while len(self.cache) > 8 or sum(map(len, self.cache.values())) > 8 * 1024 * 1024:
                    self.cache.popitem(last=False)
        return b''.join(result)

    def environment(self):
        env = DirectEnvironment()
        for name, offset, size, flags in self.nodes:
            stream = NodeStream(self, offset, size)
            if flags & 4:
                env.load_file(stream.read(), name=name)
            else:
                reader = EndianBinaryReader(stream)
                env.files[name] = reader
                env.register_cab(name, reader)
        return env


class NodeStream(RawIOBase):
    """向 UnityPy 提供单个 resource/resS 文件的可定位视图。"""
    def __init__(self, directory, offset, size):
        self.directory, self.offset, self.size = directory, offset, size
        self.position = 0

    def tell(self):
        return self.position

    def seek(self, offset, whence=0):
        position = offset + (self.position if whence == 1 else self.size if whence == 2 else 0)
        if whence not in (0, 1, 2) or position < 0:
            raise ValueError('无效的资源偏移')
        self.position = position
        return position

    def read(self, size=-1):
        size = max(0, min(self.size - self.position, self.size if size is None or size < 0 else size))
        data = self.directory.read(self.offset + self.position, size) if size else b''
        self.position += len(data)
        return data
