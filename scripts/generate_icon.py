"""Draw original pixel art using only Python's standard library."""
from pathlib import Path
import struct
import zlib

SIZE = 64


def pixels():
    canvas = [[(0, 0, 0, 0)] * SIZE for _ in range(SIZE)]
    def rect(x1, y1, x2, y2, color):
        color = tuple(bytes.fromhex(color)) + (255,)
        for y in range(y1, y2):
            for x in range(x1, x2):
                canvas[y][x] = color
    for y in range(2, 62):
        for x in range(2, 62):
            if (max(13-x, 0, x-50)**2 + max(13-y, 0, y-50)**2) <= 121:
                canvas[y][x] = (51, 86, 63, 255)
    rect(8, 12, 56, 51, "dce8bb")
    rect(8, 44, 56, 51, "aabd87")
    rect(12, 38, 18, 44, "547a55")
    rect(14, 32, 16, 40, "547a55")
    rect(46, 39, 52, 44, "547a55")
    rect(48, 33, 50, 41, "547a55")
    rect(22, 30, 44, 47, "f5e9bd")
    rect(20, 30, 46, 33, "825a45")
    rect(22, 26, 44, 30, "c9875d")
    rect(26, 22, 40, 26, "d49665")
    rect(30, 18, 36, 22, "dfa675")
    rect(31, 36, 37, 47, "537157")
    rect(25, 35, 29, 39, "9ebdab")
    rect(39, 35, 43, 39, "9ebdab")
    rect(31, 47, 37, 51, "f4deaa")
    rect(44, 15, 48, 19, "fff6d5")
    rect(42, 16, 50, 18, "fff6d5")
    rect(45, 13, 47, 21, "fff6d5")
    rect(13, 17, 18, 19, "a4bd8a")
    rect(18, 53, 46, 55, "a4bd8a")
    return canvas


def save_png(path, size):
    art = pixels()
    raw = b"".join(b"\x00" + b"".join(bytes(art[y*SIZE//size][x*SIZE//size]) for x in range(size)) for y in range(size))
    def chunk(name, data):
        return struct.pack(">I", len(data)) + name + data + struct.pack(">I", zlib.crc32(name + data) & 0xffffffff)
    Path(path).write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def generate(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for size in (16, 32, 128, 256, 512):
        save_png(directory / ("icon_%sx%s.png" % (size, size)), size)
        save_png(directory / ("icon_%sx%s@2x.png" % (size, size)), size * 2)


if __name__ == "__main__":
    import sys
    generate(sys.argv[1])
