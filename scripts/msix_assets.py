"""Images of the MSIX package (Microsoft Store build) from assets/icon-mark.png.

    python scripts/msix_assets.py <out-dir>

Writes <out-dir>/Assets/*.png with MRT qualifiers (scale-100/200/400,
targetsize-NN and their altform-unplated twins for the taskbar). The manifest
refers to the plain names (Assets\\Square44x44Logo.png); makepri builds the
resources.pri that maps them to these files (scripts/build_msix.ps1).

The mark is wide (about 1.86:1), so on square tiles it fills the width with a
margin; the background stays transparent (BackgroundColor="transparent").
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "assets" / "icon-mark.png"

# name: (width, height at scale-100, share of the width the mark takes)
TILES = {
    "Square44x44Logo": (44, 44, 0.96),
    "Square71x71Logo": (71, 71, 0.80),
    "Square150x150Logo": (150, 150, 0.70),
    "Square310x310Logo": (310, 310, 0.66),
    "Wide310x150Logo": (310, 150, 0.50),
    "StoreLogo": (50, 50, 0.92),
}
SCALES = (100, 200, 400)
TARGET_SIZES = (16, 24, 32, 48, 256)  # the app list, taskbar and Start icons


def _render(mark: Image.Image, width: int, height: int, share: float) -> Image.Image:
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    box_w = max(1, round(width * share))
    box_h = max(1, round(height * 0.9))
    scale = min(box_w / mark.width, box_h / mark.height)
    size = (max(1, round(mark.width * scale)), max(1, round(mark.height * scale)))
    scaled = mark.resize(size, Image.LANCZOS)
    canvas.alpha_composite(scaled, ((width - size[0]) // 2, (height - size[1]) // 2))
    return canvas


def build(out_dir: Path) -> list[Path]:
    mark = Image.open(SOURCE).convert("RGBA")
    mark = mark.crop(mark.getbbox() or (0, 0, mark.width, mark.height))
    assets = out_dir / "Assets"
    assets.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    def save(img: Image.Image, name: str) -> None:
        path = assets / name
        img.save(path, optimize=True)
        written.append(path)

    for name, (w, h, share) in TILES.items():
        for scale in SCALES:
            save(_render(mark, w * scale // 100, h * scale // 100, share), f"{name}.scale-{scale}.png")
    for size in TARGET_SIZES:
        img = _render(mark, size, size, 0.98)
        save(img, f"Square44x44Logo.targetsize-{size}.png")
        save(img, f"Square44x44Logo.targetsize-{size}_altform-unplated.png")
    return written


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    files = build(Path(argv[1]))
    print(f"[msix] картинок пакета: {len(files)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
