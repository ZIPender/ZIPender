#!/usr/bin/env python3
"""Turn the source art and fonts into the small files src/build.py embeds.

Run once, or again when a source picture changes; the outputs in src/img and src/fonts are committed, so
the daily GitHub Action never needs this script, Pillow, or the paths below.

    .venv/bin/python tools/prepare.py
"""
import io
import json
import math
import subprocess
import tempfile
import urllib.request
from pathlib import Path

from fontTools.ttLib import TTFont
from PIL import Image, ImageChops, ImageFilter

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
# The Shadowbound card plays this recording (1280 x 720, 25 fps): the Stone Saint's Shadow woven from sparks,
# standing, then unravelling. Frames are numbered from 1 as ffmpeg writes them; the first is the empty courtyard.
SUMMON = PERSO / "Shadowbound/assets/brand/gifs/stone-saint-summon-recall.mp4"
SUMMON_FPS = 25
SUMMON_CROP = (61, 214, 1161, 670)  # x0, y0, x1, y1: the card's 434 x 180 art shape around the Saint
SUMMON_STEP = 2  # every other frame: 12.5 fps
SUMMON_PAUSE = 1.0  # seconds of empty courtyard between two summons
SUMMON_STILL = 60  # the frame shown when animations are off: the Saint standing, eyes lit
# The Saint's two eyes, recoloured from violet to red while they are lit, and the teal flash that lights them.
EYES = ((615.5, 296), (633.5, 296))
EYES_LIT = range(47, 80)
EYES_FLASH = range(43, 51)


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
    save(Image.open(brand / "icon.png").convert("RGB").resize((128, 128), Image.LANCZOS), "shadowbound-icon.webp",
         quality=90)
    summon()

    media = PERSO / "3ds-projects/pkmn-randomizer-3ds-release/media"
    save(pixel_upscale(Image.open(media / "icon.png").convert("RGBA"), 3), "randomizer-icon.webp", lossless=True)
    with tempfile.TemporaryDirectory() as tmp:
        # Pillow composites partial GIF frames over the wrong background, so let ImageMagick coalesce them.
        subprocess.run(["magick", str(DEMO_GIF), "-coalesce", f"{tmp}/f_%03d.png"], check=True)
        for n, index in enumerate(DEMO_FRAMES):
            frame = Image.open(f"{tmp}/f_{index:03d}.png").convert("RGB")  # 800 x 980: both screens at 2x
            save(frame.resize((400, 490), Image.LANCZOS), f"randomizer-{n}.webp", quality=80)


def summon(size=(868, 360), columns=12):
    """The summon recording as the card plays it: the empty courtyard as a still, the part of each frame that
    differs from it as one sheet (everything else transparent, repeated frames kept once), and the timing in
    shadowbound-summon.json. `size` is twice the card's art, the sheet's cells are at the same scale."""
    k = size[0] / (SUMMON_CROP[2] - SUMMON_CROP[0])
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["ffmpeg", "-v", "error", "-i", str(SUMMON), f"{tmp}/f_%03d.png"], check=True)
        count = len(list(Path(tmp).glob("f_*.png")))

        def frame(i):
            im = Image.open(f"{tmp}/f_{i:03d}.png").convert("RGB")
            if i in EYES_FLASH:
                red_flash(im)
            if i in EYES_LIT:
                red_eyes(im)
            return im.crop(SUMMON_CROP).resize(size, Image.LANCZOS)

        scene = frame(1)
        shown = list(range(2, count + 1, SUMMON_STEP))
        frames = [frame(i) for i in shown]

    def moved(a, b, level):
        return ImageChops.difference(a, b).convert("L").point(lambda v: 255 if v > level else 0)

    union = Image.new("L", size)
    for f in frames:
        union = ImageChops.lighter(union, moved(f, scene, 10))
    x0, y0, x1, y1 = union.getbbox()
    box = (max(x0 - 4, 0), max(y0 - 4, 0), min(x1 + 4, size[0]), min(y1 + 4, size[1]))
    back = scene.crop(box)
    cells, sequence, last = [], [], None
    for f in frames:
        cell = f.crop(box)
        mask = moved(cell, back, 10).filter(ImageFilter.MaxFilter(5))
        if not mask.getbbox():
            sequence.append(None)
            continue
        if last is None or moved(cell, last, 12).histogram()[255] >= 100:
            cell.putalpha(mask)
            cells.append(cell)
            last = cell.convert("RGB")
        sequence.append(len(cells) - 1)
    sequence += [None] * round(SUMMON_PAUSE * SUMMON_FPS / SUMMON_STEP)

    cw, ch = box[2] - box[0], box[3] - box[1]
    sheet = Image.new("RGBA", (columns * cw, math.ceil(len(cells) / columns) * ch))
    for n, cell in enumerate(cells):
        sheet.paste(cell, (n % columns * cw, n // columns * ch))
    save(scene, "shadowbound-scene.webp", quality=78)
    save(sheet, "shadowbound-summon.webp", quality=70)

    runs = []  # [first slot, number of slots, cell]
    for slot, cell in enumerate(sequence):
        if cell is None:
            continue
        if runs and runs[-1][2] == cell and sum(runs[-1][:2]) == slot:
            runs[-1][1] += 1
        else:
            runs.append([slot, 1, cell])

    def slot(i):
        return (i - shown[0]) // SUMMON_STEP

    loop = {
        "scene": "shadowbound-scene.webp", "sheet": "shadowbound-summon.webp", "size": list(size),
        "box": [box[0], box[1], cw, ch], "columns": columns, "slot": SUMMON_STEP / SUMMON_FPS,
        "slots": len(sequence), "runs": runs, "still": slot(SUMMON_STILL),
        "eyes": [[round((x - SUMMON_CROP[0]) * k, 1), round((y - SUMMON_CROP[1]) * k, 1)] for x, y in EYES],
        "lit": [slot(min(i for i in shown if i in EYES_FLASH)), slot(max(i for i in shown if i in EYES_LIT)) + 1],
    }
    (IMG / "shadowbound-summon.json").write_text(json.dumps(loop) + "\n")
    print(f"  shadowbound-summon.json       {len(cells)} cells of {cw}x{ch}, {len(runs)} runs, {len(sequence)} slots")


def red_eyes(im):
    """The Saint's violet eyes, teal corners included, as hot red, in place."""
    px = im.load()
    for y in range(round(EYES[0][1]) - 5, round(EYES[0][1]) + 6):
        for x in range(round(EYES[0][0]) - 8, round(EYES[1][0]) + 8):
            r, g, b = px[x, y]
            v = max(r, g, b)
            t = min(max((v - 36) / 40, 0), 1)  # the body round them is darker than 36
            hot = max(v - 130, 0)
            red = (min(255, 1.9 * v), .18 * v + hot, .22 * v + .6 * hot)
            px[x, y] = tuple(round(c + (e - c) * t) for c, e in zip((r, g, b), red))


def red_flash(im, radius=90):
    """The teal flash that lights the eyes as a red one, in place: teal light turns red, its white core stays."""
    cx, cy = (EYES[0][0] + EYES[1][0]) / 2, EYES[0][1]
    px = im.load()
    for y in range(round(cy - radius), round(cy + radius)):
        for x in range(round(cx - radius), round(cx + radius)):
            d = math.hypot(x - cx, y - cy) / radius
            r, g, b = px[x, y]
            t = min(max((min(g, b) - r) / 50, 0), 1) * max(1 - d * d, 0)
            red = (max(g, b), .6 * r, .6 * r)
            px[x, y] = tuple(round(c + (e - c) * t) for c, e in zip((r, g, b), red))


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
