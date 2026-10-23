"""Live numbers for the profile: GitHub (GraphQL) and Modrinth, cached in data/stats.json.

Every source falls back to the cached copy when it fails, so a flaky API never blanks a picture.
"""
import datetime as dt
import json
import os
import re
import urllib.request
from pathlib import Path

CACHE = Path(__file__).resolve().parent.parent / "data" / "stats.json"

# Languages that are not really "what I write": markup, build glue and engine-generated files.
IGNORED_LANGUAGES = {
    "HTML", "CSS", "SCSS", "Less", "Makefile", "CMake", "Batchfile", "Dockerfile", "ShaderLab", "HLSL", "GLSL",
    "Mathematica", "Roff", "Procfile", "Blade", "Twig", "Smarty", "Jupyter Notebook", "MDX", "Vue",
}

QUERY = """
query($login: String!) {
  user(login: $login) {
    createdAt
    contributionsCollection {
      contributionCalendar { totalContributions weeks { contributionDays { date contributionCount } } }
      commitContributionsByRepository(maxRepositories: 100) {
        repository { name isPrivate primaryLanguage { name } }
        contributions { totalCount }
      }
    }
    repositories(first: 100, ownerAffiliations: OWNER, privacy: PUBLIC) {
      totalCount nodes { name stargazerCount latestRelease { tagName } }
    }
  }
}
"""


def _request(url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": "ZIPender-profile/1.0", **(headers or {})})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def fetch_github(login, token, hide_repos=(), hide_languages=()):
    """Contribution calendar, account age, public stars, and the languages of this year's commits.

    Languages are weighted by commits rather than bytes: byte counts are dominated by mirrored and generated
    code, while commits are what I actually wrote this year. A repo counts as its primary language.
    """
    headers = {"Authorization": f"bearer {token}", "Content-Type": "application/json"}
    body = json.dumps({"query": QUERY, "variables": {"login": login}}).encode()
    res = _request("https://api.github.com/graphql", body, headers)
    if "errors" in res:
        raise RuntimeError(res["errors"])
    user = res["data"]["user"]
    contrib = user["contributionsCollection"]

    days = [
        {"date": d["date"], "count": d["contributionCount"]}
        for w in contrib["contributionCalendar"]["weeks"]
        for d in w["contributionDays"]
    ]
    commits, private_seen = {}, False
    for c in contrib["commitContributionsByRepository"]:
        repo = c["repository"]
        private_seen |= repo["isPrivate"]
        lang = (repo["primaryLanguage"] or {}).get("name")
        if lang and lang not in IGNORED_LANGUAGES and lang not in hide_languages and repo["name"] not in hide_repos:
            commits[lang] = commits.get(lang, 0) + c["contributions"]["totalCount"]
    return {
        "created_at": user["createdAt"],
        "contributions": contrib["contributionCalendar"]["totalContributions"],
        "days": days,
        "stars": {r["name"]: r["stargazerCount"] for r in user["repositories"]["nodes"]},
        "releases": {r["name"]: r["latestRelease"]["tagName"].lstrip("v")
                     for r in user["repositories"]["nodes"] if r["latestRelease"]},
        "public_repos": user["repositories"]["totalCount"],
        "languages": dict(sorted(commits.items(), key=lambda kv: -kv[1])),
        "languages_include_private": private_seen,
    }


def fetch_modrinth(slug):
    p = _request(f"https://api.modrinth.com/v2/project/{slug}")
    versions = _request(f"https://api.modrinth.com/v2/project/{slug}/version")  # newest first
    return {"downloads": p["downloads"], "followers": p["followers"],
            "version": versions[0]["version_number"] if versions else None}


def fetch_curseforge(project_id):
    """Downloads and latest file of a CurseForge project, through the keyless CFWidget API (it wants the
    numeric project ID: the slug lookup does not find new projects)."""
    p = _request(f"https://api.cfwidget.com/{project_id}")
    latest = re.findall(r"\d+(?:\.\d+)+", (p.get("download") or {}).get("display", ""))
    return {"downloads": p["downloads"]["total"], "version": latest[-1] if latest else None}


# Placeholders in profile.toml that name an outside project, e.g. {downloads:modrinth/cobblemon_expeditions}.
PROJECT_REF = re.compile(r"\{\w+:(modrinth|curseforge)/([^}]+)\}")
FETCHERS = {"modrinth": fetch_modrinth, "curseforge": fetch_curseforge}


def load(profile, offline=False):
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    if offline:
        return cache

    token = os.environ.get("PROFILE_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        try:
            hide = profile.get("stats", {})
            gh = fetch_github(profile["login"], token, set(hide.get("hide_repos", [])), set(hide.get("hide_languages", [])))
            # The Action's own token only sees public repos; keep the language mix from a run that saw the
            # private ones rather than shrinking it to a handful of public repos.
            old = cache.get("github", {})
            if not gh["languages_include_private"] and old.get("languages_include_private"):
                gh["languages"] = old["languages"]
                gh["languages_include_private"] = True
            cache["github"] = gh
        except Exception as e:  # noqa: BLE001 - any failure means "use the cache"
            print(f"github: {e}; using the cached numbers")
    else:
        print("github: no PROFILE_TOKEN or GITHUB_TOKEN; using the cached numbers")

    refs = {m for text in _all_text(profile) for m in PROJECT_REF.findall(text)}
    for site, project in sorted(refs):
        try:
            cache.setdefault(site, {})[project] = FETCHERS[site](project)
        except Exception as e:  # noqa: BLE001
            print(f"{site} {project}: {e}; using the cached numbers")

    cache["updated"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(cache, indent=1, ensure_ascii=False) + "\n")
    return cache


def _all_text(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for v in node.values():
            yield from _all_text(v)
    elif isinstance(node, list):
        for v in node:
            yield from _all_text(v)
