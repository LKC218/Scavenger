"""按需读取 UnityFS 解压区间，供只读资源解析使用。"""

from bisect import bisect_right
from functools import lru_cache
import io
import struct

import lz4.block

from 准备测试 import zstring


def decompress(data, size, flags):
    codec = flags & 63
    if codec not in (0, 2, 3):
        raise ValueError(f"未适配的 UnityFS 压缩类型：{codec}")
    decoded = lz4.block.decompress(data, uncompressed_size=size) if codec else data
    if len(decoded) != size:
        raise ValueError("UnityFS 解压长度不符")
    return decoded


class BundleWindow(io.RawIOBase):
    def __init__(self, bundle, start, length):
        self.bundle = bundle
        self.start = start
        self.length = length
        self.position = 0

    def tell(self):
        return self.position

    def seek(self, offset, whence=io.SEEK_SET):
        if whence not in (io.SEEK_SET, io.SEEK_CUR, io.SEEK_END):
            raise ValueError("无效定位方式")
        position = (0, self.position, self.length)[whence] + offset
        if position < 0:
            raise ValueError("负读取偏移")
        self.position = position
        return position

    def read(self, size=-1):
        remaining = self.length - self.position
        if remaining <= 0:
            return b""
        size = remaining if size is None or size < 0 else min(size, remaining)
        data = self.bundle.read(self.start + self.position, size)
        self.position += size
        return data


class UnityFSBundle:
    def __init__(self, source, size):
        self.source = source
        signature = zstring(source)
        self.version, = struct.unpack(">I", source.read(4))
        zstring(source)
        self.engine = zstring(source)
        declared, compressed, unpacked, flags = struct.unpack(">QIII", source.read(20))
        if signature != "UnityFS" or self.version not in (7, 8) or declared != size:
            raise ValueError("UnityFS 标记、版本或文件长度不符")
        if flags & 0x100:
            raise ValueError("未适配的 UnityFS 包头标记")
        source.seek((source.tell() + 15) & ~15)
        data_start = source.tell()
        if flags & 0x80:
            source.seek(size - compressed)
        table = io.BytesIO(decompress(source.read(compressed), unpacked, flags))
        if flags & 0x80:
            source.seek(data_start)
        if flags & 0x200:
            source.seek((source.tell() + 15) & ~15)

        table.seek(16)
        count, = struct.unpack(">I", table.read(4))
        self.blocks = [struct.unpack(">IIH", table.read(10)) for _ in range(count)]
        self.offsets = [0]
        self.packed_offsets = [source.tell()]
        for raw, packed, _ in self.blocks:
            if raw == 0 or packed == 0:
                raise ValueError("UnityFS 块长度为零")
            self.offsets.append(self.offsets[-1] + raw)
            self.packed_offsets.append(self.packed_offsets[-1] + packed)

        count, = struct.unpack(">I", table.read(4))
        self.nodes = {}
        for _ in range(count):
            start, length, node_flags = struct.unpack(">QQI", table.read(20))
            name = zstring(table)
            if name in self.nodes or start + length > self.offsets[-1]:
                raise ValueError("UnityFS 节点重复或越界")
            self.nodes[name] = (start, length, node_flags)
        self.read_block = lru_cache(maxsize=24)(self._read_block)

    def _read_block(self, index):
        raw, packed, flags = self.blocks[index]
        self.source.seek(self.packed_offsets[index])
        return decompress(self.source.read(packed), raw, flags)

    def read(self, start, length):
        end = start + length
        if not 0 <= start <= end <= self.offsets[-1]:
            raise ValueError("UnityFS 读取越界")
        pieces = []
        while start < end:
            index = bisect_right(self.offsets, start) - 1
            base = self.offsets[index]
            stop = min(end, self.offsets[index + 1])
            pieces.append(self.read_block(index)[start - base:stop - base])
            start = stop
        return b"".join(pieces)

    def open_node(self, name):
        start, length, _ = self.nodes[name]
        return BundleWindow(self, start, length)
