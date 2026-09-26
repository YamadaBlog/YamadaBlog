#!/usr/bin/env python3
"""forge.py -- renders every generated asset of this profile from real data.

    python scripts/forge.py              # calendar (public API) + snapshot -> assets, README, site
    python scripts/forge.py --snapshot   # refresh data/snapshot.json (needs a token that sees private repos)

stdlib only. No third-party widget, no hosted card service: every pixel is built here.
The snapshot stores AGGREGATES only (bytes per language, commit total, repo counts) --
never a private repository name.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

LOGIN = "YamadaBlog"
ROOT = Path(__file__).resolve().parent.parent
ASSETS, SITE, DATA = ROOT / "assets", ROOT / "site", ROOT / "data"
SNAPSHOT = DATA / "snapshot.json"
SKIP_MIRRORS = "mirror"             # backup mirrors would double-count everything
AUTHORED = ["Python", "TypeScript", "Vue", "JavaScript", "Rust", "Shell", "CSS", "PowerShell", "Svelte", "Dockerfile"]

MONO = "'JetBrains Mono','SFMono-Regular',Consolas,'Liberation Mono',Menlo,monospace"
THEMES = {
    "dark":  dict(bg="#0b0e14", panel="#0f131c", line="#1c2230", fg="#d6dce8", mute="#6b7489",
                  acc="#7ee7c4", vio="#b69cff", warn="#ffcf70", red="#ff7b86"),
    "light": dict(bg="#fbfaf7", panel="#f3f2ed", line="#e2e0d8", fg="#1b1f29", mute="#6a7182",
                  acc="#0f8a6a", vio="#6a4fd6", warn="#a86a00", red="#c43d4b"),
}


# ---------------------------------------------------------------- data
def token() -> str:
    t = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if t:
        return t
    return subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, check=True).stdout.strip()


def api(path: str, body: dict | None = None):
    url = "https://api.github.com/" + path.lstrip("/")
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body else None,
                                 headers={"Authorization": f"bearer {token()}", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req) as r:
        return json.load(r), r.headers


def calendar() -> list[tuple[dt.date, int]]:
    q = """query($l:String!){user(login:$l){contributionsCollection{contributionCalendar{
           weeks{contributionDays{date contributionCount}}}}}}"""
    d, _ = api("graphql", {"query": q, "variables": {"l": LOGIN}})
    weeks = d["data"]["user"]["contributionsCollection"]["contributionCalendar"]["weeks"]
    days = [(dt.date.fromisoformat(x["date"]), x["contributionCount"]) for w in weeks for x in w["contributionDays"]]
    return days[-365:]



def punch_repo(full_name: str, punch: list[list[int]]) -> None:
    """Accumulate a weekday x hour matrix from the last year of commits.

    Timestamps are read in the AUTHOR's own offset, so "3am" means 3am where the
    commit was written -- not UTC. Only commits authored by LOGIN are counted.
    """
    since = (dt.date.today() - dt.timedelta(days=365)).isoformat() + "T00:00:00Z"
    page = 1
    while page <= 12:                                  # hard cap: stay polite
        try:
            rows, _ = api(f"repos/{full_name}/commits?per_page=100&page={page}&since={since}")
        except Exception:
            return
        if not rows:
            return
        for c in rows:
            author = (c.get("author") or {}).get("login")
            stamp = (((c.get("commit") or {}).get("author") or {}).get("date")) or ""
            if author and author != LOGIN:
                continue
            m = re.match(r"(\d{4})-(\d\d)-(\d\d)T(\d\d):\d\d:\d\d(Z|[+-]\d\d:\d\d)", stamp)
            if not m:
                continue
            y, mo, d, hh, off = int(m[1]), int(m[2]), int(m[3]), int(m[4]), m[5]
            if off != "Z":                              # shift into the author's local clock
                sign = 1 if off[0] == "+" else -1
                hh += sign * int(off[1:3])
            day = dt.date(y, mo, d)
            if hh >= 24:
                hh -= 24; day += dt.timedelta(days=1)
            elif hh < 0:
                hh += 24; day -= dt.timedelta(days=1)
            punch[day.weekday()][hh] += 1
        if len(rows) < 100:
            return
        page += 1


def refresh_snapshot() -> None:
    repos, _ = api("user/repos?per_page=100&affiliation=owner")
    langs: dict[str, int] = {}
    punch = [[0] * 24 for _ in range(7)]
    commits, n = 0, 0
    for r in repos:
        if SKIP_MIRRORS in (r["description"] or "").lower() or r["fork"]:
            continue
        n += 1
        for k, v in api(f"repos/{r['full_name']}/languages")[0].items():
            langs[k] = langs.get(k, 0) + v
        _, h = api(f"repos/{r['full_name']}/commits?per_page=1")
        m = re.search(r'page=(\d+)>; rel="last"', h.get("Link", "") or "")
        commits += int(m.group(1)) if m else 1
        punch_repo(r["full_name"], punch)
    DATA.mkdir(exist_ok=True)
    put(SNAPSHOT, json.dumps({
        "taken": dt.date.today().isoformat(),
        "repos": n, "public": sum(1 for r in repos if not r["private"]),
        "commits": commits, "languages": dict(sorted(langs.items(), key=lambda kv: -kv[1])),
        "punch": punch,
    }, indent=2) + "\n")
    print(f"snapshot: {n} repos, {commits} commits")


# ---------------------------------------------------------------- stats
def stats(days: list[tuple[dt.date, int]], snap: dict) -> dict:
    today = days[-1][0]
    counts = [c for _, c in days]
    total = sum(counts)
    active = sum(1 for c in counts if c)
    run = best = 0
    for c in counts:
        run = run + 1 if c else 0
        best = max(best, run)
    cur = 0
    for c in reversed(counts[:-1] if counts[-1] == 0 else counts):   # today may still be empty
        if not c:
            break
        cur += 1
    peak_d, peak = max(days, key=lambda x: x[1])
    load = [round(sum(counts[-n:]) / n, 2) for n in (7, 30, 90)]
    wk = [0] * 7
    for d, c in days:
        wk[d.weekday()] += c
    born = dt.date(2022, 11, 18)
    y, m = divmod((today.year - born.year) * 12 + today.month - born.month, 12)
    langs = {k: v for k, v in snap["languages"].items() if k in AUTHORED}
    return dict(today=today.isoformat(), total=total, active=active, days=len(days), best=best, cur=cur,
                peak=peak, peak_d=peak_d.isoformat(), load=load, weekday=wk, uptime=f"{y}y {m}m",
                commits=snap["commits"], repos=snap["repos"], public=snap["public"],
                langs=langs, src_mb=round(sum(langs.values()) / 1e6, 1), snap=snap["taken"])


# ---------------------------------------------------------------- the sigil
def ouroboros(cols=38, rows=19) -> list[str]:
    """A ring of glyphs lifted from this very file: the snake is made of its own source."""
    src = [ch for ch in Path(__file__).read_text(encoding="utf-8") if ch.isascii() and ch.isprintable() and ch not in " <>&\"'`"]
    grid = [[" "] * cols for _ in range(rows)]
    cx, cy, R, i = (cols - 1) / 2, (rows - 1) / 2, rows / 2 - 1.2, 0
    for y in range(rows):
        for x in range(cols):
            dx, dy = (x - cx) / 2.0, y - cy                     # glyphs are ~2x taller than wide
            r, a = math.hypot(dx, dy), (math.atan2(dy, dx) + math.pi * 2.5) % (2 * math.pi)
            t = a / (2 * math.pi)                               # 0 at the head, 1 at the tail
            thick = 0.9 + 2.6 * (1 - t) ** 0.55                # body tapers into the tail
            if abs(r - R) < thick / 2 and 0.035 < t:
                grid[y][x] = src[(i * 7) % len(src)]
                i += 1
    hy, hx = int(round(cy - R)), int(round(cx))                 # head biting the tail, top of the ring
    for k, ch in enumerate("{@>"):
        if 0 <= hx - 2 + k < cols:
            grid[max(hy, 0)][hx - 2 + k] = ch
    return ["".join(r) for r in grid]


def put(path: Path, text: str) -> None:
    path.write_bytes(text.encode("utf-8"))          # LF on every OS


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---------------------------------------------------------------- svg: header
def header_svg(s: dict, th: dict) -> str:
    W, H = 1000, 400
    ring = ouroboros()
    art = "\n".join(
        f'<text x="30" y="{58 + i * 17}" xml:space="preserve">{esc(line)}</text>' for i, line in enumerate(ring))
    L = [
        ("h", "mao@lab"),
        ("r", "-" * 44),
        ("kv", "Role", "systems builder (research infra, tooling, UI)"),
        ("kv", "Uptime", f"{s['uptime']} on GitHub"),
        ("kv", "Kernel", "Python . TypeScript . Rust"),
        ("kv", "Shell", "bash . pwsh . a lot of make"),
        ("kv", "Commits", f"{s['total']:,} contributions / 365d"),
        ("kv", "Active", f"{s['active']}/{s['days']} days  (streak max {s['best']}d)"),
        ("kv", "Load avg", "  ".join(f"{x:.2f}" for x in s["load"]) + "   commits/day 7|30|90"),
        ("kv", "Source", f"{s['src_mb']} MB authored . {s['repos']} repos ({s['public']} public)"),
        ("kv", "Invariant", "no look-ahead . replay == live"),
        ("kv", "Status", "RUNNING  model factory / anti-leak linter"),
        ("r", ""),
        ("pal", ""),
    ]
    out, y = [], 58
    for row in L:
        if row[0] == "h":
            out.append(f'<text x="400" y="{y}" font-weight="700"><tspan fill="{th["acc"]}">mao</tspan>'
                       f'<tspan fill="{th["fg"]}">@</tspan><tspan fill="{th["vio"]}">lab</tspan></text>')
        elif row[0] == "r":
            out.append(f'<text x="400" y="{y}" fill="{th["line"]}">{row[1]}</text>')
        elif row[0] == "kv":
            k, v = row[1], row[2]
            col = th["warn"] if k == "Status" else th["fg"]
            out.append(f'<text x="400" y="{y}" class="kv" style="animation-delay:{0.15 + len(out) * 0.07:.2f}s">'
                       f'<tspan fill="{th["acc"]}">{k}</tspan><tspan fill="{th["mute"]}">: </tspan>'
                       f'<tspan x="510" fill="{col}">{esc(v)}</tspan></text>')
        elif row[0] == "pal":
            for i, c in enumerate(["bg", "line", "mute", "fg", "acc", "vio", "warn", "red"]):
                out.append(f'<rect x="{400 + i * 30}" y="{y - 14}" width="30" height="16" fill="{th[c]}"/>')
        y += 22
    foot = (f'<text x="{W - 30}" y="{H - 22}" text-anchor="end" fill="{th["mute"]}" font-size="11">'
            f'rendered {s["today"]} by scripts/forge.py . the ring is made of that file</text>')
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-label="mao@lab: {s['total']} contributions in 365 days, {s['active']} active days">
<style>
text{{font-family:{MONO};font-size:14px}}
.art text{{font-size:13px}}
.kv{{animation:k .4s ease-out backwards}}
@keyframes k{{from{{opacity:0;transform:translateX(-6px)}}}}
.spin{{animation:p 5s ease-in-out infinite alternate}}
@keyframes p{{from{{opacity:.75}}to{{opacity:1}}}}
</style>
<defs><linearGradient id="rg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{th['vio']}"/><stop offset="1" stop-color="{th['acc']}"/></linearGradient></defs>
<rect width="{W}" height="{H}" rx="12" fill="{th['bg']}"/>
<rect x=".5" y=".5" width="{W - 1}" height="{H - 1}" rx="12" fill="none" stroke="{th['line']}"/>
<g class="art spin" fill="url(#rg)">{art}</g>
{"".join(out)}
{foot}
</svg>'''




# ---------------------------------------------------------------- svg: sigil
def sigil_svg(th: dict) -> str:
    """An ouroboros whose body is the source code of the program drawing it.

    Not a picture of a snake: every glyph on the ring is a character taken, in
    order, from this file. Change forge.py and the snake changes with it.
    """
    ring = ouroboros(62, 23)
    W, H = 700, 430
    art = "".join(
        f'<text x="{W/2}" y="{48 + i*15}" text-anchor="middle" xml:space="preserve">{esc(line)}</text>'
        for i, line in enumerate(ring))
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-label="An ouroboros drawn in ASCII, where every glyph is a character of the source file that generates it.">
<style>
text{{font-family:{MONO};font-size:13px}}
.b{{animation:br 6s ease-in-out infinite alternate}}
@keyframes br{{from{{opacity:.62}}to{{opacity:1}}}}
</style>
<defs><linearGradient id="sg" x1="0" y1="0" x2="1" y2="1">
<stop offset="0" stop-color="{th['vio']}"/><stop offset=".55" stop-color="{th['acc']}"/><stop offset="1" stop-color="{th['vio']}"/></linearGradient></defs>
<rect width="{W}" height="{H}" rx="12" fill="{th['bg']}"/>
<rect x=".5" y=".5" width="{W-1}" height="{H-1}" rx="12" fill="none" stroke="{th['line']}"/>
<g class="b" fill="url(#sg)">{art}</g>
<text x="{W/2}" y="{H-24}" text-anchor="middle" fill="{th['mute']}" style="font-size:11px">the snake is made of the source that draws it . scripts/forge.py</text>
</svg>'''


# ---------------------------------------------------------------- svg: banner
DOMAINS = ["research infrastructure", "agent orchestration", "reverse engineering",
           "security research", "systems & automation", "applied ML"]


def banner_svg(days, s: dict, th: dict) -> str:
    """The masthead: a terminal that probes its own bench, over a live sparkline."""
    W, H = 1100, 340
    # --- live sparkline of the last 180 days, drawn along the floor
    tail = days[-180:]
    peak = max(1, max(c for _, c in tail))
    step = W / len(tail)
    pts = " ".join(f"{i*step:.1f},{H-56-44*(c/peak)**0.7:.1f}" for i, (_, c) in enumerate(tail))

    rows = []
    y = 96
    for i, d in enumerate(DOMAINS):
        col = [th["acc"], th["vio"], th["warn"]][i % 3]
        delay = 0.12 + i * 0.07
        rows.append(
            f'<g class="rw" style="animation-delay:{delay:.2f}s">'
            f'<text x="56" y="{y}" fill="{th["mute"]}">[</text>'
            f'<text x="68" y="{y}" fill="{col}">ok</text>'
            f'<text x="88" y="{y}" fill="{th["mute"]}">]</text>'
            f'<text x="108" y="{y}" fill="{th["fg"]}">{esc(d)}</text></g>')
        y += 25

    tags = ""
    x = 600
    for i, t in enumerate(["no look-ahead", "replay == live", "scope first", "it stops and asks"]):
        col = [th["acc"], th["vio"], th["warn"], th["red"]][i]
        w = len(t) * 7.8 + 18
        tags += (f'<g class="rw" style="animation-delay:{0.70 + i*0.09:.2f}s">'
                 f'<rect x="{x}" y="{78 + (i//2)*30}" width="{w:.0f}" height="21" rx="10" fill="none" stroke="{col}" stroke-opacity=".55"/>'
                 f'<text x="{x+9}" y="{93 + (i//2)*30}" fill="{col}" style="font-size:12px">{t}</text></g>')
        x = 600 if i % 2 else x + w + 10

    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-label="mao@lab -- research infrastructure, agent orchestration, reverse engineering, security research. {s['total']} contributions in the last year.">
<style>
text{{font-family:{MONO};font-size:15px}}
.rw{{animation:rw .35s ease-out backwards}}
@keyframes rw{{from{{opacity:0;transform:translateX(-8px)}}}}
.cur{{animation:bl 1.1s steps(1) infinite 1.2s}}
@keyframes bl{{50%{{opacity:0}}}}
.scan{{animation:sc 7s linear infinite}}
@keyframes sc{{from{{transform:translateY(-60px)}}to{{transform:translateY({H}px)}}}}
.spark{{stroke-dasharray:4000;animation:dr 3.4s ease-out backwards}}
.gl{{animation:g1 9s steps(1) infinite}}.gl2{{animation:g2 9s steps(1) infinite}}
@keyframes g1{{0%,90.4%,91.6%,96.4%,97.4%,100%{{transform:translateX(0);opacity:0}}90.5%,91.5%{{transform:translateX(-2.5px);opacity:1}}96.5%,97.3%{{transform:translateX(2px);opacity:1}}}}
@keyframes g2{{0%,90.4%,91.6%,96.4%,97.4%,100%{{transform:translateX(0);opacity:0}}90.5%,91.5%{{transform:translateX(2.5px);opacity:1}}96.5%,97.3%{{transform:translateX(-2px);opacity:1}}}}
@keyframes dr{{from{{stroke-dashoffset:4000}}}}
</style>
<defs>
  <linearGradient id="ti" x1="0" x2="1"><stop offset="0" stop-color="{th['vio']}"/><stop offset="1" stop-color="{th['acc']}"/></linearGradient>
  <pattern id="gr" width="26" height="26" patternUnits="userSpaceOnUse"><path d="M26 0H0v26" fill="none" stroke="{th['line']}" stroke-width="1"/></pattern>
  <linearGradient id="sg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{th['acc']}" stop-opacity="0"/><stop offset="1" stop-color="{th['acc']}" stop-opacity=".07"/></linearGradient>
</defs>
<rect width="{W}" height="{H}" rx="14" fill="{th['bg']}"/>
<rect width="{W}" height="{H}" rx="14" fill="url(#gr)"/>
<rect class="scan" width="{W}" height="60" fill="url(#sg)"/>
<polyline class="spark" points="{pts}" fill="none" stroke="{th['acc']}" stroke-width="1.2" stroke-opacity=".42"/>
<rect x=".5" y=".5" width="{W-1}" height="{H-1}" rx="14" fill="none" stroke="{th['line']}"/>
<circle cx="26" cy="24" r="5.5" fill="#ff5f57"/><circle cx="44" cy="24" r="5.5" fill="#febc2e"/><circle cx="62" cy="24" r="5.5" fill="#28c840"/>
<text x="{W/2}" y="29" text-anchor="middle" fill="{th['mute']}" style="font-size:12px">mao@lab: ~/bench</text>
<text x="34" y="66"><tspan fill="{th['acc']}">$</tspan><tspan fill="{th['fg']}"> probe --all</tspan><tspan class="cur" fill="{th['acc']}">_</tspan></text>
{"".join(rows)}
<g class="gl"><text x="600" y="60" style="font-size:36px;font-weight:700" fill="{th['red']}" opacity=".85">mao@lab</text></g>
<g class="gl2"><text x="600" y="60" style="font-size:36px;font-weight:700" fill="{th['acc']}" opacity=".85">mao@lab</text></g>
<text x="600" y="60" style="font-size:36px;font-weight:700" fill="url(#ti)">mao@lab</text>
{tags}
<text x="{W-34}" y="{H-16}" text-anchor="end" fill="{th['mute']}" style="font-size:12px">{s['total']:,} contributions . {s['active']}/365 active . streak {s['best']}d . {s['src_mb']} MB authored</text>
<text x="34" y="{H-16}" fill="{th['mute']}" style="font-size:12px">180d signal</text>
<line x1="0" y1="{H-34}" x2="{W}" y2="{H-34}" stroke="{th['line']}"/>
</svg>'''


# ---------------------------------------------------------------- svg: seismograph
def seismo_svg(days, s, th) -> str:
    W, H, pl, pr, mid = 1000, 230, 40, 30, 110
    n = len(days)
    step = (W - pl - pr) / n
    amp = lambda c: 0 if c == 0 else 6 + 84 * math.sqrt(c / max(1, s["peak"]))
    path = [f"M{pl},{mid}"]
    for i, (_, c) in enumerate(days):
        x = pl + i * step
        a = amp(c)
        path.append(f"L{x + step * .25:.1f},{mid - a:.1f}L{x + step * .75:.1f},{mid + a * .8:.1f}")
    path.append(f"L{W - pr},{mid}")
    ticks, last, lastx = [], None, 0
    for i, (d, _) in enumerate(days):
        x = pl + i * step
        if d.month != last and (not ticks or x - lastx > 40):
            last, lastx = d.month, x
            ticks.append(f'<line x1="{x:.1f}" y1="200" x2="{x:.1f}" y2="206" stroke="{th["mute"]}"/>'
                         f'<text x="{x + 3:.1f}" y="218" fill="{th["mute"]}">{d.strftime("%b").lower()}</text>')
    pi = next(i for i, (d, _) in enumerate(days) if d.isoformat() == s["peak_d"])
    px = pl + pi * step + step * .25
    grid = "".join(f'<line x1="{pl}" y1="{y}" x2="{W - pr}" y2="{y}" stroke="{th["line"]}" stroke-dasharray="2 4"/>'
                   for y in (20, 65, 155, 200))
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-label="Seismograph of daily contributions over the last {n} days">
<style>
text{{font-family:{MONO};font-size:11px}}
.tr{{stroke-dasharray:60000;animation:d 3.5s ease-out backwards}}
@keyframes d{{from{{stroke-dashoffset:60000}}}}
</style>
<rect width="{W}" height="{H}" rx="12" fill="{th['bg']}"/>
<rect x=".5" y=".5" width="{W - 1}" height="{H - 1}" rx="12" fill="none" stroke="{th['line']}"/>
{grid}
<line x1="{pl}" y1="{mid}" x2="{W - pr}" y2="{mid}" stroke="{th['line']}"/>
<path class="tr" d="{''.join(path)}" fill="none" stroke="{th['acc']}" stroke-width="1.1" stroke-linejoin="round"/>
<line x1="{px:.1f}" y1="{mid - amp(s['peak']) - 2:.1f}" x2="{px:.1f}" y2="14" stroke="{th['warn']}" stroke-dasharray="2 2"/>
<text x="{px - 6:.1f}" y="18" text-anchor="end" fill="{th['warn']}">peak event: {s['peak']} contributions . {s['peak_d']}</text>
<text x="{pl}" y="18" fill="{th['mute']}">SEISMO-01 . daily contributions . amplitude ~ sqrt(n)</text>
{"".join(ticks)}
</svg>'''


# ---------------------------------------------------------------- svg: spectrum
def spectrum_svg(s, th) -> str:
    W, H, pl = 1000, 190, 40
    langs = s["langs"]
    tot = sum(langs.values())
    pal = [th["acc"], th["vio"], th["warn"], th["red"], th["fg"], th["mute"]]
    x, bands, labels, lines = pl, [], [], []
    width = W - 2 * pl
    for i, (k, v) in enumerate(langs.items()):
        w = width * v / tot
        c = pal[i % len(pal)] if i < 5 else th["mute"]
        seed = sum(map(ord, k))
        for j in range(max(1, int(w / 2.2))):                # emission lines: denser where the language dominates
            seed = (seed * 1103515245 + 12345) & 0x7fffffff
            lx = x + (seed % 1000) / 1000 * w
            op = 0.25 + (seed >> 10) % 70 / 100
            lines.append(f'<line x1="{lx:.1f}" y1="44" x2="{lx:.1f}" y2="104" stroke="{c}" stroke-opacity="{op:.2f}"/>')
        bands.append(f'<rect x="{x:.1f}" y="112" width="{max(w - 1, 1):.1f}" height="4" fill="{c}"/>')
        if i < 5:
            labels.append((x, k, 100 * v / tot, c))
        x += w
    lab = "".join(f'<text x="{pl + i * 160}" y="148" fill="{c}">{k}</text>'
                  f'<text x="{pl + i * 160}" y="166" fill="{th["mute"]}">{p:.1f}%</text>'
                  for i, (_, k, p, c) in enumerate(labels))
    rest = len(langs) - 5
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-label="Language emission spectrum across all repositories">
<style>text{{font-family:{MONO};font-size:11px}}.sp{{animation:f 2s ease-out backwards}}@keyframes f{{from{{opacity:0}}}}</style>
<rect width="{W}" height="{H}" rx="12" fill="{th['bg']}"/>
<rect x=".5" y=".5" width="{W - 1}" height="{H - 1}" rx="12" fill="none" stroke="{th['line']}"/>
<text x="{pl}" y="28" fill="{th['mute']}">SPECTRO-02 . emission spectrum of {s['src_mb']} MB authored source . {s['repos']} repos, public + private . generated reports excluded</text>
<g class="sp">{"".join(lines)}</g>{"".join(bands)}{lab}
<text x="{W - pl}" y="166" text-anchor="end" fill="{th['mute']}">+{rest} trace elements</text>
</svg>'''




# ---------------------------------------------------------------- svg: punch card
def punch_svg(snap: dict, th: dict) -> str:
    """When the work actually happens. Author-local hours, not UTC."""
    W, H = 1000, 210
    grid = snap.get("punch") or [[0] * 24 for _ in range(7)]
    peak = max(1, max(max(r) for r in grid))
    total = sum(map(sum, grid))
    x0, y0, cw, ch = 58, 52, 36, 17
    days = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

    cells = []
    for d in range(7):
        for h in range(24):
            n = grid[d][h]
            if not n:
                continue
            r = 2.2 + 5.6 * (n / peak) ** 0.5
            cells.append(f'<circle cx="{x0 + h*cw + cw/2:.1f}" cy="{y0 + d*ch + ch/2:.1f}" '
                         f'r="{r:.1f}" fill="{th["acc"]}" fill-opacity="{0.35 + 0.65*(n/peak)**0.6:.2f}"><title>'
                         f'{days[d]} {h:02d}:00 . {n} commits</title></circle>')

    hours = "".join(f'<text x="{x0 + h*cw + cw/2:.1f}" y="{y0 - 10}" text-anchor="middle" fill="{th["mute"]}">{h:02d}</text>'
                    for h in range(0, 24, 3))
    labels = "".join(f'<text x="{x0 - 12}" y="{y0 + d*ch + ch/2 + 4}" text-anchor="end" fill="{th["mute"]}">{days[d]}</text>'
                     for d in range(7))
    night = sum(grid[d][h] for d in range(7) for h in list(range(0, 7)) + [23])
    wknd = sum(grid[d][h] for d in (5, 6) for h in range(24))
    by_hour = [sum(grid[d][h] for d in range(7)) for h in range(24)]
    busiest = by_hour.index(max(by_hour)) if total else 0
    note = (f"peak hour {busiest:02d}:00 . {100*night//max(total,1)}% outside 07-23 . "
            f"{100*wknd//max(total,1)}% on weekends" if total else "no commit timestamps in snapshot")
    band = (f'<rect x="{x0}" y="{y0-2}" width="{7*cw}" height="{7*ch}" fill="{th["vio"]}" fill-opacity=".05"/>'
            f'<rect x="{x0+23*cw}" y="{y0-2}" width="{cw}" height="{7*ch}" fill="{th["vio"]}" fill-opacity=".05"/>')
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-label="Punch card of {total} commits by weekday and author-local hour. {note}">
<style>text{{font-family:{MONO};font-size:11px}}</style>
<rect width="{W}" height="{H}" rx="12" fill="{th['bg']}"/>
<rect x=".5" y=".5" width="{W-1}" height="{H-1}" rx="12" fill="none" stroke="{th['line']}"/>
<text x="16" y="24" fill="{th['mute']}">PUNCHCARD-04 . {total:,} commits by weekday x hour . author-local clock, not UTC . shaded band = 00-06 and 23</text>
{band}{hours}{labels}{"".join(cells)}
<text x="16" y="{H-14}" fill="{th['warn']}">{note}</text>
</svg>'''


# ---------------------------------------------------------------- svg: automaton
def life_svg(days, th: dict) -> str:
    """Conway's Life, seeded by the contribution year itself.

    The seed is not decorative: a cell is born wherever that day cleared the
    median of its active days. So the pattern that evolves is literally the
    shape of the year's work. Generations are pre-computed and cross-faded in
    SMIL, because a README cannot run JavaScript.
    """
    W, H, CELL, GENS = 1000, 176, 8, 14
    cols, rows = W // CELL, H // CELL
    counts = sorted(c for _, c in days if c)
    med = counts[len(counts) // 2] if counts else 1

    grid = [[0] * cols for _ in range(rows)]
    weeks = max(1, len(days) // 7 + 1)
    for i, (_, c) in enumerate(days):                 # weekday band, weeks stretched to full width
        if c > med:
            x = (i // 7) * (cols - 1) // (weeks - 1) if weeks > 1 else 0
            grid[rows // 2 - 3 + i % 7][min(x, cols - 1)] = 1

    def step(g):
        out = [[0] * cols for _ in range(rows)]
        for y in range(rows):
            for x in range(cols):
                n = sum(g[(y + dy) % rows][(x + dx) % cols]
                        for dy in (-1, 0, 1) for dx in (-1, 0, 1) if dy or dx)
                out[y][x] = 1 if (n == 3 or (g[y][x] and n == 2)) else 0
        return out

    gens, g = [grid], grid
    for _ in range(GENS - 1):
        g = step(g)
        gens.append(g)

    dur = GENS * 0.9
    frames = []
    for k, g in enumerate(gens):
        cells = "".join(
            f'<rect x="{x*CELL}" y="{y*CELL}" width="{CELL-1}" height="{CELL-1}" rx="1"/>'
            for y in range(rows) for x in range(cols) if g[y][x])
        # each generation is visible for one slot of the loop, then hands over
        keys = f"0;1;1;0;0" if k else "1;1;0;0;1"
        t0 = k / GENS
        times = f"0;{max(t0-0.005,0):.4f};{t0+0.02:.4f};{min(t0+0.075,1):.4f};1" if k else "0;0.02;0.075;0.98;1"
        frames.append(
            f'<g opacity="0">{cells}'
            f'<animate attributeName="opacity" values="{keys}" keyTimes="{times}" '
            f'dur="{dur}s" repeatCount="indefinite" calcMode="linear"/></g>')

    alive0 = sum(map(sum, gens[0]))
    aliveN = sum(map(sum, gens[-1]))
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H+30}" viewBox="0 0 {W} {H+30}" role="img" aria-label="Conway's Game of Life seeded by the year's contribution pattern: {alive0} live cells at generation 0, {aliveN} after {GENS} generations.">
<style>text{{font-family:{MONO};font-size:11px}}</style>
<rect width="{W}" height="{H+30}" rx="12" fill="{th['bg']}"/>
<rect x=".5" y=".5" width="{W-1}" height="{H+29}" rx="12" fill="none" stroke="{th['line']}"/>
<text x="16" y="18" fill="{th['mute']}">AUTOMATON-03 . life, seeded by the year itself . a cell is born where that day beat the median</text>
<g transform="translate(0,26)" fill="{th['acc']}" fill-opacity=".85">{"".join(frames)}</g>
<text x="{W-16}" y="{H+22}" text-anchor="end" fill="{th['mute']}">gen 0: {alive0} cells  ->  gen {GENS-1}: {aliveN}</text>
<text x="16" y="{H+22}" fill="{th['mute']}">B3/S23 . toroidal . {cols}x{rows}</text>
</svg>'''


# ---------------------------------------------------------------- text outputs
def card_txt(s) -> str:
    g, v, d, y, r = "\033[38;5;122m", "\033[38;5;141m", "\033[38;5;245m", "\033[38;5;221m", "\033[0m"
    ring = ouroboros(30, 13)
    info = [f"{g}mao{r}@{v}lab{r}", d + "-" * 34 + r,
            f"{g}role{r}      systems builder",
            f"{g}focus{r}     research infra . AI tooling . UI",
            f"{g}365d{r}      {s['total']:,} contributions",
            f"{g}load{r}      {' '.join(f'{x:.2f}' for x in s['load'])}",
            f"{g}invariant{r} no look-ahead, replay == live",
            "",
            f"{g}gh{r}        github.com/{LOGIN}",
            f"{g}web{r}       yamadablog.github.io/{LOGIN}",
            f"{g}oss{r}       npm i @pulse-music/core",
            "", f"{y}$ echo 'measure, then believe'{r}"]
    out = [f"{v}{ring[i]}{r}   {info[i] if i < len(info) else ''}" for i in range(len(ring))]
    return "\n" + "\n".join(out) + "\n\n"


def patch_readme(s) -> None:
    p = ROOT / "README.md"
    txt = p.read_text(encoding="utf-8")
    line = (f"<samp>calibrated {s['today']} . {s['total']:,} contributions . {s['active']} active days . "
            f"longest streak {s['best']}d . load avg {' / '.join(f'{x:.2f}' for x in s['load'])}</samp>")
    txt = re.sub(r"<!-- telemetry -->.*?<!-- /telemetry -->", f"<!-- telemetry -->\n{line}\n<!-- /telemetry -->", txt, flags=re.S)
    put(p, txt)


def main() -> None:
    if "--snapshot" in sys.argv:
        refresh_snapshot()
    snap = json.loads(SNAPSHOT.read_text())
    days = calendar()
    s = stats(days, snap)
    ASSETS.mkdir(exist_ok=True)
    for name, th in THEMES.items():
        put(ASSETS / f"banner-{name}.svg", banner_svg(days, s, th))
        put(ASSETS / f"header-{name}.svg", header_svg(s, th))
        put(ASSETS / f"seismo-{name}.svg", seismo_svg(days, s, th))
        put(ASSETS / f"spectrum-{name}.svg", spectrum_svg(s, th))
        put(ASSETS / f"life-{name}.svg", life_svg(days, th))
        put(ASSETS / f"punch-{name}.svg", punch_svg(snap, th))
        put(ASSETS / f"sigil-{name}.svg", sigil_svg(th))
    put(SITE / "card.txt", card_txt(s))
    put(SITE / "stats.json", json.dumps({**s, "series": [[d.isoformat(), c] for d, c in days]}) + "\n")
    patch_readme(s)
    print(f"forged: {s['total']} contributions, peak {s['peak']} on {s['peak_d']}, load {s['load']}")


if __name__ == "__main__":
    main()
