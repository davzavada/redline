"""Vygeneruje ikonu aplikace ``assets/app.ico`` — jen standardní knihovna.

Motiv: obálka (dopis) na tmavě modrém podkladu (#1F3B57 + bílá).

Ikona se kreslí vektorově v normalizovaných souřadnicích 0..1 a rasterizuje
se s několikanásobným převzorkováním (antialiasing). Do souboru ICO se ukládá:

* velikosti 16, 32, 48 a 64 px jako klasický "BMP" záznam
  (BITMAPINFOHEADER + 32bitová BGRA data zdola nahoru + 1bitová AND maska),
* velikost 256 px jako PNG (formát ICO to od Windows Vista umí a ušetří
  to zhruba 260 kB).

Použití::

    python tools/make_icon.py                 # zapíše assets/app.ico
    python tools/make_icon.py --check         # jen ověří existující soubor
    python tools/make_icon.py --no-png-256    # 256 px také jako BMP záznam
    python tools/make_icon.py -o jinam.ico

Skript nic nestahuje a nepotřebuje Pillow.
"""

from __future__ import annotations

import argparse
import struct
import sys
import zlib
from pathlib import Path

# --- barvy ------------------------------------------------------------------

NAVY = (0x1F, 0x3B, 0x57)
WHITE = (0xFF, 0xFF, 0xFF)

#: Velikosti vrstev, které ikona obsahuje.
SIZES: tuple[int, ...] = (16, 32, 48, 64, 256)

#: Kolik vzorků na stranu pixelu se použije pro vyhlazení hran.
SUPERSAMPLE = 4

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
ICO_SIGNATURE = b"\x00\x00\x01\x00"

# --- geometrie motivu (normalizované souřadnice 0..1) -----------------------

#: Zaoblený čtverec podkladu.
BG_RADIUS = 0.165

#: Tělo obálky.
ENV_LEFT, ENV_TOP, ENV_RIGHT, ENV_BOTTOM = 0.155, 0.285, 0.845, 0.735
ENV_RADIUS = 0.028

#: Chlopeň obálky — trojúhelník od horních rohů dolů do středu.
FLAP_APEX_Y = 0.575


def _in_rounded_rect(
    x: float, y: float, x0: float, y0: float, x1: float, y1: float, r: float
) -> bool:
    """Leží bod uvnitř obdélníku se zaoblenými rohy?"""

    if x < x0 or x > x1 or y < y0 or y > y1:
        return False
    r = min(r, (x1 - x0) / 2.0, (y1 - y0) / 2.0)
    if r <= 0.0:
        return True
    cx = x0 + r if x < x0 + r else (x1 - r if x > x1 - r else x)
    cy = y0 + r if y < y0 + r else (y1 - r if y > y1 - r else y)
    if cx == x and cy == y:
        return True
    dx = x - cx
    dy = y - cy
    return dx * dx + dy * dy <= r * r


def _in_flap(x: float, y: float) -> bool:
    """Leží bod v trojúhelníku chlopně obálky?

    Trojúhelník má vrcholy (ENV_LEFT, ENV_TOP), (ENV_RIGHT, ENV_TOP)
    a (střed, FLAP_APEX_Y); protože je symetrický a má vodorovnou základnu,
    stačí lineární interpolace šířky podle výšky.
    """

    if y < ENV_TOP or y > FLAP_APEX_Y:
        return False
    t = (y - ENV_TOP) / (FLAP_APEX_Y - ENV_TOP)  # 0 nahoře, 1 ve špičce
    mid = (ENV_LEFT + ENV_RIGHT) / 2.0
    half = (ENV_RIGHT - ENV_LEFT) / 2.0 * (1.0 - t)
    return mid - half <= x <= mid + half


def _sample(x: float, y: float) -> tuple[int, int, int] | None:
    """Barva jednoho vzorku, nebo ``None`` mimo ikonu (průhledno)."""

    if not _in_rounded_rect(x, y, 0.0, 0.0, 1.0, 1.0, BG_RADIUS):
        return None
    if _in_rounded_rect(x, y, ENV_LEFT, ENV_TOP, ENV_RIGHT, ENV_BOTTOM, ENV_RADIUS):
        if not _in_flap(x, y):
            return WHITE
    return NAVY


def render_rgba(size: int, samples: int = SUPERSAMPLE) -> bytes:
    """Vykreslí ikonu dané velikosti jako RGBA bajty (shora dolů)."""

    if size < 1:
        raise ValueError("velikost ikony musí být kladná")
    total = samples * samples
    step = 1.0 / (size * samples)
    half = step / 2.0
    out = bytearray(size * size * 4)
    pos = 0
    for py in range(size):
        base_y = py * samples
        for px in range(size):
            base_x = px * samples
            r = g = b = 0
            covered = 0
            for sy in range(samples):
                y = (base_y + sy) * step + half
                for sx in range(samples):
                    color = _sample((base_x + sx) * step + half, y)
                    if color is not None:
                        r += color[0]
                        g += color[1]
                        b += color[2]
                        covered += 1
            if covered:
                out[pos] = r // covered
                out[pos + 1] = g // covered
                out[pos + 2] = b // covered
                out[pos + 3] = (covered * 255 + total // 2) // total
            pos += 4
    return bytes(out)


# --- zápis dílčích formátů --------------------------------------------------


def _png_chunk(tag: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + tag
        + payload
        + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
    )


def encode_png(rgba: bytes, size: int) -> bytes:
    """Minimální PNG (8bit RGBA, bez prokládání) ze surových RGBA bajtů."""

    stride = size * 4
    raw = bytearray()
    for row in range(size):
        raw.append(0)  # filtr None
        raw += rgba[row * stride : (row + 1) * stride]
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (
        PNG_SIGNATURE
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + _png_chunk(b"IEND", b"")
    )


def encode_bmp(rgba: bytes, size: int) -> bytes:
    """Záznam ICO ve variantě BITMAPINFOHEADER + BGRA + AND maska."""

    stride = size * 4
    xor = bytearray()
    for row in range(size - 1, -1, -1):  # zdola nahoru
        line = rgba[row * stride : (row + 1) * stride]
        for i in range(0, stride, 4):
            xor += bytes((line[i + 2], line[i + 1], line[i], line[i + 3]))

    mask_stride = ((size + 31) // 32) * 4  # zarovnáno na 4 bajty
    mask = bytearray()
    for row in range(size - 1, -1, -1):
        line = bytearray(mask_stride)
        for col in range(size):
            if rgba[row * stride + col * 4 + 3] == 0:  # průhledné => bit 1
                line[col // 8] |= 0x80 >> (col % 8)
        mask += line

    header = struct.pack(
        "<IiiHHIIiiII",
        40,  # biSize
        size,  # biWidth
        size * 2,  # biHeight = obraz + AND maska
        1,  # biPlanes
        32,  # biBitCount
        0,  # biCompression = BI_RGB
        len(xor) + len(mask),  # biSizeImage
        0,  # biXPelsPerMeter
        0,  # biYPelsPerMeter
        0,  # biClrUsed
        0,  # biClrImportant
    )
    return header + bytes(xor) + bytes(mask)


def build_ico(sizes: tuple[int, ...] = SIZES, *, png_256: bool = True) -> bytes:
    """Složí celý soubor ICO."""

    payloads: list[tuple[int, bytes]] = []
    for size in sorted(sizes):
        rgba = render_rgba(size)
        if size >= 256 and png_256:
            payloads.append((size, encode_png(rgba, size)))
        else:
            payloads.append((size, encode_bmp(rgba, size)))

    count = len(payloads)
    offset = 6 + 16 * count
    directory = bytearray(struct.pack("<HHH", 0, 1, count))
    for size, data in payloads:
        directory += struct.pack(
            "<BBBBHHII",
            0 if size >= 256 else size,  # bWidth (0 znamená 256)
            0 if size >= 256 else size,  # bHeight
            0,  # bColorCount (0 = truecolor)
            0,  # bReserved
            1,  # wPlanes
            32,  # wBitCount
            len(data),  # dwBytesInRes
            offset,  # dwImageOffset
        )
        offset += len(data)
    return bytes(directory) + b"".join(data for _, data in payloads)


# --- kontrola ---------------------------------------------------------------


def check_ico(data: bytes) -> list[str]:
    """Přečte hotový ICO a vrátí čitelný popis vrstev. Chyby hlásí výjimkou."""

    if len(data) < 6:
        raise ValueError("soubor je kratší než hlavička ICONDIR")
    if data[:4] != ICO_SIGNATURE:
        raise ValueError("chybí podpis ICO (00 00 01 00)")
    reserved, ico_type, count = struct.unpack("<HHH", data[:6])
    if reserved != 0 or ico_type != 1:
        raise ValueError(f"nečekaná hlavička ICONDIR: reserved={reserved}, type={ico_type}")
    if count < 1:
        raise ValueError("ikona neobsahuje žádnou vrstvu")
    if len(data) < 6 + 16 * count:
        raise ValueError("soubor je kratší než tabulka vrstev")

    lines: list[str] = []
    seen_end = 6 + 16 * count
    for i in range(count):
        start = 6 + 16 * i
        (w, h, colors, res, planes, bits, nbytes, offset) = struct.unpack(
            "<BBBBHHII", data[start : start + 16]
        )
        width = w or 256
        height = h or 256
        if res != 0:
            raise ValueError(f"vrstva {i}: bReserved musí být 0, je {res}")
        if planes != 1 or bits != 32:
            raise ValueError(f"vrstva {i}: čekáno 1 rovina/32 bitů, je {planes}/{bits}")
        if colors != 0:
            raise ValueError(f"vrstva {i}: bColorCount musí být 0 pro truecolor")
        if offset < 6 + 16 * count or offset + nbytes > len(data):
            raise ValueError(f"vrstva {i}: data leží mimo soubor ({offset}+{nbytes})")
        payload = data[offset : offset + nbytes]

        if payload[:8] == PNG_SIGNATURE:
            if payload[12:16] != b"IHDR":
                raise ValueError(f"vrstva {i}: PNG bez chunku IHDR")
            pw, ph, depth, color_type = struct.unpack(">IIBB", payload[16:26])
            if (pw, ph) != (width, height):
                raise ValueError(
                    f"vrstva {i}: PNG má {pw}x{ph}, tabulka hlásí {width}x{height}"
                )
            if (depth, color_type) != (8, 6):
                raise ValueError(f"vrstva {i}: čekáno 8bit RGBA PNG, je {depth}/{color_type}")
            if payload[-8:-4] != b"IEND":
                raise ValueError(f"vrstva {i}: PNG nekončí chunkem IEND")
            kind = "PNG"
        else:
            if len(payload) < 40:
                raise ValueError(f"vrstva {i}: data kratší než BITMAPINFOHEADER")
            (bi_size, bi_w, bi_h, bi_planes, bi_bits, bi_compression, _size_image) = (
                struct.unpack("<IiiHHII", payload[:24])
            )
            if bi_size != 40:
                raise ValueError(f"vrstva {i}: biSize={bi_size}, čekáno 40")
            if bi_compression != 0:
                raise ValueError(f"vrstva {i}: čekáno BI_RGB, je {bi_compression}")
            if bi_planes != 1 or bi_bits != 32:
                raise ValueError(f"vrstva {i}: BMP {bi_planes} rovin / {bi_bits} bitů")
            if bi_w != width:
                raise ValueError(f"vrstva {i}: biWidth={bi_w}, tabulka hlásí {width}")
            if bi_h != height * 2:
                raise ValueError(
                    f"vrstva {i}: biHeight={bi_h}, čekáno {height * 2} (obraz + AND maska)"
                )
            mask_stride = ((width + 31) // 32) * 4
            expected = 40 + width * height * 4 + mask_stride * height
            if len(payload) != expected:
                raise ValueError(
                    f"vrstva {i}: délka dat {len(payload)} B, čekáno {expected} B"
                )
            kind = "BMP"
        lines.append(f"  {width:>3}x{height:<3} {kind:<3} {nbytes:>7} B  @ {offset}")
        seen_end = max(seen_end, offset + nbytes)

    if seen_end != len(data):
        raise ValueError(f"v souboru zbývá {len(data) - seen_end} B nepoužitých dat")
    return lines


# --- CLI --------------------------------------------------------------------


def default_output() -> Path:
    return Path(__file__).resolve().parent.parent / "assets" / "app.ico"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Vygeneruje ikonu aplikace (assets/app.ico) bez cizích knihoven."
    )
    parser.add_argument(
        "-o", "--output", type=Path, default=None, help="cesta k výstupnímu .ico"
    )
    parser.add_argument(
        "--check", action="store_true", help="jen zkontroluje existující soubor"
    )
    parser.add_argument(
        "--no-png-256",
        action="store_true",
        help="uloží i 256px vrstvu jako BMP (soubor bude o ~260 kB větší)",
    )
    args = parser.parse_args(argv)

    out: Path = args.output or default_output()

    if args.check:
        if not out.exists():
            print(f"Soubor {out} neexistuje.", file=sys.stderr)
            return 1
        data = out.read_bytes()
    else:
        data = build_ico(SIZES, png_256=not args.no_png_256)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)

    try:
        lines = check_ico(data)
    except ValueError as exc:
        print(f"Ikona {out} není v pořádku: {exc}", file=sys.stderr)
        return 2

    action = "Zkontrolováno" if args.check else "Zapsáno"
    print(f"{action}: {out} ({len(data)} B, {len(lines)} vrstev)")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
