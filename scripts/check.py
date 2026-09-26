#!/usr/bin/env python3
"""
check.py — everything this site claims about itself, verified mechanically.

Run before every push and in CI. Standard library only, no network.

It exists because each of the classes of failure below has actually shipped
here at least once:

  * a colour comment that drifted away from the colour it described
  * a universe whose portal wash did not match the page it landed on
  * a stated contrast ratio that was invented rather than measured
  * an inline <script> with a syntax error, which silently killed the whole
    script block and took four working features with it
  * a private repository name hard-coded into a published file

Exit status is 1 on any failure, and every failure prints what it measured
next to what the file claimed.
"""
import json
import pathlib
import re
import subprocess
import sys
import xml.dom.minidom

ROOT = pathlib.Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
FAIL: list[str] = []


def bad(msg: str) -> None:
    FAIL.append(msg)
    print("  FAIL " + msg)


def ok(msg: str) -> None:
    print("  ok   " + msg)


# ── colour ───────────────────────────────────────────────────────────────────
def luminance(hexcol: str) -> float:
    n = int(hexcol.lstrip("#"), 16)

    def lin(v: int) -> float:
        c = v / 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * lin((n >> 16) & 255) + 0.7152 * lin((n >> 8) & 255) + 0.0722 * lin(n & 255)


def contrast(a: str, b: str) -> float:
    la, lb = luminance(a), luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


# ── 1. the universe registry describes the pages that exist ──────────────────
def check_registry() -> dict:
    print("\n[registry] content.json against the files on disk")
    content = json.loads((SITE / "content.json").read_text(encoding="utf-8"))
    seen = set()
    for u in content["universes"]:
        if u["id"] in seen:
            bad(f"duplicate universe id {u['id']}")
        seen.add(u["id"])
        rel = u["path"][2:] if u["path"].startswith("./") else u["path"]
        page = SITE / rel
        if not page.exists():
            bad(f"{u['id']}: {u['path']} does not exist")
            continue
        html = page.read_text(encoding="utf-8")

        # the atrium washes the screen in `ground` and then navigates; the
        # destination opens on that same colour. If the two disagree, the cut
        # across the navigation flashes.
        m = re.search(r'portal\.js"[^>]*data-ground="(#[0-9a-fA-F]{6})"', html)
        if not m:
            bad(f"{u['id']}: page does not load portal.js with a data-ground")
        elif m.group(1).lower() != u["ground"].lower():
            bad(f"{u['id']}: registry ground {u['ground']} != portal ground {m.group(1)}")
        else:
            ok(f"{u['id']:7s} {u['path']:20s} exists, wash colour agrees ({u['ground']})")

        t = re.search(r'name="theme-color" content="(#[0-9a-fA-F]{6})"', html)
        if t and t.group(1).lower() != u["ground"].lower():
            bad(f"{u['id']}: theme-color {t.group(1)} != ground {u['ground']}")

        if contrast(u["ground"], u["ink"]) < 3.0:
            bad(f"{u['id']}: ink {u['ink']} on ground {u['ground']} is "
                f"{contrast(u['ground'], u['ink']):.2f}:1, under 3:1")
    return content


# ── 2. LOUD's colour system ──────────────────────────────────────────────────
def check_loud() -> None:
    print("\n[loud] ground/ink pairs, measured against what the file claims")
    src = (SITE / "u" / "loud.html").read_text(encoding="utf-8")
    # the comment may name the hue in one word or several ("hot pink")
    pairs = re.findall(r"\['(#[0-9a-f]{6})','(#[0-9a-f]{6})'\],\s*//[^\d\n]*?([\d.]+)\s*:\s*1", src)
    if len(pairs) != 6:
        bad(f"expected 6 annotated ground/ink pairs, parsed {len(pairs)}")
        return
    for ground, ink, claimed in pairs:
        r = contrast(ground, ink)
        if r < 4.5:
            bad(f"{ground}/{ink} is {r:.2f}:1, under 4.5:1")
        elif abs(float(claimed) - r) > 0.1:
            bad(f"{ground}/{ink} measures {r:.2f}:1 but the comment says {claimed}:1")
        else:
            ok(f"{ground} / {ink}  {r:5.2f}:1  (comment agrees)")

    # The affordance is a shape, not a colour, precisely because no colour
    # works across this range. Keep that true: nothing may reintroduce a
    # single reserved hue without proving it clears 3:1 on every ground.
    if "--act" in src:
        bad("loud.html reintroduced a reserved affordance colour (--act); "
            "no single hue clears 3:1 against all six grounds")
    else:
        ok("affordance is an invariant shape, not a reserved hue")

    lo = min(luminance(g) for g, _, _ in pairs)
    hi = max(luminance(g) for g, _, _ in pairs)
    ok(f"grounds span L={lo:.3f} to L={hi:.3f}, which is why the shape rule holds")


# ── 3. the atrium's own HUD ──────────────────────────────────────────────────
def check_atrium() -> None:
    print("\n[atrium] HUD text on the void")
    src = (SITE / "index.html").read_text(encoding="utf-8")
    void = re.search(r"--void:(#[0-9a-f]{6})", src).group(1)
    for name in ("ink", "mute", "dim", "cyan"):
        col = re.search(r"--%s:(#[0-9a-f]{6})" % name, src).group(1)
        r = contrast(void, col)
        (ok if r >= 4.5 else bad)(f"--{name:5s} {col} on {void}: {r:.2f}:1")

    # The flat index is the fallback for every machine the glass refuses, so
    # it has to be in the HTML rather than built by script.
    for u in ("field", "paper", "raw", "system", "loud"):
        if f'href="./u/{u}.html"' not in src:
            bad(f"index.html has no server-rendered link to {u}")
    ok("all five universes are linked from the static HTML")
    if "hub/hub.js" not in src:
        bad("index.html never imports the hub")
    if "failIfMajorPerformanceCaveat" not in src:
        bad("index.html no longer refuses a software renderer")
    else:
        ok("the glass still declines a machine that cannot afford it")


# ── 4. every script actually parses ──────────────────────────────────────────
def check_scripts() -> None:
    print("\n[scripts] node --check on every module and every inline block")
    tmp = ROOT / ".checktmp"
    tmp.mkdir(exist_ok=True)
    try:
        targets = [(SITE / "hub" / "hub.js", "mjs"), (SITE / "portal.js", "js")]
        for path, ext in targets:
            dest = tmp / (path.stem + "." + ext)
            dest.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
            run(dest, f"{path.relative_to(ROOT)}")

        for page in sorted(SITE.rglob("*.html")):
            html = page.read_text(encoding="utf-8")
            blocks = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S)
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


# ── 5. the geometry and matrix tests that caught a real bug ──────────────────
def check_hub_math() -> None:
    print("\n[hub] matrix, geometry and winding tests")
    test = ROOT / "scripts" / "hub_math_test.mjs"
    if not test.exists():
        bad("scripts/hub_math_test.mjs is missing")
        return
    p = subprocess.run(["node", str(test)], capture_output=True, text=True, cwd=ROOT)
    tail = (p.stdout or "").strip().splitlines()
    for line in tail:
        if line.startswith("FAIL"):
            bad("hub math: " + line)
    if p.returncode:
        bad("hub math tests failed" + (("\n" + p.stderr.strip()) if p.stderr.strip() else ""))
    else:
        ok(f"{sum(1 for l in tail if l.strip().startswith('ok'))} assertions passed")


# ── 6. markup ────────────────────────────────────────────────────────────────
def check_markup() -> None:
    print("\n[markup] every published SVG is well-formed")
    n = 0
    for svg in sorted((ROOT / "assets").glob("*.svg")):
        try:
            xml.dom.minidom.parse(str(svg))
            n += 1
        except Exception as e:  # noqa: BLE001 - we want the message, whatever it is
            bad(f"{svg.name}: {e}")
    ok(f"{n} svg files parse")


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
    files = [p for p in SITE.rglob("*") if p.is_file()] + \
            [p for p in (ROOT / "assets").glob("*")] + [ROOT / "README.md"]
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
    check_registry()
    check_loud()
    check_atrium()
    check_scripts()
    check_hub_math()
    check_markup()
    check_leaks()
    print()
    if FAIL:
        print(f"{len(FAIL)} FAILURE(S)")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
