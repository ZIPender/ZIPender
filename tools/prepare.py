#!/usr/bin/env python3
"""Turn the source art and fonts into the small files src/build.py embeds.

Run once, or again when a source picture changes; the outputs in src/img and src/fonts are committed, so
the daily GitHub Action never needs this script, Pillow, or the paths below.

    .venv/bin/python tools/prepare.py
"""
import io
import subprocess
import tempfile
import urllib.request
from pathlib import Path

from fontTools.ttLib import TTFont
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
IMG = ROOT / "src" / "img"
FONTS = ROOT / "src" / "fonts"

PERSO = Path("/mnt/slow/projects/perso")
SYSTEM_FONTS = Path("/usr/share/fonts/TTF")
AVATAR_URL = "https://github.com/ZIPender.png?size=460"
# "New textures" from the Cobblemon Expeditions Modrinth gallery: every board tier, in game.
BOARDS_URL = "https://cdn.modrinth.com/data/HtRy1shF/images/bea23f7e67bb385ce6966fabd5f6525ec97a636a.png"
DEMO_GIF = PERSO / "3ds-projects/pkmn-randomizer-3ds-release/media/demo.gif"
# Frames of the demo GIF (as ImageMagick coalesces them) shown by the 3DS card, in order: pick a game, pick a
# style, roll a seed, randomizing at 30 % and 73 %, game ready, title screen. The first is also the still frame.
DEMO_FRAMES = [12, 20, 28, 76, 80, 84, 0]


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "ZIPender-profile/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return Image.open(io.BytesIO(r.read()))


def save(im, name, **kw):
    out = IMG / name
    im.save(out, "WEBP", method=6, **kw)
    print(f"  {name:28} {im.size[0]}x{im.size[1]}  {out.stat().st_size / 1024:.1f} KB")


def cover(im, w, h, focus=(0.5, 0.5)):
    """Scale `im` to fill w x h and crop the overflow around `focus` (fractions of the source)."""
    s = max(w / im.width, h / im.height)
    im = im.resize((round(im.width * s), round(im.height * s)), Image.LANCZOS)
    x = min(max(round(im.width * focus[0] - w / 2), 0), im.width - w)
    y = min(max(round(im.height * focus[1] - h / 2), 0), im.height - h)
    return im.crop((x, y, x + w, y + h))


def pixel_upscale(im, factor):
    return im.resize((im.width * factor, im.height * factor), Image.NEAREST)


def images():
    IMG.mkdir(parents=True, exist_ok=True)
    print("images")
    save(fetch(AVATAR_URL).convert("RGBA").resize((440, 440), Image.LANCZOS), "avatar.webp", quality=86)  # keeps its transparent background

    boards = fetch(BOARDS_URL).convert("RGB")
    save(cover(boards, 1040, 500, focus=(0.5, 0.42)), "cobblemon-boards.webp", quality=78)
    save(pixel_upscale(Image.open(PERSO / "Cobblemon_Expeditions/icon.png").convert("RGBA"), 1),
         "cobblemon-icon.webp", lossless=True)

    brand = PERSO / "Shadowbound/assets/brand"
    save(cover(Image.open(brand / "screenshots/16-the-soul-enters.jpg").convert("RGB"), 866, 420, focus=(0.42, 0.42)),
         "shadowbound-soul.webp", quality=78)
    save(Image.open(brand / "icon.png").convert("RGB").resize((128, 128), Image.LANCZOS), "shadowbound-icon.webp",
         quality=90)

    media = PERSO / "3ds-projects/pkmn-randomizer-3ds-release/media"
    save(pixel_upscale(Image.open(media / "icon.png").convert("RGBA"), 3), "randomizer-icon.webp", lossless=True)
    with tempfile.TemporaryDirectory() as tmp:
        # Pillow composites partial GIF frames over the wrong background, so let ImageMagick coalesce them.
        subprocess.run(["magick", str(DEMO_GIF), "-coalesce", f"{tmp}/f_%03d.png"], check=True)
        for n, index in enumerate(DEMO_FRAMES):
            frame = Image.open(f"{tmp}/f_{index:03d}.png").convert("RGB")  # 800 x 980: both screens at 2x
            save(frame.resize((400, 490), Image.LANCZOS), f"randomizer-{n}.webp", quality=80)


def fonts():
    """Ship the two weights the SVGs use as WOFF2. build.py subsets them per picture."""
    FONTS.mkdir(parents=True, exist_ok=True)
    print("fonts")
    for weight in ("Regular", "Bold"):
        src = SYSTEM_FONTS / f"JetBrainsMonoNLNerdFontMono-{weight}.ttf"
        font = TTFont(src)
        font.flavor = "woff2"
        out = FONTS / f"JetBrainsMonoNLNerdFontMono-{weight}.woff2"
        font.save(out)
        print(f"  {out.name:44} {out.stat().st_size / 1024:.0f} KB")


if __name__ == "__main__":
    images()
    fonts()
