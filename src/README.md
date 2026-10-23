# How the profile is made

Every picture on the profile is an SVG drawn by `build.py`. They're styled after my desktop: Konsole's Obsidian
Purple scheme, the starship prompt, fastfetch, btop and eza from
[zsh-config](https://github.com/ZIPender/zsh-config), and spectacle-border's albedo-yae frame (Yae's sakura
top-left, Albedo's feathers bottom-right).

| File | What it is |
| --- | --- |
| `../profile.toml` | Everything the profile says: fetch lines, projects, stack. Edit this. |
| `build.py` | Renders `../assets/*.svg` and `../README.md`. |
| `data.py` | Live numbers from GitHub (GraphQL), Modrinth and CurseForge, cached in `../data/stats.json`. |
| `fonts/` | JetBrains Mono Nerd Font (OFL). Each SVG embeds only the glyphs it uses. |
| `img/`, `art/` | Pictures and corner art the SVGs embed, made by `../tools/prepare.py`. |

Commands, from the repo root:

```sh
tools/refresh.sh             # fetch numbers, render, write .preview/{dark,light}.html
tools/refresh.sh --offline   # render from the cached numbers
tools/preview.sh --open      # open the preview (animations play in a browser)
.venv/bin/python tools/prepare.py   # rebuild src/img and src/fonts from the source art
```

The `refresh` workflow re-renders every day at midnight Zurich time (22:00 UTC in summer, 23:00 in winter),
and on every push that changes `profile.toml` or `src/`.
It uses the workflow's own token, which sees the contribution calendar and public stars. The language mix in
btop comes from this year's commits, private repos included, so it only refreshes when a `PROFILE_TOKEN` secret
(a token with `repo` scope) is set. Without one, the mix from the last local run is kept.

Each SVG is self-contained (fonts and images inlined, CSS animations, no scripts), because GitHub shows README
images with external resources blocked.
