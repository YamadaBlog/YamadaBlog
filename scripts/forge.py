#!/usr/bin/env python3
"""forge.py -- renders every generated asset of this profile from real data.

    python scripts/forge.py              # calendar + traffic -> assets, README, site
    python scripts/forge.py --snapshot   # refresh data/snapshot.json (walks repos; slow)

stdlib only. No third-party widget, no hosted card service: every pixel is built here.

Two privacy rules are structural, not cosmetic:
  . the snapshot stores AGGREGATES only -- never a private repository name
  . visitor figures come from GitHub's own Traffic API, which is already
    aggregated. No cookie, no script, no fingerprint, nothing per-person.
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

# Hue carries meaning, never decoration:
#   cyan = nominal signal . steel = structure . amber = elevated . rose = refused
THEMES = {
    "dark": dict(
        bg="#080b12", panel="#0c111c", line="#18202f", grid="#111827",
        fg="#ccd6e8", mute="#5f6e88", dim="#39445a",
        cyan="#5ad2e8", steel="#8fa8ff", amber="#f5b95c", rose="#ff6b9d", violet="#b08cff",
        ink=".62", glow=".26"),
    "light": dict(
        bg="#f7f8fb", panel="#eef1f7", line="#dde3ee", grid="#e7ecf5",
        fg="#131924", mute="#5c6880", dim="#97a2b8",
        cyan="#0e7d93", steel="#3f5bd4", amber="#a06708", rose="#c02f69", violet="#6a4fd6",
        ink=".88", glow=".14"),
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


def traffic() -> dict:
    """Aggregated, first-party visit counts from GitHub's own Traffic API.

    GitHub does the aggregating; we only read totals. There is no cookie, no
    script and no identifier anywhere in this path, and nothing returned can
    single out a visitor. Needs push access, so in CI only this repo resolves.
    Any failure degrades to 'unavailable' rather than to a fabricated number.
    """
    out = {"views": None, "uniques": None, "clones": None, "referrer": None, "series": [], "asof": None}
    repo = f"{LOGIN}/{LOGIN}"
    try:
        v, _ = api(f"repos/{repo}/traffic/views")
        out["views"], out["uniques"] = v.get("count"), v.get("uniques")
        out["series"] = [d.get("uniques", 0) for d in v.get("views", [])]
    except Exception:
        pass
    try:
        out["clones"] = api(f"repos/{repo}/traffic/clones")[0].get("count")
    except Exception:
        pass
    try:
        r = api(f"repos/{repo}/traffic/popular/referrers")[0]
        if r:
            out["referrer"] = r[0].get("referrer")
    except Exception:
        pass

    # The Traffic API needs push access, which the default CI token does not
    # carry. Rather than show "--" on every nightly run, remember the last
    # reading and label it with its date -- stale but true, never invented.
    cache = DATA / "traffic.json"
    if out["views"] is not None:
        out["asof"] = dt.date.today().isoformat()
        put(cache, json.dumps(out, indent=2) + "\n")
    elif cache.exists():
        try:
            out = json.loads(cache.read_text())
        except Exception:
            pass
    return out


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


def stats(days: list[tuple[dt.date, int]], snap: dict) -> dict:
    today = days[-1][0]
    counts = [c for _, c in days]
    total = sum(counts)
    active = sum(1 for c in counts if c)
    run = best = 0
    for c in counts:
        run = run + 1 if c else 0
        best = max(best, run)
    peak_d, peak = max(days, key=lambda x: x[1])
    load = [round(sum(counts[-n:]) / n, 2) for n in (7, 30, 90)]
    wk = [0] * 7
    for d, c in days:
        wk[d.weekday()] += c
    born = dt.date(2022, 11, 18)
    y, m = divmod((today.year - born.year) * 12 + today.month - born.month, 12)
    langs = {k: v for k, v in snap["languages"].items() if k in AUTHORED}
    return dict(today=today.isoformat(), total=total, active=active, days=len(days), best=best,
                peak=peak, peak_d=peak_d.isoformat(), load=load, weekday=wk, uptime=f"{y}y {m}m",
                commits=snap["commits"], repos=snap["repos"], public=snap["public"],
                punch=snap.get("punch"), langs=langs,
                src_mb=round(sum(langs.values()) / 1e6, 1), snap=snap["taken"])


# ---------------------------------------------------------------- helpers
def put(path: Path, text: str) -> None:
    path.write_bytes(text.encode("utf-8"))          # LF on every OS


def esc(s) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def ramp(t: float, th: dict) -> str:
    """cyan -> steel -> amber as intensity rises. Colour states a level."""
    return th["cyan"] if t < 0.45 else (th["steel"] if t < 0.78 else th["amber"])


def hours(st: dict) -> list[int]:
    p = st.get("punch") or [[0] * 24 for _ in range(7)]
    return [sum(p[d][h] for d in range(7)) for h in range(24)]


# ---------------------------------------------------------------- svg: observer
def sigil_svg(days, st: dict, th: dict) -> str:
    """The Observer: concentric instrument rings around a living iris.

    Nothing here is ornament.
      . the 24 outer ticks are hours, lit by the commits that actually land in them
      . the inner gauge is active days / 365
      . the iris is Conway's Life seeded by the contribution year, drifting slowly
      . the scan sweeps once every 18s -- slow enough to ignore while reading
    """
    S = 468
    c = S / 2
    counts = sorted(x for _, x in days if x)
    med = counts[len(counts) // 2] if counts else 1

    # --- iris: the whole year as a polar rosette.
    # angle = position in the year, radius = that day's intensity. A quiet day
    # sits near the pupil, a heavy one reaches for the rim. Nothing decays,
    # because nothing is simulated: this is the data itself, seen end-on.
    R_IRIS, R_MIN = 88, 14
    # radius by RANK, not by raw ratio: a handful of huge days would otherwise
    # pin every ordinary day to the pupil. Rank spreads the year across the disc.
    order = sorted(range(len(days)), key=lambda j: days[j][1])
    rank = [0.0] * len(days)
    for pos, j in enumerate(order):
        rank[j] = pos / max(len(days) - 1, 1)
    dots = []
    for i, (_, n) in enumerate(days):
        ang = 2 * math.pi * i / len(days) - math.pi / 2
        lvl = rank[i]
        r = R_MIN + (R_IRIS - R_MIN - 3) * lvl
        x, y = c + r * math.cos(ang), c + r * math.sin(ang)
        sz = 2.0 + 2.8 * lvl
        col = ramp(lvl, th) if n else th["dim"]
        dots.append(f'<rect x="{x-sz/2:.1f}" y="{y-sz/2:.1f}" width="{sz:.1f}" height="{sz:.1f}" '
                    f'rx="{sz/2:.1f}" fill="{col}" fill-opacity="{(0.18 + 0.82*lvl) if n else 0.13:.2f}"/>')
    iris = (f'<g class="rot" style="transform-origin:{c}px {c}px">{"".join(dots)}</g>')

    by_h = hours(st)
    mx = max(by_h) or 1
    ticks = []
    for h in range(24):
        a = math.radians(h * 15 - 90)
        lvl = by_h[h] / mx
        r0, r1 = 186, 186 + (5 + 11 * lvl if lvl else 3)
        col = ramp(lvl, th) if lvl else th["dim"]
        ticks.append(f'<line x1="{c+r0*math.cos(a):.1f}" y1="{c+r0*math.sin(a):.1f}" '
                     f'x2="{c+r1*math.cos(a):.1f}" y2="{c+r1*math.sin(a):.1f}" '
                     f'stroke="{col}" stroke-width="{2.4 if lvl > .5 else 1.3}" stroke-linecap="round" '
                     f'opacity="{0.3 + 0.7*lvl:.2f}"/>')

    cover = st["active"] / 365
    ex, ey = c + 142 * math.cos(math.radians(-18)), c + 142 * math.sin(math.radians(-18))
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{S}" height="{S}" viewBox="0 0 {S} {S}" role="img" aria-label="An observer sigil: twenty-four hour ticks lit by real commit activity, a gauge showing {st['active']} active days out of 365, and an iris of cellular automata seeded by the contribution year.">
<style>
.gg{{animation:gg 2.2s cubic-bezier(.3,0,.2,1) .3s}}
@keyframes gg{{from{{stroke-dashoffset:1}}}}
.rot{{animation:rot 240s linear infinite}}
@keyframes rot{{to{{transform:rotate(360deg)}}}}
.scan{{animation:rot 18s linear infinite}}
</style>
<defs>
  <clipPath id="ir"><circle cx="{c}" cy="{c}" r="{R_IRIS}"/></clipPath>
  <radialGradient id="co"><stop offset="0" stop-color="{th['cyan']}" stop-opacity="{th['glow']}"/><stop offset="1" stop-color="{th['cyan']}" stop-opacity="0"/></radialGradient>
  <linearGradient id="sw" x1="0" x2="1"><stop offset="0" stop-color="{th['cyan']}" stop-opacity="0"/><stop offset="1" stop-color="{th['cyan']}" stop-opacity=".85"/></linearGradient>
</defs>
<rect width="{S}" height="{S}" rx="14" fill="{th['bg']}"/>
<circle cx="{c}" cy="{c}" r="{R_IRIS+40}" fill="url(#co)"/>
<circle cx="{c}" cy="{c}" r="142" fill="none" stroke="{th['line']}"/>
<circle cx="{c}" cy="{c}" r="186" fill="none" stroke="{th['line']}"/>
{"".join(ticks)}
<circle cx="{c}" cy="{c}" r="164" fill="none" stroke="{th['line']}" stroke-width="3"/>
<circle class="gg" cx="{c}" cy="{c}" r="164" fill="none" stroke="{th['steel']}" stroke-width="3" stroke-linecap="round"
        pathLength="1" stroke-dasharray="1" stroke-dashoffset="{1-cover:.4f}" transform="rotate(-90 {c} {c})"/>
<g clip-path="url(#ir)">{iris}</g>
<circle cx="{c}" cy="{c}" r="{R_IRIS}" fill="none" stroke="{th['line']}" stroke-width="2"/>
<circle cx="{c}" cy="{c}" r="12" fill="{th['bg']}" stroke="{th['cyan']}" stroke-width="1.4"/>
<circle cx="{c}" cy="{c}" r="3.5" fill="{th['cyan']}"/>
<g class="scan" style="transform-origin:{c}px {c}px">
  <path d="M{c},{c-142} A142,142 0 0 1 {ex:.1f},{ey:.1f}" fill="none" stroke="url(#sw)" stroke-width="2.4" stroke-linecap="round"/>
</g>
<text x="{c}" y="{S-16}" text-anchor="middle" font-family={MONO!r} font-size="10.5" fill="{th['mute']}" letter-spacing="1">OBSERVER . CONTINUOUS . {st['active']}/365 COVERED</text>
</svg>'''


# ---------------------------------------------------------------- svg: system state
def state_svg(st: dict, tr: dict, th: dict) -> str:
    """One console in place of four separate charts."""
    W, H = 1000, 262
    by_h = hours(st)
    tot = sum(by_h) or 1
    mx = max(by_h) or 1
    peak = by_h.index(mx)
    focus = sum(sorted(by_h, reverse=True)[:6]) / tot
    cadence = min(st["load"][0] / max(st["load"][2], 0.01), 2) / 2
    cover = st["active"] / 365

    def gauge(x, y, val, label, shown, state):
        col = ramp(val, th)
        return (f'<circle cx="{x}" cy="{y}" r="29" fill="none" stroke="{th["line"]}" stroke-width="5"/>'
                f'<circle cx="{x}" cy="{y}" r="29" fill="none" stroke="{col}" stroke-width="5" stroke-linecap="round"'
                f' pathLength="1" stroke-dasharray="1" stroke-dashoffset="{1-min(val,1):.4f}"'
                f' transform="rotate(-90 {x} {y})" class="gg"/>'
                f'<text x="{x}" y="{y+4}" text-anchor="middle" fill="{th["fg"]}" style="font-size:13.5px;font-weight:600">{shown}</text>'
                f'<text x="{x}" y="{y+47}" text-anchor="middle" fill="{th["mute"]}" style="font-size:9.5px;letter-spacing:.07em">{label}</text>'
                f'<text x="{x}" y="{y+60}" text-anchor="middle" fill="{col}" style="font-size:9px;letter-spacing:.06em">{state}</text>')

    gauges = (gauge(96, 112, cover, "COVERAGE", f"{st['active']}", "SUSTAINED" if cover > .6 else "INTERMITTENT")
              + gauge(198, 112, cadence, "CADENCE", f"{st['load'][0]:.0f}/d", "ELEVATED" if cadence > .55 else "NOMINAL")
              + gauge(300, 112, focus, "FOCUS", f"{focus*100:.0f}%", "CONCENTRATED" if focus > .45 else "DIFFUSE"))

    bx, by, bw = 398, 152, 9.4
    bars = "".join(
        f'<rect x="{bx + h*bw:.1f}" y="{by - 52*(by_h[h]/mx):.1f}" width="{bw-2.6:.1f}" '
        f'height="{max(52*(by_h[h]/mx), 1.5):.1f}" rx="1.4" fill="{ramp(by_h[h]/mx, th)}" '
        f'fill-opacity="{0.4 + 0.6*(by_h[h]/mx):.2f}"><title>{h:02d}:00 . {by_h[h]} commits</title></rect>'
        for h in range(24))
    hrs = "".join(f'<text x="{bx + h*bw + bw/2:.1f}" y="{by+14}" text-anchor="middle" fill="{th["dim"]}" style="font-size:9px">{h:02d}</text>'
                  for h in range(0, 24, 6))

    langs = list(st["langs"].items())
    lt = sum(v for _, v in langs) or 1
    pal = [th["cyan"], th["steel"], th["violet"], th["amber"], th["rose"]]
    cur, segs, leg = 398, [], []
    for i, (k, v) in enumerate(langs):
        w = 352 * v / lt
        col = pal[i] if i < 5 else th["dim"]
        segs.append(f'<rect x="{cur:.1f}" y="198" width="{max(w-1.4, 1):.1f}" height="7" rx="2" fill="{col}">'
                    f'<title>{esc(k)} {100*v/lt:.1f}%</title></rect>')
        if i < 4:
            leg.append(f'<text x="{398 + i*90}" y="226" fill="{col}" style="font-size:10px">{esc(k.lower())}</text>'
                       f'<text x="{398 + i*90}" y="239" fill="{th["mute"]}" style="font-size:10px">{100*v/lt:.1f}%</text>')
        cur += w

    v, u, cl = tr.get("views"), tr.get("uniques"), tr.get("clones")
    ref = tr.get("referrer") or "direct"
    spark = ""
    if tr.get("series"):
        s_ = tr["series"][-14:]
        m_ = max(s_) or 1
        spark = "".join(
            f'<rect x="{798 + i*9.6:.1f}" y="{136 - 22*(x/m_):.1f}" width="6" height="{max(22*(x/m_), 1.5):.1f}" '
            f'rx="1.2" fill="{th["cyan"]}" fill-opacity=".5"/>' for i, x in enumerate(s_))

    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-label="System state console: {st['active']} of 365 days covered, {st['load'][0]:.0f} contributions per day, peak commit hour {peak:02d}:00, and {v if v is not None else 'no'} views on this repository in the last 14 days.">
<style>text{{font-family:{MONO};font-size:11px}}
.pl{{animation:p 3.6s ease-in-out infinite}}@keyframes p{{0%,100%{{opacity:.3}}50%{{opacity:1}}}}
.gg{{animation:gg 1.6s cubic-bezier(.3,0,.2,1) .35s}}@keyframes gg{{from{{stroke-dashoffset:1}}}}</style>
<rect width="{W}" height="{H}" rx="12" fill="{th['bg']}"/>
<rect x=".5" y=".5" width="{W-1}" height="{H-1}" rx="12" fill="none" stroke="{th['line']}"/>
<line x1="0" y1="34" x2="{W}" y2="34" stroke="{th['line']}"/>
<circle cx="22" cy="21" r="3.5" fill="{th['cyan']}" class="pl"/>
<text x="36" y="25" fill="{th['fg']}" style="letter-spacing:.11em">SYSTEM STATE</text>
<text x="{W-18}" y="25" text-anchor="end" fill="{th['mute']}">sampled {st['today']} . recomputed nightly</text>
<line x1="352" y1="46" x2="352" y2="{H-16}" stroke="{th['line']}"/>
<line x1="768" y1="46" x2="768" y2="{H-16}" stroke="{th['line']}"/>
{gauges}
<text x="398" y="70" fill="{th['mute']}" style="letter-spacing:.09em">RHYTHM . commits by local hour</text>
<text x="752" y="70" text-anchor="end" fill="{th['amber']}">peak {peak:02d}:00</text>
{bars}{hrs}
<text x="398" y="184" fill="{th['mute']}" style="letter-spacing:.09em">COMPOSITION . {st['src_mb']} MB authored</text>
{"".join(segs)}{"".join(leg)}
<text x="790" y="70" fill="{th['mute']}" style="letter-spacing:.09em">OBSERVATION . 14d</text>
<text x="{W-18}" y="70" text-anchor="end" fill="{th['dim']}" style="font-size:9px">{("as of " + tr["asof"]) if tr.get("asof") else ""}</text>
<text x="790" y="99" fill="{th['fg']}" style="font-size:20px;font-weight:600">{v if v is not None else "--"}</text>
<text x="790" y="115" fill="{th['mute']}">views . {u if u is not None else "--"} unique</text>
{spark}
<text x="790" y="166" fill="{th['mute']}">source</text>
<text x="790" y="181" fill="{th['steel']}">{esc(ref)[:20]}</text>
<text x="790" y="203" fill="{th['mute']}">clones</text>
<text x="790" y="218" fill="{th['steel']}">{cl if cl is not None else "--"}</text>
<text x="790" y="240" fill="{th['dim']}" style="font-size:9px">aggregated by github.</text>
<text x="790" y="251" fill="{th['dim']}" style="font-size:9px">no cookie. no script. no id.</text>
</svg>'''


# ---------------------------------------------------------------- svg: signal
def signal_svg(days, st, th) -> str:
    """A year of daily contributions. Hue rises with amplitude, so peaks read as events."""
    W, H, pl, pr, mid = 1000, 182, 44, 26, 94
    n = len(days)
    step = (W - pl - pr) / n
    peak = max(1, st["peak"])
    segs, ticks, last, lastx = [], [], None, -99
    for i, (d, cnt) in enumerate(days):
        x = pl + i * step
        if cnt:
            t = (cnt / peak) ** 0.55
            a = 4 + 62 * t
            segs.append(f'<line x1="{x+step/2:.1f}" y1="{mid-a:.1f}" x2="{x+step/2:.1f}" y2="{mid+a*0.7:.1f}" '
                        f'stroke="{ramp(t, th)}" stroke-width="{step*0.8:.2f}" stroke-opacity="{0.4+0.6*t:.2f}"/>')
        if d.month != last and x - lastx > 44:
            last, lastx = d.month, x
            ticks.append(f'<text x="{x:.1f}" y="{H-11}" fill="{th["dim"]}">{d.strftime("%b").lower()}</text>')
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-label="Daily contribution signal across 365 days, peaking at {st['peak']} on {st['peak_d']}.">
<style>text{{font-family:{MONO};font-size:10.5px}}</style>
<rect width="{W}" height="{H}" rx="12" fill="{th['bg']}"/>
<rect x=".5" y=".5" width="{W-1}" height="{H-1}" rx="12" fill="none" stroke="{th['line']}"/>
<text x="{pl}" y="24" fill="{th['mute']}" style="letter-spacing:.09em">SIGNAL . 365d . amplitude ~ contributions</text>
<text x="{W-pr}" y="24" text-anchor="end" fill="{th['amber']}">max {st['peak']} . {st['peak_d']}</text>
<line x1="{pl}" y1="{mid}" x2="{W-pr}" y2="{mid}" stroke="{th['line']}"/>
{"".join(segs)}{"".join(ticks)}
</svg>'''


# ---------------------------------------------------------------- svg: masthead
DOMAINS = [("research infrastructure", "cyan"), ("agent orchestration", "cyan"),
           ("reverse engineering", "steel"), ("security research", "steel"),
           ("systems & automation", "violet"), ("applied ML", "violet")]


def banner_svg(days, st: dict, th: dict) -> str:
    W, H = 1100, 322
    tail = days[-180:]
    peak = max(1, max(c for _, c in tail))
    step = W / len(tail)
    pts = " ".join(f"{i*step:.1f},{H-40-30*(c/peak)**0.7:.1f}" for i, (_, c) in enumerate(tail))

    rows, y = [], 104
    for i, (d, key) in enumerate(DOMAINS):
        rows.append(
            f'<g>'
            f'<circle cx="52" cy="{y-5}" r="3" fill="{th[key]}"/>'
            f'<text x="68" y="{y}" fill="{th["fg"]}">{esc(d)}</text></g>')
        y += 25

    tags, x = "", 620
    for i, (t, key) in enumerate([("no look-ahead", "cyan"), ("replay == live", "steel"),
                                  ("scope first", "violet"), ("it stops and asks", "amber")]):
        w = len(t) * 7.7 + 20
        tags += (f'<g>'
                 f'<rect x="{x}" y="{92 + (i//2)*32}" width="{w:.0f}" height="22" rx="11" fill="none" stroke="{th[key]}" stroke-opacity=".5"/>'
                 f'<text x="{x+10}" y="{107 + (i//2)*32}" fill="{th[key]}" style="font-size:11.5px">{esc(t)}</text></g>')
        x = 620 if i % 2 else x + w + 10

    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-label="mao@lab -- research infrastructure, agent orchestration, reverse engineering, security research. {st['total']} contributions in the last year.">
<style>
text{{font-family:{MONO};font-size:14.5px}}
.cur{{animation:bl 1.2s steps(1) infinite 1s}}
@keyframes bl{{50%{{opacity:0}}}}
.sc{{animation:sc 9s cubic-bezier(.4,0,.6,1) infinite}}
@keyframes sc{{from{{transform:translateY(-70px)}}to{{transform:translateY({H}px)}}}}
</style>
<defs>
  <linearGradient id="ti" x1="0" x2="1"><stop offset="0" stop-color="{th['steel']}"/><stop offset="1" stop-color="{th['cyan']}"/></linearGradient>
  <pattern id="gr" width="28" height="28" patternUnits="userSpaceOnUse"><path d="M28 0H0v28" fill="none" stroke="{th['grid']}" stroke-width="1"/></pattern>
  <linearGradient id="sg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{th['cyan']}" stop-opacity="0"/><stop offset="1" stop-color="{th['cyan']}" stop-opacity=".06"/></linearGradient>
</defs>
<rect width="{W}" height="{H}" rx="14" fill="{th['bg']}"/>
<rect width="{W}" height="{H}" rx="14" fill="url(#gr)"/>
<rect class="sc" width="{W}" height="70" fill="url(#sg)"/>
<polyline points="{pts}" fill="none" stroke="{th['cyan']}" stroke-width="1.1" stroke-opacity=".36"/>
<rect x=".5" y=".5" width="{W-1}" height="{H-1}" rx="14" fill="none" stroke="{th['line']}"/>
<circle cx="26" cy="24" r="5" fill="{th['rose']}" opacity=".7"/><circle cx="43" cy="24" r="5" fill="{th['amber']}" opacity=".7"/><circle cx="60" cy="24" r="5" fill="{th['cyan']}" opacity=".7"/>
<text x="{W/2}" y="29" text-anchor="middle" fill="{th['mute']}" style="font-size:11.5px">mao@lab: ~/bench . observation active</text>
<text x="34" y="72"><tspan fill="{th['cyan']}">$</tspan><tspan fill="{th['fg']}"> probe --all</tspan><tspan class="cur" fill="{th['cyan']}">_</tspan></text>
{"".join(rows)}
<text x="620" y="64" style="font-size:36px;font-weight:700" fill="url(#ti)">mao@lab</text>
{tags}
<line x1="0" y1="{H-34}" x2="{W}" y2="{H-34}" stroke="{th['line']}"/>
<text x="34" y="{H-14}" fill="{th['dim']}" style="font-size:11px">180d signal</text>
<text x="{W-34}" y="{H-14}" text-anchor="end" fill="{th['mute']}" style="font-size:11px">{st['total']:,} contributions . {st['active']}/365 active . streak {st['best']}d . {st['src_mb']} MB authored</text>
</svg>'''


# ---------------------------------------------------------------- svg: session
def session_svg(st: dict, tr: dict, th: dict) -> str:
    """A terminal session that types itself out. Every value is real.

    No input, no state, nothing external can write to it. The typewriter is a
    background-coloured rect animated from full width to zero; its *attribute*
    width is 0, so a renderer that ignores SMIL shows the finished session
    rather than a blank box. The failure mode is 'already typed', never
    'invisible'.
    """
    CH, LH, X0, Y0 = 7.8, 22, 22, 56
    by_h = hours(st)
    tot = sum(by_h) or 1
    peak = by_h.index(max(by_h)) if any(by_h) else 0
    night = sum(by_h[h] for h in list(range(0, 7)) + [23])
    top = list(st["langs"])[:3]
    seen = f"{tr['views']} views . {tr['uniques']} unique" if tr.get("views") is not None else "unavailable"

    P, O, D, W_, C = "p", "o", "d", "w", "c"
    lines = [
        (P, "observe --self"),
        (O, f"coverage     {st['active']}/365 days . streak {st['best']}d . {st['total']:,} contributions"),
        (O, f"source       {st['src_mb']} MB authored . {st['repos']} repos ({st['public']} public)"),
        (O, f"composition  {' . '.join(top)}"),
        (W_, f"rhythm       peak {peak:02d}:00 local . {100*night//tot}% of commits outside 07-23"),
        (P, "observe --inbound"),
        (O, f"this node    {seen} . 14d, aggregated by github"),
        (D, "             no cookie, no script, nothing per-person"),
        (P, "echo $INVARIANT"),
        (C, "it stops and asks"),
    ]

    t, rows = 0.35, []
    for ln, (kind, txt) in enumerate(lines):
        y = Y0 + ln * LH
        if kind == P:
            body = (f'<tspan fill="{th["cyan"]}">mao@lab</tspan><tspan fill="{th["mute"]}">:~$ </tspan>'
                    f'<tspan fill="{th["fg"]}">{esc(txt)}</tspan>')
            n, speed = 11 + len(txt), 0.045
        else:
            col = {O: th["fg"], D: th["dim"], W_: th["amber"], C: th["cyan"]}[kind]
            body = f'<tspan fill="{col}">{esc(txt)}</tspan>'
            n, speed = len(txt), 0.006
        dur = max(n * speed, 0.12)
        w = n * CH + 10
        rows.append(f'<text x="{X0}" y="{y}" xml:space="preserve">{body}</text>')
        t += dur + (0.30 if kind == P else 0.06)

    H, W = Y0 + len(lines) * LH + 26, 1000
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-label="A terminal session reporting {st['active']} active days, {st['src_mb']} MB of authored source, a peak commit hour of {peak:02d}:00 local, and {seen} inbound over 14 days.">
<style>
text{{font-family:{MONO};font-size:13.5px}}
/* the only motion is the prompt: nothing here can ever hide its own content */
.cur{{animation:bl 1.2s steps(1) infinite}}
@keyframes bl{{50%{{opacity:0}}}}
</style>
<rect width="{W}" height="{H}" rx="12" fill="{th['bg']}"/>
<circle cx="24" cy="23" r="4.5" fill="{th['rose']}" opacity=".7"/><circle cx="41" cy="23" r="4.5" fill="{th['amber']}" opacity=".7"/><circle cx="58" cy="23" r="4.5" fill="{th['cyan']}" opacity=".7"/>
<text x="{W/2}" y="28" text-anchor="middle" fill="{th['mute']}" style="font-size:11.5px">mao@lab: ~ . session recorded at forge time . read-only</text>
<line x1="0" y1="38" x2="{W}" y2="38" stroke="{th['line']}"/>
{"".join(rows)}
<text x="{X0}" y="{Y0 + len(lines)*LH}" xml:space="preserve"><tspan fill="{th['cyan']}">mao@lab</tspan><tspan fill="{th['mute']}">:~$ </tspan><tspan class="cur" fill="{th['cyan']}">&#9608;</tspan></text>
<rect x=".5" y=".5" width="{W-1}" height="{H-1}" rx="12" fill="none" stroke="{th['line']}"/>
</svg>'''


# ---------------------------------------------------------------- text outputs
def card_txt(st: dict, tr: dict) -> str:
    c, s_, d, a, r = "\033[38;5;80m", "\033[38;5;111m", "\033[38;5;243m", "\033[38;5;215m", "\033[0m"
    by_h = hours(st)
    peak = by_h.index(max(by_h)) if any(by_h) else 0
    seen = f"{tr['views']} views / {tr['uniques']} unique" if tr.get("views") is not None else "unavailable"
    ring = [
        "      .:'''''':.      ",
        "    .'  .----.  '.    ",
        "   /   / .--. \\   \\   ",
        "  |   |  (  )  |   |  ",
        "   \\   \\ '--' /   /   ",
        "    '.  '----'  .'    ",
        "      ':......:'      ",
    ]
    info = [
        f"{c}mao{r}@{s_}lab{r}",
        d + "-" * 44 + r,
        f"{c}observing {r} research infra . orchestration . reverse eng.",
        f"{c}coverage  {r} {st['active']}/365 days . streak {st['best']}d",
        f"{c}cadence   {r} {st['load'][0]:.1f}/d (7d) . {st['load'][2]:.1f}/d (90d)",
        f"{c}rhythm    {r} peak {peak:02d}:00 local",
        f"{c}inbound   {r} {seen} . 14d, aggregated",
        f"{a}invariant {r} it stops and asks",
    ]
    n = max(len(ring), len(info))
    return "\n" + "\n".join(
        f"{s_}{ring[i] if i < len(ring) else ' ' * 22}{r}  {info[i] if i < len(info) else ''}"
        for i in range(n)) + "\n\n"


def patch_readme(st: dict, tr: dict) -> None:
    p = ROOT / "README.md"
    txt = p.read_text(encoding="utf-8")
    seen = f" . {tr['views']} views / {tr['uniques']} unique inbound" if tr.get("views") is not None else ""
    line = (f"<samp>sampled {st['today']} . {st['total']:,} contributions . {st['active']}/365 active . "
            f"streak {st['best']}d . cadence {st['load'][0]:.1f}/d{seen}</samp>")
    txt = re.sub(r"<!-- telemetry -->.*?<!-- /telemetry -->",
                 f"<!-- telemetry -->\n{line}\n<!-- /telemetry -->", txt, flags=re.S)
    put(p, txt)


def main() -> None:
    if "--snapshot" in sys.argv:
        refresh_snapshot()
    snap = json.loads(SNAPSHOT.read_text())
    days = calendar()
    st = stats(days, snap)
    tr = traffic()
    ASSETS.mkdir(exist_ok=True)
    for name, th in THEMES.items():
        put(ASSETS / f"banner-{name}.svg", banner_svg(days, st, th))
        put(ASSETS / f"sigil-{name}.svg", sigil_svg(days, st, th))
        put(ASSETS / f"state-{name}.svg", state_svg(st, tr, th))
        put(ASSETS / f"signal-{name}.svg", signal_svg(days, st, th))
        put(ASSETS / f"session-{name}.svg", session_svg(st, tr, th))
    put(SITE / "card.txt", card_txt(st, tr))
    put(SITE / "stats.json", json.dumps({**st, "traffic": tr,
                                         "series": [[d.isoformat(), c] for d, c in days]}) + "\n")
    patch_readme(st, tr)
    print(f"forged: {st['total']} contributions, peak {st['peak']} on {st['peak_d']}, "
          f"inbound {tr.get('views')} views / {tr.get('uniques')} unique")


if __name__ == "__main__":
    main()
