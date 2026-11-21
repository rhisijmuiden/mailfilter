"""Shrink the screenshots (256-colour PNG, no dithering): about a third of the size,
no visible difference for interface screenshots. Usage: python3 optimize.py DIR"""
import sys
from pathlib import Path

from PIL import Image

for p in sorted(Path(sys.argv[1] if len(sys.argv) > 1 else "docs/screenshots").glob("*.png")):
    im = Image.open(p)
    if im.mode == "P":
        continue
    im.convert("RGB").quantize(colors=256, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.NONE).save(p, optimize=True)
