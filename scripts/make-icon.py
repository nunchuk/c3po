#!/usr/bin/env python3
"""Render assets/c3po.svg into assets/c3po.icns for the launcher .app.

Run with the project venv (it has pyobjc; the system python does not):

    .venv/bin/python scripts/make-icon.py
    .venv/bin/python scripts/make-icon.py --force

Uses only what ships with macOS -- AppKit (NSImage reads SVG directly), sips and
iconutil. No rsvg-convert / inkscape / cairosvg dependency.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from AppKit import (
    NSBitmapImageRep,
    NSCompositingOperationSourceOver,
    NSDeviceRGBColorSpace,
    NSGraphicsContext,
    NSImage,
    NSMakeRect,
    NSPNGFileType,
)

REPO = Path(__file__).resolve().parent.parent
SVG = REPO / "assets" / "c3po.svg"
ICNS = REPO / "assets" / "c3po.icns"

# Master render size; every icon size is downsampled from this one.
MASTER = 1024
# Fraction of the canvas the artwork occupies. macOS app icons are expected to
# sit inside a margin rather than bleed to the edge, and the C-3PO artwork is
# tall and narrow, so without this it looks oversized next to other icons.
INSET = 0.86
# Sizes iconutil expects in an .iconset (each also needs an @2x variant).
SIZES = (16, 32, 128, 256, 512)


def render_png(dest: Path, size: int) -> None:
    """Draw the SVG into a square RGBA PNG, scaled to fit and centred."""
    src = NSImage.alloc().initWithContentsOfFile_(str(SVG))
    if src is None:
        sys.exit(f"ERROR: could not read {SVG} as an image")

    rep = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
        None, size, size, 8, 4, True, False, NSDeviceRGBColorSpace, 0, 0
    )

    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.setCurrentContext_(
        NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
    )
    s = src.size()
    scale = min(size / s.width, size / s.height) * INSET
    w, h = s.width * scale, s.height * scale
    src.drawInRect_fromRect_operation_fraction_(
        NSMakeRect((size - w) / 2, (size - h) / 2, w, h),
        NSMakeRect(0, 0, s.width, s.height),
        NSCompositingOperationSourceOver,
        1.0,
    )
    NSGraphicsContext.restoreGraphicsState()

    data = rep.representationUsingType_properties_(NSPNGFileType, {})
    if data is None or not data.writeToFile_atomically_(str(dest), True):
        sys.exit(f"ERROR: failed to write {dest}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force", action="store_true", help="rebuild even if the .icns is current"
    )
    args = parser.parse_args()

    if not SVG.exists():
        sys.exit(f"ERROR: {SVG} not found")

    if (
        not args.force
        and ICNS.exists()
        and ICNS.stat().st_mtime >= SVG.stat().st_mtime
    ):
        print(f"Up to date: {ICNS}  (--force to rebuild)")
        return

    for tool in ("sips", "iconutil"):
        if shutil.which(tool) is None:
            sys.exit(f"ERROR: {tool} not found (expected on macOS)")

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        master = tmpdir / "master.png"
        render_png(master, MASTER)

        iconset = tmpdir / "c3po.iconset"
        iconset.mkdir()
        for size in SIZES:
            for suffix, px in ((f"{size}x{size}", size), (f"{size}x{size}@2x", size * 2)):
                subprocess.run(
                    ["sips", "-z", str(px), str(px), str(master),
                     "--out", str(iconset / f"icon_{suffix}.png")],
                    check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )

        ICNS.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["iconutil", "-c", "icns", str(iconset), "-o", str(ICNS)], check=True
        )

    print(f"Created: {ICNS}  ({ICNS.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
