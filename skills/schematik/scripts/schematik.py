#!/usr/bin/env python3
"""schematik.io public catalogue reader: pinouts, CAD models, reference builds.

No API key, no login. Only public pages (/parts, /projects, /sitemap.xml);
/app and /api are Disallow in robots.txt and are never touched here.
"""
import argparse, json, os, re, sys, urllib.parse, urllib.request

BASE = "https://www.schematik.io"
CACHE = os.path.expanduser("~/.cache/schematik")
PUSH = re.compile(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)')


def get(url, binary=False):
    req = urllib.request.Request(url, headers={"User-Agent": "schematik-skill/1 (+local agent)"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read()
    except urllib.error.HTTPError as e:
        sys.exit("%s -> HTTP %s (check the slug with `search`)" % (url, e.code))
    return data if binary else data.decode("utf8", "replace")


def payload(url):
    """Join the RSC flight chunks of a Next.js page into one decoded string."""
    return "".join(json.loads(m.group(1)) for m in PUSH.finditer(get(url)))


def slice_json(text, key):
    """Return the JSON object that follows `key` in text, or None."""
    i = text.find(key)
    if i < 0:
        return None
    i += key.index("{")
    depth, instr, esc = 0, False, False
    for j in range(i, len(text)):
        c = text[j]
        if instr:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                instr = False
        elif c == '"':
            instr = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[i:j + 1])
                except json.JSONDecodeError:
                    return None
    return None


def sitemap():
    os.makedirs(CACHE, exist_ok=True)
    f = os.path.join(CACHE, "sitemap.xml")
    if not os.path.exists(f) or os.path.getsize(f) < 1000:
        open(f, "w").write(get(BASE + "/sitemap.xml"))
    return re.findall(r"<loc>([^<]+)</loc>", open(f).read())


def cmd_search(a):
    words = [w.lower() for w in a.words]
    hits = [u for u in sitemap() if "/%s/" % a.kind in u and all(w in u.lower() for w in words)]
    print("\n".join(hits[:a.limit]) or "no match (slugs only; try fewer/other words)")
    if len(hits) > a.limit:
        print("... %d more" % (len(hits) - a.limit))


def cmd_part(a):
    t = payload("%s/parts/%s" % (BASE, a.slug))
    part = slice_json(t, '"part":{"partId"') or {}
    pinout = slice_json(t, '"pinout":{') or {}
    print("# %s  (%s)" % (part.get("displayName", a.slug), part.get("brand") or "?"))
    print(part.get("description", "").strip(), "\n")
    for k in ("categoryId", "groupId", "partNumber", "price", "currency", "availability", "sourceUrl"):
        if part.get(k):
            print("%-13s %s" % (k, part[k]))
    pins = pinout.get("pins") or []
    if pins:
        print("\n## pins (logical; physical placement NOT verified by schematik)")
        for p in pins:
            v = p.get("voltage") or ""
            print("  %-6s %-9s %-6s %s%s" % (p.get("label", "?"), p.get("type", ""),
                  "" if v == "$undefined" else v, p.get("function", ""),
                  "" if p.get("required", True) else " [optional]"))
    cad = sorted(set(re.findall(r"https://[^\"\\ ]+\.(?:step|stl)", t)))
    if cad:
        print("\n## cad")
        print("\n".join("  " + c for c in cad))


def cmd_cad(a):
    os.makedirs(a.out, exist_ok=True)
    for slug in a.slugs:
        t = payload("%s/parts/%s" % (BASE, slug))
        urls = sorted(set(re.findall(r"https://[^\"\\ ]+\.(?:step|stp|stl)", t)))
        # blob.vercel-storage mirrors are direct downloads; github.com/<o>/<r>/blob/ pages are HTML
        urls = [u for u in urls if "github.com/" not in u or "raw.github" in u or "media.github" in u]
        if a.step_only:
            urls = [u for u in urls if not u.endswith(".stl")] or urls
        if not urls:
            print("%-50s no CAD" % slug)
            continue
        for u in urls:
            dst = os.path.join(a.out, urllib.parse.unquote(u.rsplit("/", 1)[-1]))
            open(dst, "wb").write(get(u, binary=True))
            print("%-50s %s  %d bytes" % (slug, dst, os.path.getsize(dst)))


def cmd_project(a):
    t = payload("%s/projects/%s" % (BASE, a.slug))
    d = slice_json(t, '"diagram":{"nodes"') or {}
    board = re.search(r'"resolvedBoardId":"([^"]+)"', t)
    if board:
        print("board: " + board.group(1))
    print("\n## components")
    for n in d.get("nodes", []):
        c = n.get("data", {}).get("component", {})
        print("  %-18s %-40s %s" % (n.get("id"), c.get("name", "?"),
              "libs=" + ",".join(c.get("libraries") or []) if c.get("libraries") else ""))
        if c.get("partSlug"):
            print("%22s/parts/%s" % ("", c["partSlug"]))
    print("\n## connections")
    for e in d.get("edges", []):
        dd = e.get("data") or {}
        print("  %s.%s -> %s.%s  %s" % (e.get("source"), dd.get("sourcePin") or e.get("sourceHandle"),
              e.get("target"), dd.get("targetPin") or e.get("targetHandle"), dd.get("connectionType") or ""))
    i = t.find("#include")
    if i >= 0:
        # RSC text chunks are announced as `<id>:T<hex-length>,` right before the body
        m = re.search(r"T([0-9a-f]+),$", t[max(0, i - 20):i])
        n = int(m.group(1), 16) if m else None
        if n is None:
            end = re.search(r"[0-9a-f]{1,4}:[\[{T]", t[i:])
            n = end.start() if end else 20000
        print("\n## firmware\n" + t[i:i + n])


def cmd_selfcheck(a):
    """Parser check against two pages whose content I verified by hand on 2026-09-16."""
    t = payload(BASE + "/parts/bosch-bmi270")
    pins = (slice_json(t, '"pinout":{') or {}).get("pins") or []
    assert [p["label"] for p in pins] == ["VCC", "GND", "SDA", "SCL", "INT1", "INT2"], pins
    assert (slice_json(t, '"part":{"partId"') or {}).get("brand") == "Bosch Sensortec"
    assert re.search(r'https://[^"\\ ]+\.step', t)
    t = payload(BASE + "/projects/esp32-mp3-walkman-player-c5d5")
    d = slice_json(t, '"diagram":{"nodes"') or {}
    assert len(d.get("nodes", [])) == 6 and len(d.get("edges", [])) == 6, d.keys()
    i = t.find("#include")
    m = re.search(r"T([0-9a-f]+),$", t[i - 20:i])
    code = t[i:i + int(m.group(1), 16)]
    assert "I2S_BCLK_PIN" in code and code.rstrip().endswith("}"), code[-200:]
    print("selfcheck ok")


p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
s = p.add_subparsers(dest="cmd", required=True)
q = s.add_parser("search", help="match words against public part/project slugs")
q.add_argument("words", nargs="+"); q.add_argument("--kind", default="parts", choices=["parts", "projects"])
q.add_argument("--limit", type=int, default=30); q.set_defaults(fn=cmd_search)
q = s.add_parser("part", help="pinout + specs + CAD links for one part slug")
q.add_argument("slug"); q.set_defaults(fn=cmd_part)
q = s.add_parser("cad", help="download the part's STEP/STL")
q.add_argument("slugs", nargs="+"); q.add_argument("-o", "--out", default=".")
q.add_argument("--step-only", action="store_true"); q.set_defaults(fn=cmd_cad)
q = s.add_parser("project", help="components, wiring edges and firmware of a community build")
q.add_argument("slug"); q.set_defaults(fn=cmd_project)
s.add_parser("selfcheck", help="assert the RSC parser still matches two known pages").set_defaults(fn=cmd_selfcheck)
a = p.parse_args(); a.fn(a)
