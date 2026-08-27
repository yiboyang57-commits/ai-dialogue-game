"""生成 App 图标 icon.icns（纯 Python + 系统 iconutil，无第三方依赖）。

深蓝圆角底 + 白色剑形，产出 macOS 所需的 10 张 PNG 后交给 iconutil 合成 icns。
用法：python3 make_icon.py
"""
import math
import os
import shutil
import struct
import subprocess
import zlib

TOP = (0x5b, 0x7c, 0xfa)      # 顶部渐变蓝
BOT = (0x3a, 0x55, 0xc9)      # 底部渐变蓝
SWORD = (0xf4, 0xf6, 0xff)    # 剑身白

BLADE = [(0.5, 0.10), (0.578, 0.41), (0.545, 0.50), (0.455, 0.50), (0.422, 0.41)]


def _point_in_poly(x, y, poly):
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi + 1e-9) + xi:
            inside = not inside
        j = i
    return inside


def _inside_sword(x, y):
    if _point_in_poly(x, y, BLADE):
        return True
    if 0.31 <= x <= 0.69 and 0.53 <= y <= 0.59:      # 护手
        return True
    if 0.465 <= x <= 0.535 and 0.59 <= y <= 0.76:    # 剑柄
        return True
    if (x - 0.5) ** 2 + (y - 0.83) ** 2 <= 0.05 ** 2:  # 剑首
        return True
    return False


def _rounded_sdf(px, py, cx, cy, hw, hh, r):
    qx = abs(px - cx) - (hw - r)
    qy = abs(py - cy) - (hh - r)
    return math.hypot(max(qx, 0.0), max(qy, 0.0)) + min(max(qx, qy), 0.0) - r


def _sample(x, y):
    if _rounded_sdf(x, y, 0.5, 0.5, 0.46, 0.46, 0.225) > 0:
        return (0, 0, 0, 0)
    if _inside_sword(x, y):
        return (SWORD[0], SWORD[1], SWORD[2], 255)
    t = y
    r = round(TOP[0] + (BOT[0] - TOP[0]) * t)
    g = round(TOP[1] + (BOT[1] - TOP[1]) * t)
    b = round(TOP[2] + (BOT[2] - TOP[2]) * t)
    return (r, g, b, 255)


def _chunk(typ, data):
    return struct.pack(">I", len(data)) + typ + data + \
        struct.pack(">I", zlib.crc32(typ + data) & 0xffffffff)


def _write_png(path, size):
    ss = 3
    raw = bytearray()
    for py in range(size):
        raw.append(0)
        for px in range(size):
            ar = ag = ab = aa = 0
            for sy in range(ss):
                for sx in range(ss):
                    nx = (px + (sx + 0.5) / ss) / size
                    ny = (py + (sy + 0.5) / ss) / size
                    r, g, b, a = _sample(nx, ny)
                    ar += r; ag += g; ab += b; aa += a
            n = ss * ss
            raw += bytes((ar // n, ag // n, ab // n, aa // n))
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    png = b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + \
        _chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + _chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)


def main():
    iconset = "AppIcon.iconset"
    shutil.rmtree(iconset, ignore_errors=True)
    os.makedirs(iconset, exist_ok=True)

    files = [
        ("icon_16x16.png", 16),
        ("icon_16x16@2x.png", 32),
        ("icon_32x32.png", 32),
        ("icon_32x32@2x.png", 64),
        ("icon_128x128.png", 128),
        ("icon_128x128@2x.png", 256),
        ("icon_256x256.png", 256),
        ("icon_256x256@2x.png", 512),
        ("icon_512x512.png", 512),
        ("icon_512x512@2x.png", 1024),
    ]
    for fname, size in files:
        _write_png(os.path.join(iconset, fname), size)
        print("write", fname, f"{size}x{size}")

    subprocess.run(["iconutil", "-c", "icns", iconset, "-o", "icon.icns"], check=True)
    print("OK -> icon.icns")


if __name__ == "__main__":
    main()
