#!/usr/bin/env python3

from __future__ import annotations

from pathlib import Path
import subprocess

WIDTH = 960
HEIGHT = 540
OUTPUT = Path(__file__).resolve().parents[1] / "assets" / "exocortex-loop.gif"
POSTER = Path(__file__).resolve().parents[1] / "assets" / "exocortex-loop-poster.gif"
POSTER_PNG = Path(__file__).resolve().parents[1] / "assets" / "exocortex-loop-poster.png"
POSTER_PPM = Path(__file__).resolve().parents[1] / "assets" / "exocortex-loop-poster.ppm"

BG = 0
PANEL = 1
PANEL_ALT = 2
BORDER = 3
TEXT = 4
MUTED = 5
CYAN = 6
CYAN_DIM = 7
MAGENTA = 8
AMBER = 9
GREEN = 10
RED = 11

PALETTE = [
    (7, 9, 13),
    (15, 19, 27),
    (20, 26, 37),
    (40, 50, 69),
    (245, 247, 251),
    (137, 148, 167),
    (102, 252, 241),
    (38, 96, 103),
    (255, 0, 127),
    (242, 169, 0),
    (64, 214, 126),
    (255, 88, 120),
]

FONT = {
    " ": [".....", ".....", ".....", ".....", ".....", ".....", "....."],
    "+": [".....", "..#..", "..#..", "#####", "..#..", "..#..", "....."],
    "-": [".....", ".....", ".....", "#####", ".....", ".....", "....."],
    ".": [".....", ".....", ".....", ".....", ".....", ".##..", ".##.."],
    "/": ["....#", "...#.", "..#..", ".#...", "#....", ".....", "....."],
    ">": ["#....", ".#...", "..#..", "...#.", "..#..", ".#...", "#...."],
    ":": [".....", ".##..", ".##..", ".....", ".##..", ".##..", "....."],
    "A": [".###.", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"],
    "B": ["####.", "#...#", "#...#", "####.", "#...#", "#...#", "####."],
    "C": [".####", "#....", "#....", "#....", "#....", "#....", ".####"],
    "D": ["####.", "#...#", "#...#", "#...#", "#...#", "#...#", "####."],
    "E": ["#####", "#....", "#....", "####.", "#....", "#....", "#####"],
    "F": ["#####", "#....", "#....", "####.", "#....", "#....", "#...."],
    "G": [".####", "#....", "#....", "#.###", "#...#", "#...#", ".###."],
    "H": ["#...#", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"],
    "I": ["#####", "..#..", "..#..", "..#..", "..#..", "..#..", "#####"],
    "J": ["..###", "...#.", "...#.", "...#.", "...#.", "#..#.", ".##.."],
    "K": ["#...#", "#..#.", "#.#..", "##...", "#.#..", "#..#.", "#...#"],
    "L": ["#....", "#....", "#....", "#....", "#....", "#....", "#####"],
    "M": ["#...#", "##.##", "#.#.#", "#.#.#", "#...#", "#...#", "#...#"],
    "N": ["#...#", "##..#", "#.#.#", "#..##", "#...#", "#...#", "#...#"],
    "O": [".###.", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."],
    "P": ["####.", "#...#", "#...#", "####.", "#....", "#....", "#...."],
    "Q": [".###.", "#...#", "#...#", "#...#", "#.#.#", "#..#.", ".##.#"],
    "R": ["####.", "#...#", "#...#", "####.", "#.#..", "#..#.", "#...#"],
    "S": [".####", "#....", "#....", ".###.", "....#", "....#", "####."],
    "T": ["#####", "..#..", "..#..", "..#..", "..#..", "..#..", "..#.."],
    "U": ["#...#", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."],
    "V": ["#...#", "#...#", "#...#", "#...#", "#...#", ".#.#.", "..#.."],
    "W": ["#...#", "#...#", "#...#", "#.#.#", "#.#.#", "##.##", "#...#"],
    "X": ["#...#", "#...#", ".#.#.", "..#..", ".#.#.", "#...#", "#...#"],
    "Y": ["#...#", "#...#", ".#.#.", "..#..", "..#..", "..#..", "..#.."],
    "Z": ["#####", "....#", "...#.", "..#..", ".#...", "#....", "#####"],
}


def make_canvas() -> bytearray:
    return bytearray([BG]) * (WIDTH * HEIGHT)


def rect(canvas: bytearray, x: int, y: int, w: int, h: int, color: int) -> None:
    if w <= 0 or h <= 0:
        return
    x0 = max(0, x)
    y0 = max(0, y)
    x1 = min(WIDTH, x + w)
    y1 = min(HEIGHT, y + h)
    if x1 <= x0 or y1 <= y0:
        return
    for row in range(y0, y1):
        start = row * WIDTH + x0
        canvas[start : start + (x1 - x0)] = bytes([color]) * (x1 - x0)


def frame_rect(canvas: bytearray, x: int, y: int, w: int, h: int, border: int, fill: int | None = None) -> None:
    if fill is not None:
        rect(canvas, x + 1, y + 1, w - 2, h - 2, fill)
    rect(canvas, x, y, w, 1, border)
    rect(canvas, x, y + h - 1, w, 1, border)
    rect(canvas, x, y, 1, h, border)
    rect(canvas, x + w - 1, y, 1, h, border)


def dot(canvas: bytearray, x: int, y: int, scale: int, color: int) -> None:
    rect(canvas, x, y, scale, scale, color)


def text(canvas: bytearray, message: str, x: int, y: int, scale: int, color: int, tracking: int = 1) -> None:
    cursor = x
    for char in message.upper():
        glyph = FONT.get(char, FONT[" "])
        for row, pattern in enumerate(glyph):
            for col, pixel in enumerate(pattern):
                if pixel == "#":
                    dot(canvas, cursor + col * scale, y + row * scale, scale, color)
        cursor += (5 * scale) + tracking


def divider(canvas: bytearray, x: int, y: int, w: int, color: int) -> None:
    rect(canvas, x, y, w, 1, color)


def base_frame(step: int) -> bytearray:
    canvas = make_canvas()
    rect(canvas, 24, 24, WIDTH - 48, HEIGHT - 48, PANEL)
    frame_rect(canvas, 24, 24, WIDTH - 48, HEIGHT - 48, BORDER)
    rect(canvas, 24, 24, WIDTH - 48, 42, PANEL_ALT)
    divider(canvas, 24, 66, WIDTH - 48, BORDER)
    rect(canvas, 34, 34, 10, 10, CYAN)
    text(canvas, "EXOCORTEX LOOP", 58, 32, 2, CYAN, 3)
    text(canvas, "DEMO", WIDTH - 146, 32, 2, MUTED, 3)

    frame_rect(canvas, 42, 92, 560, 350, BORDER, PANEL_ALT)
    frame_rect(canvas, 624, 92, 292, 102, BORDER, PANEL_ALT)
    frame_rect(canvas, 624, 208, 292, 124, BORDER, PANEL_ALT)
    frame_rect(canvas, 624, 346, 292, 96, BORDER, PANEL_ALT)
    frame_rect(canvas, 42, 458, 874, 58, BORDER, PANEL_ALT)

    text(canvas, "COMMAND SURFACE", 62, 108, 2, MUTED, 2)
    text(canvas, "ACTIVE CONTEXT", 644, 108, 2, MUTED, 2)
    text(canvas, "SESSION OUTPUTS", 644, 224, 2, MUTED, 2)
    text(canvas, "COMPOUNDING", 644, 362, 2, MUTED, 2)

    text(canvas, "STATELESS CHAT LOSES CONTEXT", 62, 150, 4, TEXT, 2)
    text(canvas, "EXOCORTEX KEEPS IT", 62, 188, 3, MUTED, 2)

    frame_rect(canvas, 62, 238, 520, 176, BORDER, PANEL)
    text(canvas, "TERMINAL", 84, 252, 2, MUTED, 2)
    divider(canvas, 78, 278, 488, BORDER)
    if step % 2 == 0:
        rect(canvas, 528, 392, 16, 4, CYAN)

    chips = [
        ("LOCAL FIRST", CYAN if step >= 3 else MUTED),
        ("MARKDOWN", GREEN if step >= 4 else MUTED),
        ("JOURNAL", AMBER if step >= 5 else MUTED),
        ("MEMORY", MAGENTA if step >= 6 else MUTED),
    ]
    chip_x = 64
    for label, color in chips:
        frame_rect(canvas, chip_x, 472, 188, 28, BORDER, PANEL)
        text(canvas, label, chip_x + 12, 480, 2, color, 2)
        chip_x += 202

    return canvas


def line(canvas: bytearray, label: str, x: int, y: int, color: int = TEXT, prompt: bool = False) -> None:
    if prompt:
        text(canvas, ">", x, y, 3, CYAN, 1)
        text(canvas, label, x + 20, y, 3, color, 1)
    else:
        text(canvas, label, x, y, 3, color, 1)


def context_panel(canvas: bytearray, rows: list[tuple[str, int]]) -> None:
    y = 138
    for label, color in rows:
        line(canvas, label, 646, y, color)
        y += 20


def outputs_panel(canvas: bytearray, rows: list[tuple[str, int]]) -> None:
    y = 256
    for label, color in rows:
        line(canvas, label, 646, y, color)
        y += 22


def compounding_panel(canvas: bytearray, rows: list[tuple[str, int]]) -> None:
    y = 394
    for label, color in rows:
        line(canvas, label, 646, y, color)
        y += 18


def build_frames() -> tuple[list[bytes], list[int]]:
    frames: list[bytes] = []
    delays = [12, 12, 14, 14, 16, 16, 18, 34]
    for step in range(8):
        canvas = base_frame(step)
        line(canvas, "CD DOMAINS/WORK/PROJECTS/ACME", 86, 298, prompt=True)
        if step >= 1:
            line(canvas, "CODEX", 86, 334, prompt=True)
        if step >= 2:
            line(canvas, "+ BOOTSTRAP LOADED", 110, 372, GREEN)
        if step >= 3:
            line(canvas, "+ ROOT AND PROJECT CONTEXT", 110, 396, CYAN)

        context_rows: list[tuple[str, int]] = []
        outputs_rows: list[tuple[str, int]] = []
        comp_rows: list[tuple[str, int]] = []

        if step >= 2:
            context_rows.extend(
                [
                    ("AGENT CHIEF OF STAFF", CYAN),
                    ("MODE CONVERSATION", TEXT),
                    ("VISIBLE FILES LOADED", MUTED),
                ]
            )
        if step >= 4:
            outputs_rows.extend(
                [
                    ("+ MANIFEST", GREEN),
                    ("+ TRANSCRIPT", GREEN),
                    ("+ SUMMARY", GREEN),
                    ("+ CANDIDATES", AMBER),
                ]
            )
        if step >= 5:
            outputs_rows.append(("+ DAILY JOURNAL", AMBER))
        if step >= 6:
            comp_rows.extend(
                [
                    ("WORKFLOW CANDIDATE", MAGENTA),
                    ("MEMORY CANDIDATE", CYAN),
                    ("QUESTION QUEUE", TEXT),
                ]
            )
        if step >= 7:
            line(canvas, "NEXT SESSION STARTS SMARTER", 86, 430, TEXT)
            comp_rows.append(("REUSABLE CONTEXT INJECTED", GREEN))

        context_panel(canvas, context_rows)
        outputs_panel(canvas, outputs_rows)
        compounding_panel(canvas, comp_rows)
        frames.append(bytes(canvas))
    return frames, delays


def pack_codes_fixed(codes: list[int], code_size: int) -> bytes:
    out = bytearray()
    current = 0
    bits = 0
    for code in codes:
        current |= code << bits
        bits += code_size
        while bits >= 8:
            out.append(current & 0xFF)
            current >>= 8
            bits -= 8
    if bits:
        out.append(current & 0xFF)
    return bytes(out)


def lzw_compress(data: bytes, min_code_size: int) -> bytes:
    clear = 1 << min_code_size
    end = clear + 1
    chunk = 10
    codes: list[int] = []
    for start in range(0, len(data), chunk):
        codes.append(clear)
        codes.extend(data[start : start + chunk])
    codes.append(end)
    return pack_codes_fixed(codes, min_code_size + 1)


def gif_sub_blocks(payload: bytes) -> bytes:
    chunks = bytearray()
    for index in range(0, len(payload), 255):
        block = payload[index : index + 255]
        chunks.append(len(block))
        chunks.extend(block)
    chunks.append(0)
    return bytes(chunks)


def encode_gif(frames: list[bytes], delays: list[int]) -> bytes:
    min_code_size = 4
    palette_bytes = bytearray()
    for red, green, blue in PALETTE:
        palette_bytes.extend([red, green, blue])
    while len(palette_bytes) < 3 * (1 << (min_code_size + 1)):
        palette_bytes.extend([0, 0, 0])

    result = bytearray()
    result.extend(b"GIF89a")
    result.extend(WIDTH.to_bytes(2, "little"))
    result.extend(HEIGHT.to_bytes(2, "little"))
    result.append(0b10010011)
    result.append(0)
    result.append(0)
    result.extend(palette_bytes)
    result.extend(b"\x21\xFF\x0BNETSCAPE2.0\x03\x01\x00\x00\x00")

    for frame, delay in zip(frames, delays):
        compressed = lzw_compress(frame, min_code_size)
        result.extend(b"\x21\xF9\x04")
        result.append(0b00000100)
        result.extend(delay.to_bytes(2, "little"))
        result.append(0)
        result.append(0)
        result.append(0x2C)
        result.extend((0).to_bytes(2, "little"))
        result.extend((0).to_bytes(2, "little"))
        result.extend(WIDTH.to_bytes(2, "little"))
        result.extend(HEIGHT.to_bytes(2, "little"))
        result.append(0)
        result.append(min_code_size)
        result.extend(gif_sub_blocks(compressed))

    result.append(0x3B)
    return bytes(result)


def write_ppm(frame: bytes, path: Path) -> None:
    with path.open("wb") as handle:
        handle.write(f"P6\n{WIDTH} {HEIGHT}\n255\n".encode("ascii"))
        for pixel in frame:
            red, green, blue = PALETTE[pixel]
            handle.write(bytes((red, green, blue)))


def write_gif(frames: list[bytes], delays: list[int]) -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_bytes(encode_gif(frames, delays))
    POSTER.write_bytes(encode_gif([frames[0]], [50]))
    write_ppm(frames[0], POSTER_PPM)
    subprocess.run(
        ["sips", "-s", "format", "png", str(POSTER_PPM), "--out", str(POSTER_PNG)],
        check=True,
        capture_output=True,
        text=True,
    )
    POSTER_PPM.unlink(missing_ok=True)


def main() -> None:
    frames, delays = build_frames()
    write_gif(frames, delays)
    print(f"Wrote {OUTPUT}")
    print(f"Wrote {POSTER}")
    print(f"Wrote {POSTER_PNG}")


if __name__ == "__main__":
    main()
