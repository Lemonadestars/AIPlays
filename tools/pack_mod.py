"""把 mod 文件夹打包成 BG3 的 .pak（LSPK v18），不依赖任何第三方库。

用法：
    python pack_mod.py ../mod/AIPlays AIPlays.pak
    python pack_mod.py --verify AIPlays.pak      # 读回并列出内容

格式参考 Norbyte/lslib 的 PackageFormat.cs / PackageWriter.cs：
    "LSPK" + LSPKHeader16 + 文件数据 + 文件列表(LZ4 block 压缩)
按游戏自带 pak（patch 8）的实际格式：每个文件用 LZ4 block 存储（Flags = 0x42），
数据相对文件头之后按 64 字节对齐、用 0xAD 填充；文件列表同样是 LZ4 block。
这里的 LZ4 只写字面量（不真正压缩），格式合法，任何 LZ4 解码器都能解。
"""

import os
import struct
import sys

SIGNATURE = b"LSPK"
VERSION = 18
HEADER_FMT = "<IQIBB16sH"          # Version, FileListOffset, FileListSize, Flags, Priority, Md5, NumParts
ENTRY_FMT = "<256sIHBBII"          # Name, OffsetLow, OffsetHigh, ArchivePart, Flags, SizeOnDisk, UncompressedSize
HEADER_SIZE = struct.calcsize(HEADER_FMT)   # 36
ENTRY_SIZE = struct.calcsize(ENTRY_FMT)     # 272
DATA_START = 4 + HEADER_SIZE                # 40
ALIGN = 64
FLAG_LZ4_DEFAULT = 0x42                     # 游戏自带 pak 里每个文件都是这个值


def lz4_block_literals(data):
    """生成只含字面量的合法 LZ4 block（不做真正压缩，但任何 LZ4 解码器都能解）。"""
    n = len(data)
    out = bytearray()
    if n >= 15:
        out.append(0xF0)
        rest = n - 15
        while rest >= 255:
            out.append(255)
            rest -= 255
        out.append(rest)
    else:
        out.append(n << 4)
    out += data
    return bytes(out)


def lz4_block_decode(src, size):
    """最小 LZ4 block 解码器，用来校验。"""
    dst = bytearray()
    i = 0
    while i < len(src):
        token = src[i]; i += 1
        lit = token >> 4
        if lit == 15:
            while True:
                b = src[i]; i += 1
                lit += b
                if b != 255:
                    break
        dst += src[i:i + lit]; i += lit
        if i >= len(src):
            break
        off = src[i] | (src[i + 1] << 8); i += 2
        ml = token & 15
        if ml == 15:
            while True:
                b = src[i]; i += 1
                ml += b
                if b != 255:
                    break
        ml += 4
        for _ in range(ml):
            dst.append(dst[-off])
    if len(dst) != size:
        raise ValueError(f"LZ4 解码长度不符：{len(dst)} != {size}")
    return bytes(dst)


def collect(root):
    files = []
    for dirpath, _, names in os.walk(root):
        for name in sorted(names):
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            files.append((rel, full))
    files.sort()
    return files


def pack(root, out_path):
    files = collect(root)
    if not any(rel.startswith("Mods/") and rel.endswith("/meta.lsx") for rel, _ in files):
        raise SystemExit("找不到 Mods/<名字>/meta.lsx，传入的应该是包含 Mods 文件夹的那一层")

    entries = []
    with open(out_path, "wb") as f:
        f.write(SIGNATURE)
        f.write(b"\0" * HEADER_SIZE)              # 占位，最后回填
        for rel, full in files:
            with open(full, "rb") as src:
                data = src.read()
            name = rel.encode("utf-8")
            if len(name) >= 256:
                raise SystemExit(f"路径太长：{rel}")
            offset = f.tell()
            stored = lz4_block_literals(data) if data else b""
            f.write(stored)
            entries.append(struct.pack(ENTRY_FMT, name.ljust(256, b"\0"),
                                       offset & 0xFFFFFFFF, (offset >> 32) & 0xFFFF,
                                       0, FLAG_LZ4_DEFAULT, len(stored), len(data)))
            pad = (ALIGN - (f.tell() - DATA_START) % ALIGN) % ALIGN
            f.write(b"\xAD" * pad)

        list_offset = f.tell()
        raw = b"".join(entries)
        comp = lz4_block_literals(raw)
        f.write(struct.pack("<II", len(entries), len(comp)))
        f.write(comp)
        list_size = f.tell() - list_offset

        f.seek(4)
        f.write(struct.pack(HEADER_FMT, VERSION, list_offset, list_size, 0, 0, b"\0" * 16, 1))
    print(f"已生成 {out_path}（{len(entries)} 个文件）")
    for rel, _ in files:
        print("  ", rel)


def verify(path):
    with open(path, "rb") as f:
        blob = f.read()
    assert blob[:4] == SIGNATURE, "签名不对"
    version, list_off, list_size, flags, prio, md5, parts = struct.unpack_from(HEADER_FMT, blob, 4)
    assert version == VERSION, f"版本不对：{version}"
    count, comp_size = struct.unpack_from("<II", blob, list_off)
    assert list_size == 8 + comp_size, "FileListSize 不对"
    raw = lz4_block_decode(blob[list_off + 8:list_off + 8 + comp_size], count * ENTRY_SIZE)
    out = {}
    for k in range(count):
        name, lo, hi, part, fl, size, usize = struct.unpack_from(ENTRY_FMT, raw, k * ENTRY_SIZE)
        name = name.split(b"\0", 1)[0].decode("utf-8")
        off = lo | (hi << 32)
        stored = blob[off:off + size]
        if (fl & 0x0F) == 2:
            out[name] = lz4_block_decode(stored, usize) if usize else b""
        else:
            out[name] = stored
        print(f"  {name}  {len(out[name])} 字节  flags={fl:#x}  offset%64={(off - DATA_START) % ALIGN}")
    return out


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--verify":
        verify(sys.argv[2])
    elif len(sys.argv) == 3:
        pack(sys.argv[1], sys.argv[2])
    else:
        print(__doc__)
