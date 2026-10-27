#!/usr/bin/env python3
"""Render the profile: assets/*.svg and README.md, from profile.toml and the numbers in data/stats.json.

    python src/build.py            # fetch fresh numbers (PROFILE_TOKEN or GITHUB_TOKEN), then render
    python src/build.py --offline  # render from the cached numbers only

Everything is drawn in the look of my desktop: Konsole's Obsidian Purple scheme, the starship prompt, fastfetch,
btop and eza from zsh-config, framed like spectacle-border's albedo-yae screenshots. Each SVG embeds the subset of
JetBrains Mono Nerd Font it uses, so it renders the same everywhere and needs nothing from the network.
"""
import argparse
import base64
import datetime as dt
import html
import io
import json
import math
import re
import textwrap
import tomllib
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from fontTools import subset
from fontTools.ttLib import TTFont

import data

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
OUT = ROOT / "assets"

# Obsidian Purple (Konsole) plus the starship, btop and eza colours from zsh-config.
C = {
    "bg": "#111014", "bg2": "#17151B", "bg3": "#1D1A22", "line": "#2A252F", "pill": "#3A3342",
    "fg": "#E7E2EA", "faint": "#918799", "dim": "#746D7D", "ghost": "#69636F",
    "frame": "#5D4770", "purple": "#BD63FF", "lilac": "#D89AFF", "violet": "#A970FF", "dir": "#D6B5F5",
    "red": "#FF6B81", "green": "#75D6A5", "yellow": "#E6C875", "blue": "#7297FF", "sky": "#8FAAFF",
    "cyan": "#70CFE3", "eza_dir": "#8FAAFF", "box": "#BD63FF", "box2": "#8B6AA6", "title": "#D6B5F5",
}
TERM_COLORS = [  # Color0-7 and their Intense variants, for fastfetch's colour blocks
    ["#17151B", "#FF6B81", "#75D6A5", "#E6C875", "#7297FF", "#BC61FF", "#70CFE3", "#D9D4DE"],
    ["#69636F", "#FF8A9E", "#92E3B8", "#F1D78E", "#8FAAFF", "#D89AFF", "#8DE0EE", "#FFFFFF"],
]
KEY_COLORS = ["#D89AFF", "#BD63FF", "#A970FF", "#70CFE3", "#8FAAFF"]  # fastfetch keyColor, in config order
BAND = ["#F59AC6", "#A866FF", "#3A1863"]  # spectacle-border albedo-yae: sakura -> electro -> abyss, 35 degrees
GLOW = ["#F28CC4", "#A35CFF", "#5E2AA8"]
CPU = ["#5D4770", "#A970FF", "#D89AFF"]  # btop cpu_start / mid / end
USED = ["#6E4783", "#BC61FF", "#E879F9"]  # btop used_start / mid / end

W = 880  # every picture is drawn at this width, so they all scale alike in the README
FONT = {400: SRC / "fonts/JetBrainsMonoNLNerdFontMono-Regular.woff2",
        700: SRC / "fonts/JetBrainsMonoNLNerdFontMono-Bold.woff2"}
CW = 0.6  # advance width of one cell, in em


# ---------------------------------------------------------------------------------------------- fonts and helpers

@lru_cache(None)
def _font_bytes(weight):
    return FONT[weight].read_bytes()


@lru_cache(None)
def _glyphs():
    return {name: chr(cp) for cp, name in TTFont(FONT[400]).getBestCmap().items()}


def nf(name):
    """A Nerd Font glyph by its name, e.g. nf("md-pokeball")."""
    try:
        return _glyphs()[name]
    except KeyError:
        raise SystemExit(f"profile.toml: no Nerd Font glyph named {name!r}") from None


def woff2(weight, chars):
    font = TTFont(io.BytesIO(_font_bytes(weight)))
    opts = subset.Options()
    opts.flavor = "woff2"
    opts.layout_features = []
    opts.hinting = False
    opts.notdef_outline = True
    opts.drop_tables += ["PfEd"]  # FontForge metadata in the Nerd Fonts
    sub = subset.Subsetter(opts)
    sub.populate(unicodes={ord(c) for c in chars} | {0x20})
    sub.subset(font)
    buf = io.BytesIO()
    font.save(buf)
    return buf.getvalue()


def b64(raw):
    return base64.b64encode(raw).decode()


@lru_cache(None)
def img(name):
    return "data:image/webp;base64," + b64((SRC / "img" / name).read_bytes())


def esc(s):
    return html.escape(str(s), quote=True)


def mix(a, b, t):
    a, b = [int(a[i:i + 2], 16) for i in (1, 3, 5)], [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02X}" for x, y in zip(a, b))


def ramp(stops, t):
    """Colour at t (0..1) along a multi-stop gradient."""
    t = min(max(t, 0), 1) * (len(stops) - 1)
    i = min(int(t), len(stops) - 2)
    return mix(stops[i], stops[i + 1], t - i)


BASE_CSS = """
text{font-family:J,'JetBrainsMono Nerd Font','JetBrains Mono',ui-monospace,monospace;fill:#E7E2EA;white-space:pre}
.fx{transform-box:fill-box}
@keyframes in{from{opacity:0}to{opacity:1}}
@keyframes out{from{opacity:1}to{opacity:0}}
@keyframes blink{0%,49.9%{opacity:1}50%,100%{opacity:0}}
@keyframes rise{from{transform:scaleY(0)}to{transform:scaleY(1)}}
@keyframes sweep{from{clip-path:inset(0 100% 0 0)}to{clip-path:inset(0 0 0 0)}}
@media (prefers-reduced-motion:reduce){*{animation:none!important}.typing{display:none}}
"""


class Svg:
    """A picture under construction: defs, body, CSS, and which characters each font weight must carry."""

    def __init__(self, w, h, label):
        self.w, self.h, self.label = w, h, label
        self.defs, self.body, self.css = [], [], [BASE_CSS]
        self.chars = {400: set(), 700: set()}
        self._n = 0

    def id(self, stem):
        self._n += 1
        return f"{stem}{self._n}"

    def add(self, *parts):
        self.body.extend(parts)

    def text(self, x, y, spans, size=14, anchor=None, attrs=""):
        """`spans` are strings or (text, colour, bold, extra-attributes) tuples, shorter tuples allowed."""
        out = []
        for sp in spans:
            sp = (sp,) if isinstance(sp, str) else tuple(sp)
            t, color, bold, extra = (*sp, *(None, False, "")[len(sp) - 1:])
            if not t:
                continue
            self.chars[700 if bold else 400].update(t)
            a = (f' fill="{color}"' if color else "") + (' font-weight="700"' if bold else "") + (f" {extra}" if extra else "")
            out.append(f"<tspan{a}>{esc(t)}</tspan>")
        anchor = f' text-anchor="{anchor}"' if anchor else ""
        return f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size:g}"{anchor}{" " + attrs if attrs else ""}>{"".join(out)}</text>'

    def gradient(self, stops, x, y, w, h, angle=0, opacities=None):
        """A userSpace linear gradient across the box, `angle` in degrees (0 = left to right, 90 = top down)."""
        gid = self.id("g")
        a = math.radians(angle)
        cx, cy = x + w / 2, y + h / 2
        half = abs(w / 2 * math.cos(a)) + abs(h / 2 * math.sin(a))
        dx, dy = half * math.cos(a), half * math.sin(a)
        stops_xml = "".join(
            f'<stop offset="{i / (len(stops) - 1):.3f}" stop-color="{c}"'
            + (f' stop-opacity="{opacities[i]}"' if opacities else "") + "/>"
            for i, c in enumerate(stops))
        self.defs.append(f'<linearGradient id="{gid}" gradientUnits="userSpaceOnUse" x1="{cx - dx:.1f}" '
                         f'y1="{cy - dy:.1f}" x2="{cx + dx:.1f}" y2="{cy + dy:.1f}">{stops_xml}</linearGradient>')
        return f"url(#{gid})"

    def save(self, name):
        faces = "".join(
            f"@font-face{{font-family:J;font-weight:{w};"
            f"src:url(data:font/woff2;base64,{b64(woff2(w, chars))}) format('woff2')}}"
            for w, chars in self.chars.items() if chars)
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
               f'viewBox="0 0 {self.w} {self.h}" width="{self.w}" height="{self.h}" role="img" '
               f'aria-label="{esc(self.label)}" xml:space="preserve"><title>{esc(self.label)}</title>'
               f"<style>{faces}{''.join(self.css)}</style><defs>{''.join(self.defs)}</defs>"
               f"{''.join(self.body)}</svg>\n")
        path = OUT / name
        path.write_text(svg)
        print(f"  {name:36} {self.w}x{self.h}  {len(svg.encode()) / 1024:6.1f} KB")


# ------------------------------------------------------------------------------------------------- terminal parts

class Grid:
    """Monospace cell positions inside a terminal area."""

    def __init__(self, x, y, size=14, lh=21):
        self.x, self.y, self.size, self.lh = x, y, size, lh
        self.cw = size * CW

    def cx(self, col):
        return self.x + col * self.cw

    def top(self, row):
        return self.y + row * self.lh

    def base(self, row):
        return self.y + row * self.lh + self.lh * 0.5 + self.size * 0.36


def prompt_top(path):
    """Starship's first prompt line: ╭─<dir> <branch>."""
    return [("╭─", C["frame"]), (path, C["dir"], True), ("  "), (nf("pl-branch") + " main", C["purple"], True)]


def typed_command(svg, g, row, command, start, speed=0.055, blink_from=None):
    """`╰─❯ command`, typed one character at a time from `start` seconds; returns when the typing ends.

    zsh-syntax-highlighting colours the command word green and underlines paths. With `blink_from` the cursor
    stays and blinks after typing; otherwise it disappears when the next output appears (at the returned time).
    """
    svg.add(svg.text(g.cx(0), g.base(row), [("╰─❯", C["purple"], True)], g.size))
    word, _, rest = command.partition(" ")
    spans = [(word, C["green"])]
    for part in re.split(r"(\s+)", " " + rest if rest else ""):
        spans.append((part, None, False, 'text-decoration="underline"') if part.startswith(("~", "/", "./")) else (part,))
    col = 4
    n = len(command)
    end = start + n * speed
    # The clip opens one cell per step to "type" the command; without animation support it is simply shown.
    svg.add(svg.text(g.cx(col), g.base(row), spans, g.size,
                     attrs=f'style="animation:sweep {n * speed:.2f}s steps({n}) {start:.2f}s both"'))
    svg.css.append(f"@keyframes move{n}{{from{{transform:translateX(0)}}to{{transform:translateX({n * g.cw:.1f}px)}}}}")
    anim = f"move{n} {n * speed:.2f}s steps({n}) {start:.2f}s both"
    if blink_from is not None:
        anim += f",blink 1.1s steps(1) {blink_from:.2f}s infinite"
    else:
        anim += f",out .01s {end + 0.35:.2f}s both"
    svg.add(f'<rect class="fx typing" x="{g.cx(col):.1f}" y="{g.top(row) + 2:.1f}" width="{g.cw:.1f}" '
            f'height="{g.lh - 4}" fill="{C["fg"]}" style="animation:{anim}"/>')
    return end + 0.35


def reveal(at):
    return f'style="animation:in .12s {at:.2f}s both"'


def flip(svg, start, length, slots, slot):
    """The style that shows an element for `length` of every `slots` slots of `slot` seconds, from slot `start`.

    A picture's flips share one cycle, each offset by a negative delay, so the loop is already running when the
    picture appears."""
    name = f"show{length}of{slots}"
    pct = 100 * length / slots
    rule = f"@keyframes {name}{{0%,{pct - .01:.2f}%{{opacity:1}}{pct:.2f}%,100%{{opacity:0}}}}"
    if rule not in svg.css:
        svg.css.append(rule)
    period = slots * slot
    return f'style="animation:{name} {period:.2f}s steps(1) {start * slot - period:.2f}s infinite"'


def right_clock(svg, g, row, cols, clock):
    svg.add(svg.text(g.cx(cols), g.base(row), [(clock, C["dim"])], g.size, anchor="end"))


def panel(svg, x, y, w, h, r=10):
    svg.add(f'<rect x="{x + .5}" y="{y + .5}" width="{w - 1}" height="{h - 1}" rx="{r}" fill="{C["bg"]}" '
            f'stroke="{C["line"]}"/>')


# ------------------------------------------------------------------------------- spectacle-border albedo-yae frame

@lru_cache(None)
def _ornament(name):
    s = re.sub(r"<!--.*?-->", "", (SRC / "art" / f"{name}.svg").read_text(), flags=re.S)
    vb = re.search(r'viewBox="([^"]+)"', s).group(1)
    return vb, s[s.index(">", s.index("<svg")) + 1:s.rindex("</svg>")]


def ornament(svg, name, corner, anchor, width):
    vb, inner = _ornament(name)
    p = svg.id(name)
    inner = re.sub(r'id="([^"]+)"', rf'id="{p}-\1"', inner)
    inner = re.sub(r"url\(#([^)]+)\)", rf"url(#{p}-\1)", inner)
    inner = re.sub(r'href="#([^"]+)"', rf'href="#{p}-\1"', inner)
    vx, vy, vw, vh = map(float, vb.split())
    s = width / vw
    x, y = corner[0] - (anchor[0] - vx) * s, corner[1] - (anchor[1] - vy) * s
    svg.add(f'<svg x="{x:.1f}" y="{y:.1f}" width="{vw * s:.1f}" height="{vh * s:.1f}" viewBox="{vb}" '
            f'overflow="visible">{inner}</svg>')


PETAL = "M0 0 C -9 -5 -12 -17 -5 -24 Q -2 -23 0 -20 Q 2 -23 5 -24 C 12 -17 9 -5 0 0 Z"


def petals(svg, x, y, drifts):
    """Loose sakura petals that drift right and down from (x, y), fading in and out, forever."""
    grad = svg.gradient(["#E880BF", "#FFD9EC"], -12, -24, 24, 24, angle=-90)
    for i, (dx, dy, scale, dur, delay, spin) in enumerate(drifts):
        k = svg.id("petal")
        svg.css.append(
            f"@keyframes {k}{{0%{{transform:translate(0,0) rotate(0deg) scale({scale});opacity:0}}"
            f"12%{{opacity:.9}}75%{{opacity:.75}}100%{{transform:translate({dx}px,{dy}px) rotate({spin}deg) "
            f"scale({scale});opacity:0}}}}")
        svg.add(f'<g transform="translate({x + i * 14:.1f} {y + (i % 2) * 8:.1f})"><path class="fx" d="{PETAL}" '
                f'fill="{grad}" stroke="#E9C77F" stroke-width="1.1" style="transform-origin:center;opacity:0;'
                f'animation:{k} {dur}s linear {delay}s infinite"/></g>')


def frame_back(svg, x, y, w, h, band=5):
    """Glow and gradient band of spectacle-border, around the capture rectangle (x, y, w, h)."""
    bx, by, bw, bh = x - band, y - band, w + 2 * band, h + 2 * band
    blur = svg.id("blur")
    svg.defs.append(f'<filter id="{blur}" x="-20%" y="-30%" width="140%" height="160%">'
                    f'<feGaussianBlur stdDeviation="9"/></filter>')
    svg.css.append("@keyframes breathe{0%,100%{opacity:.78}50%{opacity:1}}")
    svg.add(f'<rect x="{bx}" y="{by}" width="{bw}" height="{bh}" fill="{svg.gradient(GLOW, bx, by, bw, bh, 35)}" '
            f'filter="url(#{blur})" style="animation:breathe 7s ease-in-out infinite"/>',
            f'<rect x="{bx}" y="{by}" width="{bw}" height="{bh}" fill="{svg.gradient(BAND, bx, by, bw, bh, 35)}"/>',
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{C["bg"]}"/>')


def frame_front(svg, x, y, w, h, band=5, size=190):
    """The corner art: Yae's sakura top-left, Albedo's feathers bottom-right, as spectacle-border places them."""
    ornament(svg, "yae", (x - band, y - band), (70, 70), size)
    petals(svg, x + 120, y - 2, [(420, 70, .62, 13, 0, 260), (360, 95, .5, 11, 4.5, -220),
                                 (480, 60, .42, 15, 8, 300)])
    ornament(svg, "albedo", (x + w + band, y + h + band), (210, 80), size * 0.95)


# ---------------------------------------------------------------------------------------------------- the numbers

class Numbers:
    def __init__(self, stats, profile):
        self.stats, self.profile = stats, profile
        gh = stats.get("github", {})
        self.days = gh.get("days", [])
        tz = ZoneInfo(profile.get("timezone", "UTC"))
        self.updated = dt.datetime.fromisoformat(stats.get("updated", dt.datetime.now(dt.timezone.utc).isoformat()))
        self.now = self.updated.astimezone(tz)
        counts = [d["count"] for d in self.days]
        run = self.best_streak = 0
        for c in counts:
            run = run + 1 if c else 0
            self.best_streak = max(self.best_streak, run)
        i = len(counts) - 1
        if i >= 0 and counts[i] == 0:  # today is not over yet
            i -= 1
        self.streak = 0
        while i >= 0 and counts[i]:
            self.streak, i = self.streak + 1, i - 1
        self.best_day = max(self.days, key=lambda d: d["count"]) if self.days else {"date": "", "count": 0}
        self.weekdays = [0] * 7
        for d in self.days:
            self.weekdays[dt.date.fromisoformat(d["date"]).weekday()] += d["count"]
        self.values = {
            "contributions": f"{gh.get('contributions', 0):,}",
            "uptime": self._age(gh.get("created_at")),
            "streak": str(self.streak), "best_streak": str(self.best_streak),
            "public_repos": str(gh.get("public_repos", 0)),
        }

    def _age(self, created):
        if not created:
            return "?"
        c = dt.datetime.fromisoformat(created.replace("Z", "+00:00"))
        months = (self.now.year - c.year) * 12 + self.now.month - c.month - (self.now.day < c.day)
        y, m = divmod(max(months, 0), 12)
        parts = ([f"{y} year{'s' * (y != 1)}"] if y else []) + ([f"{m} month{'s' * (m != 1)}"] if m else [])
        return ", ".join(parts) or "a few days"

    def fill(self, text):
        """`text` with its {placeholders} filled, or None when one of them has no number yet.

        A placeholder can add up projects, each part naming its own key or keeping the one before it:
        {downloads:modrinth/<slug>+curseforge/<id>}, {followers:modrinth/<slug>+favorites:curseforge/<id>}. Parts
        without a number yet are skipped, so the sum is only missing when none of them has one."""
        missing = False

        def sub(m):
            nonlocal missing
            key, _, arg = m.group(1).partition(":")
            if "/" in arg:  # {downloads:modrinth/<slug>}, {version:curseforge/<id>}, or a sum of them
                parts = []
                for part in arg.split("+"):
                    if ":" in part:
                        key, _, part = part.partition(":")
                    site, _, project = part.partition("/")
                    parts.append(self.stats.get(site, {}).get(project, {}).get(key))
                known = [v for v in parts if v is not None]
                value = (sum(known) if len(parts) > 1 else known[0]) if known else None
            elif key in ("stars", "release"):
                value = self.stats.get("github", {}).get({"stars": "stars", "release": "releases"}[key], {}).get(arg)
            else:
                value = self.values.get(key)
            if value is None:
                missing = True
                return ""
            return f"{value:,}" if isinstance(value, int) else str(value)

        out = re.sub(r"\{([a-z_]+(?::[^}]+)?)\}", sub, text)
        return None if missing else out

    @property
    def clock(self):
        return self.now.strftime("%H:%M")


# ------------------------------------------------------------------------------------------------------- pictures

def header(p, n):
    """fastfetch in Konsole, framed like a spectacle-border screenshot."""
    lines = fetch_lines(p, n)
    rows_fetch = max(2 + len(lines) + 3, 13)  # title, separator, lines, blank, two rows of colour blocks
    g = Grid(0, 0)
    pad_x, pad_y = 18, 14
    rows = 2 + rows_fetch + 1 + 2
    mx, my = 52, 50
    x, y, w = mx + 5, my + 5, W - 2 * (mx + 5)
    h = pad_y * 2 + rows * g.lh
    svg = Svg(W, h + 2 * (my + 5), f"{p['user']}@{p['host']}: fastfetch")
    g = Grid(x + pad_x, y + pad_y)
    cols = int((w - 2 * pad_x) / g.cw)

    frame_back(svg, x, y, w, h)
    svg.add(svg.text(g.cx(0), g.base(0), prompt_top(p["cwd"]), g.size))
    t = typed_command(svg, g, 1, "fastfetch", start=0.5)
    right_clock(svg, g, 1, cols, n.clock)

    # fastfetch: the kitty logo, then the info column two cells to its right
    logo = 13 * g.lh - 10
    lx, ly = g.cx(0), g.top(2) + 4
    halo = svg.id("halo")
    svg.defs.append(f'<radialGradient id="{halo}"><stop offset="0" stop-color="{C["purple"]}" stop-opacity=".30"/>'
                    f'<stop offset=".55" stop-color="{C["red"]}" stop-opacity=".07"/>'
                    f'<stop offset="1" stop-color="{C["red"]}" stop-opacity="0"/></radialGradient>')
    out = [f'<g {reveal(t)}>',
           f'<ellipse cx="{lx + logo / 2:.1f}" cy="{ly + logo * .48:.1f}" rx="{logo * .55:.1f}" ry="{logo * .5:.1f}" '
           f'fill="url(#{halo})"/>',
           f'<image href="{img("avatar.webp")}" x="{lx}" y="{ly}" width="{logo}" height="{logo}"/>']
    col = math.ceil(logo / g.cw) + 2
    title = f"{p['user']}@{p['host']}"
    out.append(svg.text(g.cx(col), g.base(2), [(p["user"], KEY_COLORS[0], True), ("@"),
                                               (p["host"], KEY_COLORS[0], True)], g.size))
    out.append(svg.text(g.cx(col), g.base(3), [("-" * len(title), C["fg"])], g.size))
    k = 0
    for i, line in enumerate(lines):
        if not line:
            k = 0
            continue
        key, value = line
        if col + 12 + len(value) > cols:
            print(f"  ! header: '{value}' is {col + 12 + len(value) - cols} cells too wide")
        out.append(svg.text(g.cx(col), g.base(4 + i), [(f"{key:<10}", KEY_COLORS[k % len(KEY_COLORS)], True),
                                                       ("  " + value)], g.size))
        k += 1
    blocks = 4 + len(lines) + 1
    for r, row in enumerate(TERM_COLORS):
        for i, c in enumerate(row):
            out.append(f'<rect x="{g.cx(col + i * 3):.1f}" y="{g.top(blocks + r):.1f}" width="{3 * g.cw + .3:.1f}" '
                       f'height="{g.lh + .3}" fill="{c}"/>')
    out.append("</g>")
    svg.add(*out)

    last = 2 + rows_fetch + 1
    svg.add(f'<g {reveal(t + 0.25)}>', svg.text(g.cx(0), g.base(last), prompt_top(p["cwd"]), g.size))
    svg.add(svg.text(g.cx(0), g.base(last + 1), [("╰─❯", C["purple"], True)], g.size))
    ghost = "eza --icons ~/projects"
    svg.add(svg.text(g.cx(4), g.base(last + 1), [(ghost, C["ghost"])], g.size))
    svg.add(f'<rect x="{g.cx(4):.1f}" y="{g.top(last + 1) + 2:.1f}" width="{g.cw:.1f}" height="{g.lh - 4}" '
            f'fill="{C["fg"]}" style="animation:blink 1.1s steps(1) {t + 0.25:.2f}s infinite"/>')
    right_clock(svg, g, last + 1, cols, n.clock)
    svg.add("</g>")

    frame_front(svg, x, y, w, h)
    svg.save("header.svg")


def fetch_lines(p, n):
    """profile.toml's fetch lines with their numbers filled; lines without a number yet are left out."""
    lines = [(line[0], n.fill(line[1])) if line else () for line in p["fetch"]["lines"]]
    return [line for line in lines if not line or line[1] is not None]


def prompt_strip(name, path, command, label, n):
    """A slim terminal strip with a typed command, used as a section title."""
    g = Grid(18, 13)
    svg = Svg(W, 2 * g.lh + 26, label)
    panel(svg, 0, 0, W, svg.h)
    cols = int((W - 36) / g.cw)
    svg.add(svg.text(g.cx(0), g.base(0), prompt_top(path), g.size))
    typed_command(svg, g, 1, command, start=0.3, blink_from=0.3 + len(command) * 0.055 + 0.2)
    right_clock(svg, g, 1, cols, n.clock)
    svg.save(name)


def wrap(text, cols):
    return textwrap.wrap(text, cols, break_on_hyphens=False)


def card_base(svg, p, h, n):
    """Card body, accent border, Konsole-like tab bar with the project's folder and a version badge."""
    w, acc = svg.w, p["accent"]
    clip = svg.id("card")
    svg.defs.append(f'<clipPath id="{clip}"><rect x=".5" y=".5" width="{w - 1}" height="{h - 1}" rx="12"/></clipPath>')
    svg.add(f'<rect x=".5" y=".5" width="{w - 1}" height="{h - 1}" rx="12" fill="{C["bg"]}"/>')
    svg.add(f'<rect x="0" y="0" width="{w}" height="34" fill="{C["bg2"]}" clip-path="url(#{clip})"/>',
            f'<line x1="0" y1="34" x2="{w}" y2="34" stroke="{C["line"]}"/>')
    folder = nf("custom-folder")
    svg.add(svg.text(16, 22, [(folder + " ", C["eza_dir"]), (p["dir"], C["dir"], True), ("  "),
                              (nf("pl-branch") + " main", C["purple"], True)], 12.5))
    badge = n.fill(p.get("badge", ""))
    if badge:
        bw = len(badge) * 7.2 + 18
        svg.add(f'<rect x="{w - bw - 12:.1f}" y="8.5" width="{bw:.1f}" height="18" rx="9" fill="{acc}" '
                f'fill-opacity=".12" stroke="{acc}" stroke-opacity=".55"/>',
                svg.text(w - 12 - bw / 2, 21.5, [(badge, acc, True)], 12, anchor="middle"))
    return clip


def card_border(svg, p, h):
    w, acc = svg.w, p["accent"]
    stroke = svg.gradient([acc, C["frame"], C["line"]], 0, 0, w, h, 25)
    svg.add(f'<rect x=".75" y=".75" width="{w - 1.5}" height="{h - 1.5}" rx="12" fill="none" stroke="{stroke}" '
            f'stroke-width="1.5"/>')


def icon_tile(svg, p, x, y, s):
    clip = svg.id("icon")
    svg.defs.append(f'<clipPath id="{clip}"><rect x="{x}" y="{y}" width="{s}" height="{s}" rx="12"/></clipPath>')
    pixel = ' style="image-rendering:pixelated"' if "randomizer" in p["icon"] or "cobblemon" in p["icon"] else ""
    svg.add(f'<rect x="{x - 3}" y="{y - 3}" width="{s + 6}" height="{s + 6}" rx="14" fill="{C["bg"]}"/>',
            f'<rect x="{x}" y="{y}" width="{s}" height="{s}" rx="12" fill="{C["bg3"]}"/>',
            f'<image href="{img(p["icon"])}" x="{x}" y="{y}" width="{s}" height="{s}" clip-path="url(#{clip})"{pixel}/>',
            f'<rect x="{x + .5}" y="{y + .5}" width="{s - 1}" height="{s - 1}" rx="12" fill="none" '
            f'stroke="{p["accent"]}" stroke-opacity=".6"/>')


def pills(svg, p, x, y):
    for i, tag in enumerate(p["tags"]):
        tw = len(tag) * 6.9 + (30 if i == 0 else 20)
        svg.add(f'<rect x="{x:.1f}" y="{y}" width="{tw:.1f}" height="22" rx="11" fill="{C["bg3"]}" '
                f'stroke="{C["pill"]}"/>')
        tx = x + 10
        if i == 0:  # the language gets the project's accent dot
            svg.add(f'<circle cx="{x + 13}" cy="{y + 11}" r="4" fill="{p["accent"]}"/>')
            tx += 10
        svg.add(svg.text(tx, y + 15, [(tag, C["fg"] if i == 0 else C["faint"])], 11.5))
        x += tw + 7


def stat_row(svg, p, x, y, n, size=13, most=2):
    """The first `most` stats whose numbers are known, as icon + bold number + label."""
    shown = [(icon, n.fill(text)) for icon, text in p["stats"]]
    spans = []
    for i, (icon, text) in enumerate([s for s in shown if s[1] is not None][:most]):
        num, _, rest = text.partition(" ")
        spans += [("   " if i else ""), (nf(icon) + " ", p["accent"]), (num, C["fg"], True), (" " + rest, C["faint"])]
    svg.add(svg.text(x, y, spans, size))


def wide_card(p, n):
    h = 244
    svg = Svg(W, h, f"{p['name']}: {p['tagline']}")
    clip = card_base(svg, p, h, n)
    # art on the right, fading into the card on its left edge
    ax = 410
    fade = svg.id("fade")
    svg.defs.append(f'<linearGradient id="{fade}" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#fff" '
                    f'stop-opacity="0"/><stop offset=".38" stop-color="#fff"/></linearGradient>'
                    f'<mask id="{fade}m"><rect x="{ax}" y="35" width="{W - ax}" height="{h - 35}" fill="url(#{fade})"/>'
                    f'</mask>')
    svg.add(f'<g clip-path="url(#{clip})"><image href="{img(p["art"])}" x="{ax}" y="35" width="{W - ax}" '
            f'height="{h - 35}" preserveAspectRatio="xMidYMid slice" mask="url(#{fade}m)"/></g>')
    icon_tile(svg, p, 22, 52, 64)
    svg.add(svg.text(102, 77, [(p["name"], C["fg"], True)], 22))
    stat_row(svg, p, 102, 102, n)
    for i, line in enumerate(wrap(p["tagline"], 48)[:4]):
        svg.add(svg.text(22, 136 + i * 18.5, [(line, C["faint"])], 13))
    pills(svg, p, 22, h - 38)
    card_border(svg, p, h)
    svg.save(f"card-{p['id']}.svg")


def tall_card(p, n):
    w, h, art_h = 434, 380, 180
    svg = Svg(w, h, f"{p['name']}: {p['tagline']}")
    clip = card_base(svg, p, h, n)
    if p["art"] == "3ds":
        art_3ds(svg, 35, art_h)
    else:
        # a picture, or a recording (its .json, made by tools/prepare.py), fading into the card at the bottom
        fade = svg.id("fade")
        svg.defs.append(f'<linearGradient id="{fade}" x1="0" y1="0" x2="0" y2="1"><stop offset=".55" '
                        f'stop-color="#fff"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></linearGradient>'
                        f'<mask id="{fade}m"><rect x="0" y="35" width="{w}" height="{art_h}" fill="url(#{fade})"/>'
                        f'</mask>')
        if p["art"].endswith(".json"):
            art = art_loop(svg, json.loads((SRC / "img" / p["art"]).read_text()), 35, art_h)
        else:
            art = (f'<image href="{img(p["art"])}" x="0" y="35" width="{w}" height="{art_h}" '
                   f'preserveAspectRatio="xMidYMid slice"/>')
        svg.add(f'<g clip-path="url(#{clip})" mask="url(#{fade}m)">{art}</g>')
    icon_tile(svg, p, 18, 35 + art_h - 26, 58)
    svg.add(svg.text(90, 35 + art_h + 22, [(p["name"], C["fg"], True)], 19))
    stat_row(svg, p, 90, 35 + art_h + 45, n, 12.5)
    for i, line in enumerate(wrap(p["tagline"], 51)[:3]):
        svg.add(svg.text(18, 35 + art_h + 80 + i * 18, [(line, C["faint"])], 13))
    pills(svg, p, 18, h - 38)
    card_border(svg, p, h)
    svg.save(f"card-{p['id']}.svg")


def art_3ds(svg, top, art_h, frames=7, hold=1.7):
    """Both 3DS screens side by side, cycling through frames of the app's own demo recording."""
    w = svg.w
    svg.add(f'<rect x="1" y="{top}" width="{w - 2}" height="{art_h}" fill="{svg.gradient(["#20232C", "#15161B"], 0, top, w, art_h, 90)}"/>')
    # the randomizer's backdrop: a big faint Poké Ball outline
    cx, cy, r = w - 70, top + art_h / 2 + 10, 120
    art = svg.id("art")
    svg.defs.append(f'<clipPath id="{art}"><rect x="0" y="{top}" width="{w}" height="{art_h}"/></clipPath>')
    svg.add(f'<g fill="none" stroke="#2B2F3A" stroke-width="10" clip-path="url(#{art})">'
            f'<circle cx="{cx}" cy="{cy}" r="{r}"/><line x1="{cx - r}" y1="{cy}" x2="{cx + r}" y2="{cy}"/>'
            f'<circle cx="{cx}" cy="{cy}" r="30" fill="#20232C"/></g>')
    s = 0.48
    tw, th, bw = 400 * s, 240 * s, 320 * s
    gap, bez = 16, 8
    total = tw + bw + 4 * bez + gap
    x0 = (w - total) / 2
    y0 = top + (art_h - th - 2 * bez) / 2 - 6
    xs = (x0 + bez, x0 + 3 * bez + tw + gap)
    for bx, sw in ((x0, tw), (x0 + 2 * bez + tw + gap, bw)):
        svg.add(f'<rect x="{bx}" y="{y0}" width="{sw + 2 * bez}" height="{th + 2 * bez}" rx="9" fill="#2A2D36" '
                f'stroke="#3A3E4A"/>')
    svg.add(f'<circle cx="{x0 + bez + tw / 2}" cy="{y0 + 4}" r="1.6" fill="#4A4F5C"/>')  # inner camera
    ids = []
    for i in range(frames):
        fid = svg.id("f")
        svg.defs.append(f'<image id="{fid}" href="{img(f"randomizer-{i}.webp")}" width="400" height="490"/>')
        ids.append(fid)
    for (vx, vy, vw, vh), sx, sw in (((0, 0, 400, 240), xs[0], tw), ((40, 250, 320, 240), xs[1], bw)):
        layers = []
        for i, fid in enumerate(ids):
            layers.append(f'<use href="#{fid}" xlink:href="#{fid}" opacity="{1 if i == 0 else 0}" '
                          f'{flip(svg, i, 1, frames, hold)}/>')
        svg.add(f'<svg x="{sx}" y="{y0 + bez}" width="{sw}" height="{th}" viewBox="{vx} {vy} {vw} {vh}">'
                f'{"".join(layers)}</svg>')
    svg.add(f'<rect x="1" y="{top + art_h - 3}" width="{w - 2}" height="3" fill="#E5482F"/>')


def art_loop(svg, loop, top, art_h):
    """A recording on a loop: its still scene, the moving part of each frame cut from one sheet on top, and, when
    the recording has eyes, their glow. Returns the markup."""
    w = svg.w
    k = w / loop["size"][0]
    bx, by, cw, ch = loop["box"]
    cols = loop["columns"]
    rows = math.ceil((max(c for *_, c in loop["runs"]) + 1) / cols)
    sheet = svg.id("sheet")
    svg.defs.append(f'<image id="{sheet}" href="{img(loop["sheet"])}" width="{cols * cw}" height="{rows * ch}"/>')
    out = [f'<image href="{img(loop["scene"])}" x="0" y="{top}" width="{w}" height="{art_h}" '
           f'preserveAspectRatio="xMidYMid slice"/>']
    for start, length, cell in loop["runs"]:
        still = start <= loop["still"] < start + length
        out.append(f'<svg x="{bx * k:.1f}" y="{top + by * k:.1f}" width="{cw * k:.1f}" height="{ch * k:.1f}" '
                   f'viewBox="{cell % cols * cw} {cell // cols * ch} {cw} {ch}"><use href="#{sheet}" '
                   f'xlink:href="#{sheet}" opacity="{1 if still else 0}" '
                   f'{flip(svg, start, length, loop["slots"], loop["slot"])}/></svg>')
    if "eyes" in loop:
        out.append(eye_glow(svg, [(x * k, top + y * k) for x, y in loop["eyes"]], *loop["lit"], loop["slots"],
                            loop["slot"]))
    return "".join(out)


def eye_glow(svg, eyes, lit, dark, slots, slot):
    """Iridescent red glows on the Stone Saint's eyes, from slot `lit` until slot `dark` of the recording."""
    period = slots * slot
    on, off = 100 * lit / slots, 100 * dark / slots
    svg.css.append(f"@keyframes lit{{0%,{on:.2f}%{{opacity:0}}{on + 2:.2f}%,{off - 2:.2f}%{{opacity:1}}"
                   f"{off:.2f}%,100%{{opacity:0}}}}"
                   "@keyframes iris{0%,100%{fill:#FF1F3D}25%{fill:#FF5A2E}50%{fill:#FF2E86}75%{fill:#D80F38}}")
    blur = svg.id("glow")
    svg.defs.append(f'<filter id="{blur}" x="-150%" y="-150%" width="400%" height="400%">'
                    f'<feGaussianBlur stdDeviation="1.8"/></filter>')
    out = [f'<g style="mix-blend-mode:screen;animation:lit {period:.2f}s linear {-period:.2f}s infinite">']
    for i, (x, y) in enumerate(eyes):
        iris = f"animation:iris 2.4s ease-in-out {-0.7 * i:.1f}s infinite"
        out.append(f'<ellipse cx="{x:.1f}" cy="{y:.1f}" rx="5" ry="3" fill="#FF1F3D" filter="url(#{blur})" '
                   f'style="{iris}"/>'
                   f'<ellipse cx="{x:.1f}" cy="{y:.1f}" rx="2.2" ry="1" fill="#FF1F3D" style="{iris}"/>'
                   f'<ellipse cx="{x:.1f}" cy="{y:.1f}" rx="1" ry=".5" fill="#FFD3D8" opacity=".8"/>')
    out.append("</g>")
    return "".join(out)


def btop(p, n):
    """btop, themed obsidian-purple, showing a year of contributions."""
    gh = n.stats.get("github", {})
    g = Grid(18, 13)
    h = 446
    svg = Svg(W, h, f"btop: {n.values['contributions']} contributions in the last year")
    panel(svg, 0, 0, W, h)
    cols = int((W - 36) / g.cw)
    svg.add(svg.text(g.cx(0), g.base(0), prompt_top("~"), g.size))
    t = typed_command(svg, g, 1, "btop --preset github", start=0.3)
    right_clock(svg, g, 1, cols, n.clock)

    svg.add(f'<g {reveal(t)}>')
    # ¹activity: a braille graph of daily contributions, like btop's cpu graph
    bx, by, bw, bh = 16, 74, W - 32, 182
    box(svg, bx, by, bw, bh, "1", "activity", C["box"],
        right=f"{n.values['contributions']} contributions", center=f"{n.now:%a %d %b %Y}")
    gx, gy, gw, gh_ = bx + 18, by + 18, bw - 36, bh - 52
    braille_graph(svg, n.days, gx, gy, gw, gh_, t)
    # month ticks
    if n.days:
        first = dt.date.fromisoformat(n.days[0]["date"])
        span = len(n.days)
        for d in n.days:
            day = dt.date.fromisoformat(d["date"])
            if day.day == 1:
                mx = gx + (day - first).days / span * gw
                svg.add(svg.text(mx, gy + gh_ + 18, [(day.strftime("%b"), "#918799")], 11.5),
                        f'<line x1="{mx:.1f}" y1="{gy + gh_ + 3}" x2="{mx:.1f}" y2="{gy + gh_ + 6}" stroke="#4B3A57"/>')

    # ²langs: btop meters
    ly, lh = by + bh + 10, h - (by + bh + 10) - 14
    lw = (bw - 10) / 2
    langs = list(gh.get("languages", {}).items())[:p.get("stats", {}).get("languages", 6)]
    total = sum(v for _, v in gh.get("languages", {}).items()) or 1
    box(svg, bx, ly, lw, lh, "2", "langs", C["box2"], right="by commits")
    cells = 22
    for i, (name, v) in enumerate(langs):
        yy = ly + 30 + i * 22
        share = v / total
        svg.add(svg.text(bx + 16, yy + 10, [(f"{name:<11}", C["fg"])], 13))
        mx0 = bx + 16 + 11 * 7.8 + 4
        filled = max(1, round(share * cells / max(langs[0][1] / total, 0.01)))
        for c in range(cells):
            on = c < filled
            color = ramp(USED, c / (cells - 1)) if on else C["line"]
            delay = t + 0.2 + c * 0.02
            anim = f' class="fx" style="animation:in .15s {delay:.2f}s both"' if on else ""
            svg.add(f'<rect x="{mx0 + c * 9:.1f}" y="{yy + 1}" width="7" height="11" rx="1" fill="{color}"{anim}/>')
        svg.add(svg.text(bx + lw - 16, yy + 10, [(f"{share * 100:.0f}%", C["faint"])], 13, anchor="end"))

    # ³streak: counters and a weekday histogram
    sx = bx + lw + 10
    box(svg, sx, ly, lw, lh, "3", "streak", C["box2"], right="last 365 days")
    best = dt.date.fromisoformat(n.best_day["date"]) if n.best_day["date"] else None
    busiest = max(range(7), key=lambda i: n.weekdays[i])
    rows = [
        (nf("fa-fire"), "current", f"{n.streak} day{'s' * (n.streak != 1)}", C["red"]),
        (nf("md-trophy"), "longest", f"{n.best_streak} days", C["yellow"]),
        (nf("md-star_shooting"), "best day", f"{n.best_day['count']} on {best:%d %b}" if best else "-", C["lilac"]),
        (nf("fa-calendar_days"), "busiest", dt.date(2024, 1, 1 + busiest).strftime("%A") + "s", C["cyan"]),
    ]
    for i, (ic, k, v, col) in enumerate(rows):
        yy = ly + 40 + i * 24
        svg.add(svg.text(sx + 16, yy, [(ic + " ", col), (f"{k:<9}", C["faint"]), (v, C["fg"], True)], 13))
    # weekday bars
    hx, hy, hh = sx + lw - 150, ly + 26, lh - 54
    peak = max(n.weekdays) or 1
    for i, v in enumerate(n.weekdays):
        bh_ = max(2, v / peak * hh)
        x = hx + i * 19
        svg.add(f'<rect x="{x}" y="{hy}" width="12" height="{hh}" rx="2" fill="{C["line"]}" fill-opacity=".5"/>',
                f'<rect class="fx" x="{x}" y="{hy + hh - bh_:.1f}" width="12" height="{bh_:.1f}" rx="2" '
                f'fill="{BAND[0] if i == busiest else ramp(CPU, .25 + .75 * v / peak)}" style="transform-origin:bottom;animation:rise .7s ease-out '
                f'{t + 0.3 + i * 0.05:.2f}s both"/>',
                svg.text(x + 6, hy + hh + 16, [("MTWTFSS"[i], C["fg"] if i == busiest else C["dim"])], 11.5,
                         anchor="middle"))
    svg.add("</g>")
    svg.save("stats.svg")


def box(svg, x, y, w, h, num, title, color, right=None, center=None):
    """A btop box: rounded border with ┐¹title┌ cut into the top line."""
    svg.add(f'<rect x="{x + .5}" y="{y + .5}" width="{w - 1}" height="{h - 1}" rx="7" fill="none" stroke="{color}"/>')

    def notch(tx, label_spans, length):
        yy = y + .5
        svg.add(f'<rect x="{tx - 4:.1f}" y="{yy - 2}" width="{length + 8:.1f}" height="4" fill="{C["bg"]}"/>',
                f'<path d="M{tx - 4:.1f} {yy} v4 M{tx + length + 4:.1f} {yy} v4" stroke="{color}" fill="none"/>',
                svg.text(tx, yy + 4.5, label_spans, 13))

    lab = [(superscript(num), C["purple"], True), (title, C["title"], True)]
    notch(x + 18, lab, (len(title) + 1) * 7.8)
    if right:
        notch(x + w - 18 - len(right) * 7.8, [(right, C["faint"])], len(right) * 7.8)
    if center:
        notch(x + (w - len(center) * 7.8) / 2, [(center, C["dim"])], len(center) * 7.8)


def superscript(s):
    return s.translate(str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹"))


def braille_graph(svg, days, x, y, w, h, t):
    """Daily contributions as btop's braille graph: gradient columns seen through a grid of dots."""
    px, py = 4.2, 4.25
    ncol, nrow = int(w / px), int(h / py)
    counts = [d["count"] for d in days] or [0]
    per = len(counts) / ncol
    cols = []
    for i in range(ncol):
        a, b = int(i * per), max(int((i + 1) * per), int(i * per) + 1)
        cols.append(sum(counts[a:b]) / (b - a))
    smooth = [sum(cols[max(0, i - 1):i + 2]) / len(cols[max(0, i - 1):i + 2]) for i in range(ncol)]
    peak = max(smooth) or 1
    pat, mask = svg.id("dots"), svg.id("dmask")
    x0 = x + (w - ncol * px) / 2
    bottom = y + nrow * py
    svg.defs.append(f'<pattern id="{pat}" patternUnits="userSpaceOnUse" x="{x0:.2f}" y="{y:.2f}" width="{px}" '
                    f'height="{py}"><circle cx="{px / 2}" cy="{py / 2}" r="1.15" fill="#fff"/></pattern>'
                    f'<mask id="{mask}"><rect x="{x0:.1f}" y="{y}" width="{ncol * px:.1f}" height="{nrow * py:.1f}" '
                    f'fill="url(#{pat})"/></mask>')
    path = []
    for i, v in enumerate(smooth):
        k = round((v / peak) ** 0.6 * nrow)
        if v > 0:
            k = max(k, 1)
        if k:
            path.append(f"M{x0 + i * px:.1f} {bottom:.1f}h{px}v{-k * py:.2f}h{-px}z")
    fill = svg.gradient(list(reversed(CPU)), x0, y, ncol * px, nrow * py, 90)
    svg.add(f'<rect x="{x0:.1f}" y="{y}" width="{ncol * px:.1f}" height="{nrow * py:.1f}" fill="#2A252F" '
            f'mask="url(#{mask})" opacity=".55"/>',
            f'<g mask="url(#{mask})" style="animation:sweep 1.6s ease-out {t:.2f}s both">'
            f'<path d="{"".join(path)}" fill="{fill}"/></g>')


def stack(p, n):
    """eza --tree --icons ~/stack."""
    g = Grid(18, 13)
    tree_lh = 25
    h = 13 + 2 * g.lh + 10 + (len(p["stack"]) + 1) * tree_lh + 14
    svg = Svg(W, h, "eza --tree --icons ~/stack")
    panel(svg, 0, 0, W, h)
    cols = int((W - 36) / g.cw)
    svg.add(svg.text(g.cx(0), g.base(0), prompt_top("~"), g.size))
    t = typed_command(svg, g, 1, "eza --tree --icons ~/stack", start=0.3)
    right_clock(svg, g, 1, cols, n.clock)
    y0 = g.top(2) + 12
    svg.add(f'<g {reveal(t)}>')
    svg.add(svg.text(g.cx(0), y0 + 16, [(nf("custom-folder") + " ", C["eza_dir"]), ("stack", C["eza_dir"], True)]))
    for i, d in enumerate(p["stack"]):
        last = i == len(p["stack"]) - 1
        yy = y0 + 16 + (i + 1) * tree_lh
        spans = [("└── " if last else "├── ", C["frame"]), (nf(d["icon"]) + " ", C["eza_dir"]),
                 (f"{d['dir']:<9}", C["eza_dir"], True)]
        for j, (icon, name, color) in enumerate(d["items"]):
            spans += [("   " if j else ""), (nf(icon) + " ", color), (name, C["fg"])]
        width = sum(len(s if isinstance(s, str) else s[0]) for s in spans)
        if width > cols:
            print(f"  ! stack: '{d['dir']}' is {width - cols} cells too wide")
        svg.add(f'<g {reveal(t + 0.06 * (i + 1))}>', svg.text(g.cx(0), yy, spans), "</g>")
    svg.add("</g>")
    svg.save("stack.svg")


def footer(p, n):
    g = Grid(18, 13)
    svg = Svg(W, 2 * g.lh + 26, "exit")
    panel(svg, 0, 0, W, svg.h)
    cols = int((W - 36) / g.cw)
    svg.add(svg.text(g.cx(0), g.base(0), prompt_top(p["cwd"]), g.size))
    f = p.get("footer", {})
    typed_command(svg, g, 1, f.get("command", "exit"), start=0.4, blink_from=1.0)
    svg.add(svg.text(g.cx(cols), g.base(0), [(f"# {f.get('note', '')}", C["dim"])], g.size, anchor="end"))
    right_clock(svg, g, 1, cols, n.clock)
    petals(svg, 300, 8, [(520, 30, .45, 14, 0, 240), (440, 40, .38, 12, 5, -200)])
    svg.save("footer.svg")


def readme(p, n):
    """README.md: the pictures in order, each card linked to its project."""
    def img_tag(src, alt, width="100%"):
        return f'<img src="assets/{src}" width="{width}" alt="{esc(alt)}">'

    def linked(proj, width):
        tag = img_tag(f"card-{proj['id']}.svg", f"{proj['name']}: {n.fill(proj['tagline'])}", width)
        return f'<a href="{proj["url"]}">{tag}</a>' if proj.get("url") else tag

    first, *rest = p["project"]
    lines = [
        "<!-- Generated by src/build.py from profile.toml. Edit those, not this file. -->",
        '<p align="center">',
        "  " + img_tag("header.svg", f"fastfetch for {p['user']}@{p['host']}: "
                       + "; ".join(f"{key} {value}" for key, value in filter(None, fetch_lines(p, n)))),
        "</p>", "",
        '<p align="center">', "  " + img_tag("projects.svg", "eza --icons ~/projects"), "</p>",
        '<p align="center">', "  " + linked(first, "100%"), "</p>",
        '<p align="center">', *("  " + linked(r, "49%") for r in rest), "</p>", "",
        '<p align="center">', "  " + img_tag("stats.svg", f"btop: {n.values['contributions']} contributions in the "
                                             f"last year, current streak {n.streak} days, longest {n.best_streak} days"),
        "</p>", "",
        '<p align="center">', "  " + img_tag("stack.svg", "My stack: " + "; ".join(
            f"{d['dir']}: " + ", ".join(i[1] for i in d["items"]) for d in p["stack"])), "</p>", "",
        '<p align="center">', "  " + img_tag("footer.svg", p.get("footer", {}).get("note", "")), "</p>",
        "",
        '<p align="center"><sub>Drawn in the colors of my terminal (<a href="https://github.com/ZIPender/zsh-config">'
        "zsh-config</a>) and refreshed every day by a GitHub Action.</sub></p>",
        "",
    ]
    (ROOT / "README.md").write_text("\n".join(lines))
    print("  README.md")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--offline", action="store_true", help="render from data/stats.json without fetching")
    args = ap.parse_args()
    profile = tomllib.loads((ROOT / "profile.toml").read_text())
    n = Numbers(data.load(profile, offline=args.offline), profile)
    OUT.mkdir(exist_ok=True)
    header(profile, n)
    prompt_strip("projects.svg", "~/perso", "eza --icons ~/projects", "eza --icons ~/projects", n)
    first, *rest = profile["project"]
    wide_card(first, n)
    for proj in rest:
        tall_card(proj, n)
    btop(profile, n)
    stack(profile, n)
    footer(profile, n)
    readme(profile, n)


if __name__ == "__main__":
    main()
