#!/usr/bin/env python3
"""terminal.py -- the lab terminal that visitors can actually type into.

A README cannot run JavaScript. So interactivity is borrowed from GitHub itself:
a link pre-fills an issue, an Action parses the title, appends the invocation to
lab/session.json, re-renders this SVG and commits it. The terminal is real state,
shared by strangers.

Only whitelisted commands render. Nothing a visitor writes is ever drawn --
the command must match a key below exactly, and the only free-ish value is the
GitHub login, which is charset-constrained and additionally sanitised here.

    python scripts/terminal.py --run "<raw issue title>" --user "<login>"
    python scripts/terminal.py                 # re-render only
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LAB, ASSETS = ROOT / "lab", ROOT / "assets"
SESSION = LAB / "session.json"
KEEP = 7
MONO = "'JetBrains Mono','SFMono-Regular',Consolas,'Liberation Mono',Menlo,monospace"

THEMES = {
    "dark":  dict(bg="#0b0e14", line="#1c2230", fg="#d6dce8", mute="#6b7489",
                  acc="#7ee7c4", vio="#b69cff", warn="#ffcf70", red="#ff7b86"),
    "light": dict(bg="#fbfaf7", line="#e2e0d8", fg="#1b1f29", mute="#6a7182",
                  acc="#0f8a6a", vio="#6a4fd6", warn="#a86a00", red="#c43d4b"),
}

# command -> (output lines, colour key). The ONLY things that can ever be drawn.
COMMANDS: dict[str, tuple[list[str], str]] = {
    "whoami":       (["mao. research infrastructure, agent orchestration, security research."], "fg"),
    "uname -a":     (["mao@lab 6.x #1 SMP PREEMPT win11+wsl2 x86_64 -- bench online"], "fg"),
    "ls /bench":    (["research-infra  orchestration  reverse-eng  security  systems  applied-ml  frontend"], "vio"),
    "probe --all":  (["[ ok ] 7 slots responding. pull a drawer below for any of them."], "acc"),
    "cat invariants": (["assert backtest.process_bar is live.process_bar",
                        "assert transition in contract or requires_human()"], "acc"),
    "dmesg | tail": (["[ 2.31] unattended mode: gated on purpose",
                      "[ 3.99] it stops and asks"], "warn"),
    "nmap mao":     (["refused. no target without a written scope."], "red"),
    "sudo hire-me": (["[sudo] access granted. github.com/YamadaBlog"], "acc"),
    "coffee":       (["brewing. ETA: before the next backtest finishes."], "warn"),
}


def load() -> list[dict]:
    if SESSION.exists():
        try:
            return json.loads(SESSION.read_text(encoding="utf-8"))["history"]
        except Exception:
            pass
    return []


def put(path: Path, text: str) -> None:
    path.write_bytes(text.encode("utf-8"))


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def clean_user(login: str) -> str:
    """GitHub logins are [A-Za-z0-9-]; anything else is not a login."""
    return (re.sub(r"[^A-Za-z0-9\-]", "", login or "")[:39]) or "anon"


def record(title: str, user: str) -> bool:
    """Parse `lab|<cmd>` from an issue title. Returns True if state changed."""
    m = re.match(r"\s*lab\s*\|\s*(.+?)\s*$", title or "", re.I)
    if not m:
        return False
    cmd = re.sub(r"\s+", " ", m.group(1)).strip().lower()
    if cmd not in COMMANDS:                      # whitelist, no exceptions
        return False
    hist = [h for h in load() if h.get("cmd") != cmd or h.get("user") != clean_user(user)]
    hist.append({"user": clean_user(user), "cmd": cmd,
                 "at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M")})
    LAB.mkdir(exist_ok=True)
    put(SESSION, json.dumps({"history": hist[-KEEP:]}, indent=2) + "\n")
    return True


def render(th: dict) -> str:
    hist = load()
    W, line_h = 1000, 21
    rows, y = [], 60
    if not hist:
        rows.append(f'<text x="24" y="{y}" fill="{th["mute"]}">'
                    f'no one has run anything yet. the links below are live.</text>')
        y += line_h
    for h in hist:
        out, colour = COMMANDS.get(h["cmd"], ([], "mute"))
        rows.append(
            f'<text x="24" y="{y}"><tspan fill="{th["acc"]}">$</tspan>'
            f'<tspan fill="{th["fg"]}"> {esc(h["cmd"])}</tspan>'
            f'<tspan fill="{th["mute"]}">   # @{esc(h["user"])} . {esc(h["at"])}</tspan></text>')
        y += line_h
        for o in out:
            rows.append(f'<text x="24" y="{y}" fill="{th[colour]}">{esc(o)}</text>')
            y += line_h
        y += 6
    H = max(200, y + 34)
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-label="Shared lab terminal: the last {len(hist)} commands run by visitors.">
<style>text{{font-family:{MONO};font-size:13px}}.cur{{animation:b 1.1s steps(1) infinite}}@keyframes b{{50%{{opacity:0}}}}</style>
<rect width="{W}" height="{H}" rx="12" fill="{th['bg']}"/>
<rect x=".5" y=".5" width="{W-1}" height="{H-1}" rx="12" fill="none" stroke="{th['line']}"/>
<circle cx="24" cy="22" r="5" fill="#ff5f57"/><circle cx="41" cy="22" r="5" fill="#febc2e"/><circle cx="58" cy="22" r="5" fill="#28c840"/>
<text x="{W/2}" y="27" text-anchor="middle" fill="{th['mute']}" style="font-size:12px">/dev/lab . shared session . anyone may type</text>
<line x1="0" y1="40" x2="{W}" y2="40" stroke="{th['line']}"/>
{"".join(rows)}
<text x="24" y="{H-16}"><tspan fill="{th['acc']}">$</tspan><tspan class="cur" fill="{th['acc']}"> _</tspan></text>
<text x="{W-24}" y="{H-16}" text-anchor="end" fill="{th['mute']}" style="font-size:11px">last {KEEP} invocations . whitelisted commands only</text>
</svg>'''


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default=None, help="raw issue title")
    ap.add_argument("--user", default="anon")
    a = ap.parse_args()
    changed = record(a.run, a.user) if a.run else False
    ASSETS.mkdir(exist_ok=True)
    for name, th in THEMES.items():
        put(ASSETS / f"terminal-{name}.svg", render(th))
    print(f"accepted={changed} history={len(load())}")


if __name__ == "__main__":
    main()
