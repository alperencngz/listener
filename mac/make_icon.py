"""Draw the Listener app icon and pack it into mac/Listener.icns.

Pure Pillow + the macOS ``iconutil`` tool; no design assets needed. The icon
is a rounded indigo square with a white microphone and two sound arcs.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
OUT = HERE / "Listener.icns"
SIZE = 1024


def draw_icon(size: int = SIZE) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # background: rounded square, vertical gradient indigo -> violet
    pad = int(size * 0.06)
    radius = int(size * 0.22)
    top, bottom = (79, 70, 229), (124, 58, 237)
    grad = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(grad)
    for y in range(size):
        t = y / (size - 1)
        color = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)) + (255,)
        gdraw.line([(0, y), (size, y)], fill=color)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([pad, pad, size - pad, size - pad], radius=radius, fill=255)
    img.paste(grad, (0, 0), mask)

    white = (255, 255, 255, 255)
    cx = size // 2
    # microphone capsule
    cap_w, cap_h = int(size * 0.20), int(size * 0.36)
    cap_top = int(size * 0.24)
    draw.rounded_rectangle([cx - cap_w // 2, cap_top, cx + cap_w // 2, cap_top + cap_h],
                           radius=cap_w // 2, fill=white)
    # cradle arc
    stroke = int(size * 0.045)
    arc_r = int(size * 0.19)
    arc_cy = cap_top + cap_h - int(size * 0.10)
    draw.arc([cx - arc_r, arc_cy - arc_r, cx + arc_r, arc_cy + arc_r], start=0, end=180, fill=white, width=stroke)
    # stem + base
    stem_top = arc_cy + arc_r
    draw.line([(cx, stem_top), (cx, stem_top + int(size * 0.08))], fill=white, width=stroke)
    base_w = int(size * 0.22)
    base_y = stem_top + int(size * 0.08)
    draw.line([(cx - base_w // 2, base_y), (cx + base_w // 2, base_y)], fill=white, width=stroke)
    # sound arcs on the right
    for k, r in enumerate((0.30, 0.38)):
        rr = int(size * r)
        alpha = 230 - k * 70
        draw.arc([cx - rr, arc_cy - int(size * 0.16) - rr, cx + rr, arc_cy - int(size * 0.16) + rr],
                 start=-35, end=35, fill=(255, 255, 255, alpha), width=int(stroke * 0.8))
    return img


def build_icns(out: Path = OUT) -> Path:
    if shutil.which("iconutil") is None:
        raise SystemExit("iconutil not found (macOS only)")
    base = draw_icon()
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "Listener.iconset"
        iconset.mkdir()
        for px in (16, 32, 128, 256, 512):
            base.resize((px, px), Image.LANCZOS).save(iconset / f"icon_{px}x{px}.png")
            base.resize((px * 2, px * 2), Image.LANCZOS).save(iconset / f"icon_{px}x{px}@2x.png")
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(out)], check=True)
    return out


if __name__ == "__main__":
    print(f"wrote {build_icns()}")
