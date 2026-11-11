"""Clean IPTV playlist builder.

Takes the free iptv-org lists, keeps only the channels we want (Indian channels
plus English news, movies, entertainment, kids and documentary), tests every
stream and drops the dead ones, then builds a programme guide (EPG) that
matches the channels that are left.

Output (in ./public):
  playlist.m3u   - load this URL in TiviMate / IPTV Smarters
  guide.xml.gz   - programme guide (the playlist already points to it)
  report.txt     - how many channels were kept / dropped

Run:  python build.py            (full run, about 5-10 minutes)
      python build.py --quick    (skip stream testing, for trying changes)
"""
import argparse
import concurrent.futures as cf
import gzip
import io
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "public")

# Where the finished files will be served from (GitHub Pages).
# build.py writes this into the playlist header so the apps find the guide.
SITE_URL = os.environ.get("SITE_URL", "").rstrip("/")

INDIA = "https://iptv-org.github.io/iptv/countries/in.m3u"
ENGLISH = "https://iptv-org.github.io/iptv/languages/eng.m3u"
HINDI = "https://iptv-org.github.io/iptv/languages/hin.m3u"

# English channel categories we keep (iptv-org group-title values).
ENGLISH_KEEP = {
    "News": "English News",
    "Business": "English News",
    "Weather": "English News",
    "Movies": "English Movies",
    "Classic": "English Movies",
    "Entertainment": "English Entertainment",
    "Series": "English Entertainment",
    "Comedy": "English Entertainment",
    "General": "English Entertainment",
    "Kids": "Kids",
    "Animation": "Kids",
    "Family": "Kids",
    "Documentary": "Documentary & Science",
    "Science": "Documentary & Science",
    "Education": "Documentary & Science",
    "Travel": "Documentary & Science",
    "Outdoor": "Documentary & Science",
    "Culture": "Documentary & Science",
}
# Never keep these, even for India.
DROP = {"Shop", "Religious", "Legislative"}

# Free programme guides. Channels are matched to these by name.
GUIDES = [
    "https://www.open-epg.com/files/india1.xml",
    "https://www.open-epg.com/files/india2.xml",
    "https://www.open-epg.com/files/india3.xml",
    "https://i.mjh.nz/PlutoTV/all.xml.gz",
    "https://i.mjh.nz/SamsungTVPlus/all.xml.gz",
    "https://i.mjh.nz/Plex/all.xml.gz",
    "https://i.mjh.nz/Roku/all.xml.gz",
]

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
TIMEOUT = 12


def log(*a):
    print(*a, flush=True)


def fetch(url, headers=None, limit=None, timeout=TIMEOUT):
    """Return (status, final_url, body bytes). status 0 = could not connect."""
    h = {"User-Agent": UA}
    h.update(headers or {})
    req = urllib.request.Request(url, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read(limit) if limit else r.read()
            return r.status, r.geturl(), body
    except urllib.error.HTTPError as e:
        return e.code, url, b""
    except Exception:
        return 0, url, b""


# ---------------------------------------------------------------- playlist

ATTR = re.compile(r'([\w-]+)="([^"]*)"')


def parse_m3u(text):
    chans, cur = [], None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#EXTINF"):
            head, _, name = line.partition(",")
            cur = {"attrs": dict(ATTR.findall(head)), "name": name.strip(),
                   "opts": []}
        elif line.startswith("#EXTVLCOPT") and cur:
            cur["opts"].append(line)
        elif line and not line.startswith("#") and cur:
            cur["url"] = line
            chans.append(cur)
            cur = None
    return chans


def headers_for(ch):
    h = {}
    a = ch["attrs"]
    if a.get("http-user-agent"):
        h["User-Agent"] = a["http-user-agent"]
    if a.get("http-referrer"):
        h["Referer"] = a["http-referrer"]
    for o in ch["opts"]:
        k, _, v = o.split(":", 1)[1].partition("=")
        if k == "http-user-agent":
            h["User-Agent"] = v
        elif k == "http-referrer":
            h["Referer"] = v
    return h


INDIA_GROUPS = [
    ("News", {"News", "Business", "Weather"}),
    ("Movies", {"Movies", "Classic"}),
    ("Music", {"Music"}),
    ("Kids", {"Kids", "Animation", "Family"}),
    ("Sports", {"Sports"}),
    ("Infotainment", {"Documentary", "Education", "Science", "Travel",
                      "Culture", "Cooking", "Lifestyle", "Outdoor", "Auto"}),
]


def india_group(cats):
    for name, members in INDIA_GROUPS:
        if members & set(cats):
            return name
    return "Entertainment"


def select_channels():
    log("Downloading channel lists...")
    india = parse_m3u(fetch(INDIA, timeout=60)[2].decode("utf-8", "replace"))
    eng = parse_m3u(fetch(ENGLISH, timeout=60)[2].decode("utf-8", "replace"))
    hindi_urls = {c["url"] for c in
                  parse_m3u(fetch(HINDI, timeout=60)[2].decode("utf-8", "replace"))}

    picked, seen = [], set()
    for c in india:
        cats = c["attrs"].get("group-title", "").split(";")
        if DROP & set(cats) or c["url"] in seen:
            continue
        lang = "Hindi" if c["url"] in hindi_urls else "Regional"
        c["group"] = f"{lang} {india_group(cats)}"
        picked.append(c)
        seen.add(c["url"])
    for c in eng:
        cats = c["attrs"].get("group-title", "").split(";")
        if DROP & set(cats) or c["url"] in seen:
            continue
        group = next((ENGLISH_KEEP[x] for x in cats if x in ENGLISH_KEEP), None)
        if not group:
            continue
        c["group"] = group
        picked.append(c)
        seen.add(c["url"])
    log(f"  {len(india)} Indian + {len(eng)} English in the source lists, "
        f"{len(picked)} match what we want")
    return picked


# ---------------------------------------------------------------- testing

def check(ch):
    """Return 'ok', 'blocked' (403 - maybe only blocked outside India) or 'dead'."""
    h = headers_for(ch)
    url = ch["url"]
    status, final, body = fetch(url, h, limit=200_000)
    if status in (401, 403, 451):
        return "blocked"
    if status != 200:
        return "dead"
    text = body.decode("utf-8", "replace")
    if "#EXTM3U" not in text[:2000]:
        # Not an HLS playlist (e.g. a direct .ts / .mp4 / mpd). If it sent
        # bytes back, count it as alive.
        return "ok" if body else "dead"
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    # Master playlist: follow the first variant.
    if any(l.startswith("#EXT-X-STREAM-INF") for l in lines):
        variant = next((l for l in lines if not l.startswith("#")), None)
        if not variant:
            return "dead"
        status, final, body = fetch(urllib.parse.urljoin(final, variant), h,
                                    limit=200_000)
        if status in (401, 403, 451):
            return "blocked"
        if status != 200:
            return "dead"
        lines = [l.strip() for l in body.decode("utf-8", "replace").splitlines()
                 if l.strip()]
    segs = [l for l in lines if not l.startswith("#")]
    if not segs:
        return "dead"
    # Make sure a video segment actually downloads.
    status, _, seg = fetch(urllib.parse.urljoin(final, segs[-1]), h, limit=4096)
    if status in (401, 403, 451):
        return "blocked"
    return "ok" if status in (200, 206) and seg else "dead"


def test_all(chans, keep_blocked):
    log(f"Testing {len(chans)} streams (this takes a few minutes)...")
    results = {}
    with cf.ThreadPoolExecutor(max_workers=48) as pool:
        futs = {pool.submit(check, c): i for i, c in enumerate(chans)}
        for n, f in enumerate(cf.as_completed(futs), 1):
            try:
                results[futs[f]] = f.result()
            except Exception:
                results[futs[f]] = "dead"
            if n % 200 == 0:
                log(f"  {n}/{len(chans)}")
    ok = [c for i, c in enumerate(chans)
          if results[i] == "ok" or (keep_blocked and results[i] == "blocked")]
    counts = {k: list(results.values()).count(k) for k in ("ok", "blocked", "dead")}
    return ok, counts


def dedupe(chans):
    """Same channel listed several times (SD/HD/backup) -> keep one, prefer HD."""
    best = {}
    for c in chans:
        tid = c["attrs"].get("tvg-id", "")
        key = tid.split("@")[0].lower() if tid else c["name"].lower()
        score = 1 if ("@HD" in tid or " HD" in c["name"] or "1080" in c["name"]) else 0
        if key not in best or score > best[key][0]:
            best[key] = (score, c)
    return [v[1] for v in best.values()]


# ---------------------------------------------------------------- guide

def norm(name):
    n = name.lower()
    n = re.sub(r"\.(in|us|uk|ca|au|[a-z]{2})$", "", n)       # country suffix
    n = re.sub(r"\((.*?)\)|\[(.*?)\]", " ", n)              # (720p) [Geo]
    n = n.replace("&", "and")
    n = re.sub(r"\b(hd|sd|fhd|uhd|4k|tv|channel|india|in|us|uk|"
               r"1080p|720p|480p|576p|360p)\b", " ", n)
    return re.sub(r"[^a-z0-9]", "", n)


def build_guide(chans):
    log("Downloading programme guides...")
    wanted = {}
    for c in chans:
        tid = c["attrs"].get("tvg-id", "")
        keys = {norm(c["name"])}
        if tid:
            keys.add(norm(tid.split("@")[0].split(".")[0]))
        for k in keys:
            if len(k) >= 3:          # too-short names match the wrong channel
                wanted.setdefault(k, []).append(c)

    out_ch, out_prog, matched = [], [], {}
    for g in GUIDES:
        status, _, data = fetch(g, timeout=120)
        if status != 200 or not data:
            log(f"  skipped (could not download): {g}")
            continue
        if data[:2] == b"\x1f\x8b":
            data = gzip.decompress(data)
        try:
            root = ET.fromstring(data)
        except ET.ParseError:
            log(f"  skipped (bad file): {g}")
            continue
        src = re.sub(r"\W", "", urllib.parse.urlparse(g).path)[-20:]
        ids = {}
        for ch in root.findall("channel"):
            names = [d.text or "" for d in ch.findall("display-name")]
            for nm in names:
                for c in wanted.get(norm(nm), []):
                    if id(c) in matched:
                        continue
                    new_id = f"{src}.{ch.get('id')}"
                    matched[id(c)] = new_id
                    c["guide_id"] = new_id
                    ids[ch.get("id")] = new_id
        n_before = len(out_ch)
        for ch in root.findall("channel"):
            if ch.get("id") in ids:
                ch.set("id", ids[ch.get("id")])
                out_ch.append(ch)
        for p in root.findall("programme"):
            if p.get("channel") in ids:
                p.set("channel", ids[p.get("channel")])
                out_prog.append(p)
        log(f"  {len(out_ch) - n_before:4d} channels matched from {g}")

    tv = ET.Element("tv", {"generator-info-name": "iptv-clean"})
    seen = set()
    for ch in out_ch:
        if ch.get("id") not in seen:
            tv.append(ch)
            seen.add(ch.get("id"))
    tv.extend(out_prog)
    buf = io.BytesIO()
    ET.ElementTree(tv).write(buf, encoding="utf-8", xml_declaration=True)
    with gzip.open(os.path.join(OUT, "guide.xml.gz"), "wb") as f:
        f.write(buf.getvalue())
    return len(matched)


# ---------------------------------------------------------------- output

def write_playlist(chans):
    guide = f"{SITE_URL}/guide.xml.gz" if SITE_URL else "guide.xml.gz"
    order = ["Hindi", "Regional", "English News", "English Movies",
             "English Entertainment", "Kids", "Documentary"]

    def sort_key(c):
        rank = next((i for i, p in enumerate(order) if c["group"].startswith(p)),
                    len(order))
        return (rank, c["group"], c["name"].lower())

    lines = [f'#EXTM3U x-tvg-url="{guide}" url-tvg="{guide}"']
    for c in sorted(chans, key=sort_key):
        a = dict(c["attrs"])
        a["tvg-id"] = c.get("guide_id", a.get("tvg-id", ""))
        a["group-title"] = c["group"]
        attrs = " ".join(f'{k}="{v}"' for k, v in a.items() if v)
        lines.append(f"#EXTINF:-1 {attrs},{c['name']}")
        lines.extend(c["opts"])
        lines.append(c["url"])
    with open(os.path.join(OUT, "playlist.m3u"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="skip stream testing")
    ap.add_argument("--drop-blocked", action="store_true",
                    help="also drop streams that answer 403 (only safe when "
                         "running from India)")
    args = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    t0 = time.time()

    chans = select_channels()
    if args.quick:
        alive, counts = chans, {"ok": len(chans), "blocked": 0, "dead": 0}
    else:
        alive, counts = test_all(chans, keep_blocked=not args.drop_blocked)
    final = dedupe(alive)
    n_guide = build_guide(final)
    write_playlist(final)

    groups = {}
    for c in final:
        groups[c["group"]] = groups.get(c["group"], 0) + 1
    report = [
        f"Built {time.strftime('%d %b %Y %H:%M UTC', time.gmtime())}",
        f"Candidates: {len(chans)}  working: {counts['ok']}  "
        f"blocked(403): {counts['blocked']}  dead: {counts['dead']}",
        f"Channels in playlist (after removing duplicates): {len(final)}",
        f"Channels with programme guide: {n_guide}",
        "",
    ] + [f"  {n:4d}  {g}" for g, n in sorted(groups.items())]
    with open(os.path.join(OUT, "report.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(report) + "\n")
    log("\n".join(report))
    log(f"Done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    sys.exit(main())
