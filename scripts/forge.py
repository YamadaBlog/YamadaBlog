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


def refresh_snapshot() -> None:
    repos, _ = api("user/repos?per_page=100&affiliation=owner")
    langs: dict[str, int] = {}
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
    DATA.mkdir(exist_ok=True)
    put(SNAPSHOT, json.dumps({
        "taken": dt.date.today().isoformat(),
        "repos": n, "public": sum(1 for r in repos if not r["private"]),
        "commits": commits, "languages": dict(sorted(langs.items(), key=lambda kv: -kv[1])),
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
        delay = 1.5 + i * 0.16
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
        tags += (f'<g class="rw" style="animation-delay:{2.6 + i*0.14:.2f}s">'
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
    put(SITE / "card.txt", card_txt(s))
    put(SITE / "stats.json", json.dumps({**s, "series": [[d.isoformat(), c] for d, c in days]}) + "\n")
    patch_readme(s)
    print(f"forged: {s['total']} contributions, peak {s['peak']} on {s['peak_d']}, load {s['load']}")


if __name__ == "__main__":
    main()
