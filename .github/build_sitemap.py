"""Rebuild sitemap-dispatches.xml from the verified dispatch list that the
Dispatch Bridge sync stores inside https://brielleyork.com/dispatches.

Exit 0 = success (file rewritten only if its bytes changed; GITHUB_OUTPUT changed=true/false).
Exit 1 = refused; the existing sitemap file is left exactly as it was.
"""
import json, os, re, sys, urllib.request
from datetime import date

SITE = "https://brielleyork.com"
INDEX = SITE + "/dispatches"
OUT = os.environ.get("SITEMAP_FILE", "sitemap-dispatches.xml")
PATH_RE = re.compile(r"^dispatch-[a-z0-9][a-z0-9-]{0,120}$")
MANIFEST_RE = re.compile(r'<script type="application/json" id="dispatch-manifest">\s*(.*?)\s*</script>', re.S)
LOC_RE = re.compile(r"<loc>https://brielleyork\.com/(dispatch-[a-z0-9-]+)</loc>")


class Refuse(Exception):
    pass


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "brielle-dispatch-sitemap/1.0", "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status, r.read().decode("utf-8", "replace")


def read_manifest(page):
    m = MANIFEST_RE.search(page)
    if not m:
        raise Refuse("dispatch list not found on /dispatches (page changed or fetch returned something else)")
    try:
        man = json.loads(m.group(1).replace("<\\/", "</"))
    except ValueError as x:
        raise Refuse(f"dispatch list is not valid JSON: {x}")
    if not isinstance(man, dict) or man.get("v") != 1 or not isinstance(man.get("posts"), list):
        raise Refuse("dispatch list has an unexpected shape")
    active, seen = [], set()
    for p in man["posts"]:
        if not isinstance(p, dict) or p.get("state") not in ("active", "missing"):
            raise Refuse(f"dispatch list entry malformed: {p!r}")
        if p["state"] != "active":
            continue
        path, d, h = p.get("path"), p.get("date"), p.get("hash")
        if not isinstance(path, str) or not PATH_RE.match(path):
            raise Refuse(f"invalid dispatch path {path!r}")
        try:
            date.fromisoformat(d)
        except (TypeError, ValueError):
            raise Refuse(f"invalid date {d!r} for {path}")
        if path in seen:
            raise Refuse(f"duplicate path {path}")
        seen.add(path)
        active.append({"path": path, "date": d, "hash": h})
    if not active:
        raise Refuse("dispatch list has zero active dispatches; refusing to publish an empty sitemap")
    return active


def render(active):
    rows = "".join(f"  <url><loc>{SITE}/{p['path']}</loc><lastmod>{p['date']}</lastmod></url>\n" for p in active)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + rows + "</urlset>\n").encode("utf-8")


def plan(active, old_bytes, page_check):
    old = LOC_RE.findall(old_bytes.decode("utf-8", "replace")) if old_bytes else []
    new_paths = [p["path"] for p in active]
    removed = [x for x in old if x not in new_paths]
    if old and len(removed) > max(1, len(old) // 4):
        raise Refuse(f"{len(removed)} of {len(old)} URLs would disappear at once ({', '.join(removed)}); "
                     "that looks like a broken read, not a genuine removal")
    added = [p for p in active if p["path"] not in old]
    for p in added:
        ok, why = page_check(p)
        if not ok:
            raise Refuse(f"new dispatch {SITE}/{p['path']} is not confirmed live yet ({why}); will retry next run")
    return render(active), [p["path"] for p in added], removed


def live_check(p):
    try:
        status, body = fetch(f"{SITE}/{p['path']}")
    except Exception as x:
        return False, f"fetch failed: {x}"
    if status != 200:
        return False, f"HTTP {status}"
    if p.get("hash") and f'<meta name="dispatch-hash" content="{p["hash"]}">' not in body:
        return False, "page is up but not yet showing the verified version"
    return True, "live"


def main():
    out = os.environ.get("GITHUB_OUTPUT")
    def emit(**kv):
        if out:
            with open(out, "a") as f:
                for k, v in kv.items():
                    f.write(f"{k}={v}\n")
    old = open(OUT, "rb").read() if os.path.exists(OUT) else b""
    try:
        status, page = fetch(INDEX)
        if status != 200:
            raise Refuse(f"/dispatches returned HTTP {status}")
        active = read_manifest(page)
        new, added, removed = plan(active, old, live_check)
    except Refuse as x:
        print(f"SITEMAP UPDATE: FAIL\nReason: {x}\nPrevious sitemap left unchanged.")
        emit(changed="false", result="FAIL")
        return 1
    except Exception as x:
        print(f"SITEMAP UPDATE: FAIL\nReason: could not read {INDEX}: {x}\nPrevious sitemap left unchanged.")
        emit(changed="false", result="FAIL")
        return 1
    if new == old:
        print(f"SITEMAP: NO CHANGE ({len(active)} URLs)")
        emit(changed="false", result="NO CHANGE", count=len(active))
        return 0
    with open(OUT, "wb") as f:
        f.write(new)
    print(f"SITEMAP: CHANGED ({len(active)} URLs)")
    for a in added:
        print(f"  + {SITE}/{a}")
    for r in removed:
        print(f"  - {SITE}/{r}")
    emit(changed="true", result="CHANGED", count=len(active))
    return 0


if __name__ == "__main__":
    sys.exit(main())
