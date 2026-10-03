#!/usr/bin/env python3
"""
check.py — everything the published site claims about itself, verified mechanically.

The site in site/ is built in the private repository YamadaBlog/portfolio
(`npm run build`, then dist/ is copied here). This is that repository's
tools/check.py, run on what is published: the same invariants, against
site/ instead of dist/, without the unit suite (it lives with the sources).
The forge runs it before every deploy. Standard library only, no network.

The classes of failure it guards against:

  * a shot the reel plays that the page does not list, or in another order
  * a stated contrast ratio that is invented rather than measured
  * an inline <script> with a syntax error, which silently kills the whole
    script block
  * a private path or credential in a published file

Exit status is 1 on any failure, and every failure prints what it measured
next to what the file claimed.
"""
import json
import os
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
# what is published: the built site, copied from the portfolio's dist/
SITE = pathlib.Path(os.environ.get("DIST_DIR") or (ROOT / "site"))
FAIL: list[str] = []


def bad(msg: str) -> None:
    FAIL.append(msg)
    print("  FAIL " + msg)


def ok(msg: str) -> None:
    print("  ok   " + msg)


# ── colour ───────────────────────────────────────────────────────────────────
def html_text(t: str) -> str:
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("'", "&#x27;")


def luminance(hexcol: str) -> float:
    n = int(hexcol.lstrip("#"), 16)

    def lin(v: int) -> float:
        c = v / 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * lin((n >> 16) & 255) + 0.7152 * lin((n >> 8) & 255) + 0.0722 * lin(n & 255)


def contrast(a: str, b: str) -> float:
    la, lb = luminance(a), luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


# ── 1. the registry describes the reel the page plays ─────────────────────────
def check_registry() -> dict:
    print("\n[registry] content.json against the page built from it")
    content = json.loads((SITE / "content.json").read_text(encoding="utf-8"))
    src = (SITE / "index.html").read_text(encoding="utf-8")
    seen = set()
    for s in content["shots"]:
        if s["id"] in seen:
            bad(f"duplicate shot id {s['id']}")
        seen.add(s["id"])
        if not re.fullmatch(r"[a-z][a-z0-9-]*", s["id"]):
            bad(f"{s['id']}: a shot id must be a plain lowercase word")
        # a shot that shows a place maps the place's own scroll track (Noomo's
        # home is ten screens long): it may run to twelve screens; any other
        # shot, four
        top = 1200 if s.get("place") not in (None, "source") and not s.get("passage") else 400
        for k in ("h", "hm"):
            v = s.get(k, s.get("h"))
            if not isinstance(v, (int, float)) or not 40 <= v <= top:
                bad(f"{s['id']}: {k} = {v!r} is not a length a shot can have (40..{top} vh)")
        if f'data-ch="{s["id"]}"' not in src:
            bad(f"{s['id']}: no section in index.html")
        if len(s.get("label", "")) > 90:
            bad(f"{s['id']}: its label is copy, not a label ({len(s['label'])} characters)")
    ok(f"{len(seen)} shots, each with a section, a length and a label")
    order = re.findall(r'<section class="ch" data-ch="([a-z0-9-]+)"', src)
    if order != [s["id"] for s in content["shots"]]:
        bad(f"the page plays {order}, the registry says {[s['id'] for s in content['shots']]}")
    else:
        ok("the page plays the shots in the registry's order")
    return content


# ── 2. the world's own frame ─────────────────────────────────────────────────
def check_world() -> None:
    print("\n[world] the frame on the black, and what the page promises before any script")
    src = (SITE / "index.html").read_text(encoding="utf-8")
    m = re.search(r"--bg:(#[0-9a-f]{6})", src)
    if not m:
        bad("index.html has no --bg")
        return
    void = m.group(1)
    for name in ("ink", "mute", "dim", "amber"):
        mm = re.search(r"--%s:(#[0-9a-f]{6})" % name, src)
        if not mm:
            bad(f"index.html has no --{name}")
            continue
        col = mm.group(1)
        r = contrast(void, col)
        (ok if r >= 4.5 else bad)(f"--{name:5s} {col} on {void}: {r:.2f}:1")

    # The index is the whole site for every machine the scene declines, so it
    # has to be in the HTML rather than built by script.
    reg = json.loads((SITE / "content.json").read_text(encoding="utf-8"))
    for sh in reg["shots"]:
        if html_text(sh["label"]) not in src:
            bad(f"index.html does not list the shot {sh['id']} before any script")
    if f'href="{reg["identity"]["github"]}"' not in src:
        bad("index.html has no server-rendered contact")
    ok("every shot is listed, and the contact is there, in the static HTML")
    if 'id="index"' not in src or '<canvas id="world"' not in src:
        bad("index.html is missing the index or the canvas")

    # the scene starts from the registry inlined in the page, and it must be
    # the registry, not a copy that drifted
    reg = re.search(r'<script type="application/json" id="registry">(.*?)</script>', src, re.S)
    if not reg:
        bad("index.html does not inline the registry")
    else:
        inlined = json.loads(reg.group(1).replace("<\\/", "</"))
        if inlined != json.loads((SITE / "content.json").read_text(encoding="utf-8")):
            bad("the inlined registry differs from content.json")
        else:
            ok("the inlined registry is content.json")
    if 'src="./world/main.js"' not in src:
        bad("index.html never loads the world")

    # the world ships bundled and minified (names mangled, comments gone):
    # only what survives a minifier is looked for, in the whole bundle
    main = gl = bundle()
    for needle, why in (("prefers-reduced-motion", "reduced motion gets the index"),
                        ("saveData", "a data saver gets the index")):
        if needle in main:
            ok(why)
        else:
            bad(f"main.js lost a gate: {why}")
    if "COMPLETION_STATUS_KHR" not in gl:
        bad("gl.js waits on shader links: the main thread blocks for the whole compile")
    else:
        ok("shader links are polled, never waited on")


def bundle() -> str:
    """Every module of the world, as one text (vendor excluded)."""
    world = SITE / "world"
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(world.rglob("*.js"))
                     if "vendor" not in p.parts)


# ── 3. type lives in the DOM, not in the scene ─────────────────────────────
def check_typography() -> None:
    """The identity is geometry in a shader; every other word is real type in
    the document. A canvas of text hung in a 3D scene is a photograph of type,
    fixed at one resolution and kerned for a size it is not seen at."""
    print("")
    print("[type] words are in the document, the name is geometry")
    world = SITE / "world"
    # a 2D canvas as a pixel buffer is not type; drawing text into one is
    offenders = [str(p.relative_to(world)) for p in world.rglob("*.js")
                 if re.search(r"fillText|strokeText|measureText|\.font\s*=", p.read_text(encoding="utf-8"))]
    if offenders:
        bad("text is rasterised into the scene in: " + ", ".join(offenders))
    else:
        ok("no text is rasterised into the scene")
    merc = bundle()
    if "glyphM" not in merc or "glyphA" not in merc or "glyphO" not in merc:
        bad("mercury.js: the letterforms are gone")
    else:
        ok("the name is three signed distance functions, not a font")



# ── 4. what a thumb has to hit, and what it gets instead of a hover ─────────
def check_touch() -> None:
    print("")
    print("[touch] the frame a finger gets")
    src = (SITE / "index.html").read_text(encoding="utf-8")
    main = merc = bundle()
    if "@media (hover:none)" not in src.replace("hover: none", "hover:none"):
        bad("index.html keys nothing to the pointer: tap targets follow width only")
    else:
        ok("tap targets are keyed to the pointer, not to the viewport width")
    if "pointer: coarse" not in main:
        bad("the world does not know a touch screen from a small desktop")
    else:
        ok("the world knows what it is running on")
    if "column" not in merc:
        bad("the name does not stand as a column on a tall frame")
    else:
        ok("a phone gets the name as a column, not a shrunken row")


# ── 5. every script actually parses ──────────────────────────────────────────
def check_scripts() -> None:
    print("\n[scripts] node --check on every module and every inline block")
    tmp = ROOT / ".checktmp"
    tmp.mkdir(exist_ok=True)
    try:
        targets = [(p, "mjs") for p in sorted((SITE / "world").rglob("*.js"))]
        for path, ext in targets:
            dest = tmp / (path.stem + "." + ext)
            dest.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
            run(dest, f"{path.relative_to(ROOT)}")

        for page in sorted(SITE.rglob("*.html")):
            html = page.read_text(encoding="utf-8")
            # data blocks (the inlined registry) are JSON, and checked as JSON
            for data in re.findall(r'<script type="application/json"[^>]*>(.*?)</script>', html, re.S):
                try:
                    json.loads(data)
                except json.JSONDecodeError as e:
                    bad(f"{page.name}: a JSON data block does not parse: {e}")
            blocks = re.findall(r'<script(?![^>]*\bsrc=)(?![^>]*application/json)[^>]*>(.*?)</script>', html, re.S)
            for i, code in enumerate(blocks):
                if not code.strip() or "application/ld+json" in html[:0]:
                    continue
                if re.search(r'<script[^>]*type="application/ld\+json"', html) and code.strip().startswith("{"):
                    try:
                        json.loads(code)
                        continue
                    except json.JSONDecodeError as e:
                        bad(f"{page.name}: ld+json block is not valid JSON: {e}")
                        continue
                dest = tmp / f"{page.stem}_{i}.mjs"
                dest.write_text(code, encoding="utf-8")
                run(dest, f"{page.relative_to(ROOT)} inline block {i}")
    finally:
        for f in tmp.glob("*"):
            f.unlink()
        tmp.rmdir()


def run(path: pathlib.Path, label: str) -> None:
    p = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
    if p.returncode:
        bad(f"{label}: {p.stderr.strip().splitlines()[-1] if p.stderr else 'parse error'}")
    else:
        ok(f"{label}: parses")


# ── 6. the maths tests ───────────────────────────────────────────────────────
def check_units() -> None:
    print("\n[units] matrices, springs, solids")
    suite = ROOT / "tests" / "unit"
    if not suite.is_dir():
        bad("tests/unit/ is missing")
        return
    p = subprocess.run(["node", "--test", str(suite)],
                       capture_output=True, text=True, cwd=ROOT)
    out = (p.stdout or "") + (p.stderr or "")
    passed = next((l for l in out.splitlines() if l.startswith("# pass ")), "# pass ?")
    if p.returncode:
        bad("unit tests failed\n" + out.strip()[-2000:])
    else:
        ok("unit suite green (%s assertions)" % passed.replace("# pass ", "").strip())


# ── 7. nothing private goes out ──────────────────────────────────────────────
LEAK = [
    (r"(?i)\b(ghp|gho|ghs|ghu)_[A-Za-z0-9]{20,}", "a GitHub token"),
    (r"(?i)\bsk-[A-Za-z0-9]{20,}", "an API key"),
    (r"(?i)\bAKIA[0-9A-Z]{16}\b", "an AWS access key id"),
    (r"(?i)-----BEGIN [A-Z ]*PRIVATE KEY-----", "a private key"),
    (r"[A-Za-z]:\\\\?Users\\\\?[A-Za-z0-9_.-]+", "a local Windows path"),
    (r"/(?:home|Users)/[a-z0-9_.-]{2,}/", "a local home directory"),
    (r"(?i)\bBearer\s+[A-Za-z0-9._-]{24,}", "a bearer token"),
]


def check_leaks() -> None:
    print("\n[leaks] scanning everything that gets published")
    files = [p for p in SITE.rglob("*") if p.is_file()] + [ROOT / "README.md"]
    scanned = 0
    for p in files:
        if not p.exists() or p.suffix in {".png", ".jpg", ".webp", ".ico"}:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        scanned += 1
        for pattern, what in LEAK:
            m = re.search(pattern, text)
            if m:
                bad(f"{p.relative_to(ROOT)} contains {what}: {m.group(0)[:24]}...")
    ok(f"{scanned} published files scanned, nothing matched")


def main() -> int:
    print("check.py — verifying what this site says about itself")
    # the site is served from Cloudflare Pages (built in the private
    # repository); here only a redirect and the forge's card are published
    if (SITE / "world").exists():
        check_registry()
        check_world()
        check_scripts()
        check_typography()
        check_touch()
    else:
        print("\n[site] not published here: a redirect to the site, and the card")
    check_leaks()
    print()
    if FAIL:
        print(f"{len(FAIL)} FAILURE(S)")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
