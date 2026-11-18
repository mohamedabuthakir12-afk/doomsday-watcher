"""Watch BookMyShow + District (Chennai) for new Avengers: Doomsday theatres/shows.

Prints one line per NEW theatre/date found (after the first, baseline run).
Usage: python doomsday_watch.py [interval_seconds]
"""
import json, re, sys, time, subprocess, os

INTERVAL = int(sys.argv[1]) if len(sys.argv) > 1 else 60
STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "doomsday_state.json")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
DATES = [f"202612{d}" for d in range(17, 24)]
LANGS = ["english", "tamil", "hindi"]
BMS = "https://in.bookmyshow.com/movies/chennai/avengers-doomsday/buytickets/ET00526306/{d}?etCodes=*&language={l}"
DIST = "https://www.district.in/movies/avengers-doomsday-movie-tickets-in-chennai-MV176735?fromdate={d}"


def get(url):
    r = subprocess.run(["curl", "-s", "--fail", "--max-time", "25", "-A", UA, url],
                       capture_output=True, timeout=40)
    if r.returncode != 0:
        raise RuntimeError(f"curl exit {r.returncode}")
    return r.stdout.decode("utf-8", "replace")


def bms(date, lang):
    h = get(BMS.format(d=date, l=lang))
    m = re.search(r'"currentDateCode":"(\d+)"', h)
    if not m or m.group(1) != date:
        return set()
    out, venue = set(), None
    for tok in re.finditer(r'"venueName":"([^"]*)"|"showTime":"([^"]*)"', h):
        if tok.group(1):
            venue = tok.group(1)
        elif venue:
            out.add(f"BMS|{date}|{lang}|{venue}|{tok.group(2)}")
    return out


def walk(o, found):
    if isinstance(o, dict):
        if isinstance(o.get("sessions"), list):
            name = (o.get("name") or (o.get("cinemaInfo") or {}).get("name")
                    or o.get("entityName") or str(o.get("id")))
            for s in o["sessions"]:
                if isinstance(s, dict) and s.get("showDate"):
                    found.add(f"DIST|{s['showDate'].replace('-', '')}|-|{name}|{s.get('showTimeText', s.get('showTime'))}")
        for v in o.values():
            walk(v, found)
    elif isinstance(o, list):
        for v in o:
            walk(v, found)


def district_page(url, lang):
    h = get(url)
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', h, re.S)
    found = set()
    if m:
        walk(json.loads(m.group(1)), found)
    found = {f.replace("|-|", f"|{lang}|") for f in found}
    return h, found


def district(date):
    url = DIST.format(d=f"{date[:4]}-{date[4:6]}-{date[6:]}")
    h, found = district_page(url, "english")  # default page = English format
    for lang, fmt in set(re.findall(r'"language":"(\w+)","format":"(\w+)"', h)):
        if lang.lower() != "english":
            found |= district_page(f"{url}&frmtid={fmt.lower()}", lang.lower())[1]
    return {f for f in found if f.split("|")[1] == date}


def snapshot():
    shows, errors = set(), 0
    for d in DATES:
        for l in LANGS:
            try:
                shows |= bms(d, l)
            except Exception:
                errors += 1
        try:
            shows |= district(d)
        except Exception:
            errors += 1
    return shows, errors


def keep_awake():
    """Ask Windows not to sleep while this process runs (screen may still turn off/lock)."""
    try:
        import ctypes
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
    except Exception:
        pass


def ntfy_topic():
    t = os.environ.get("NTFY_TOPIC", "").strip()
    f = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ntfy_topic.txt")
    if not t and os.path.exists(f):
        t = open(f).read().strip()
    return t


def notify(title, lines, tags="movie_camera", priority="high"):
    """Push to the ntfy app on the phone (https://ntfy.sh/<topic>)."""
    topic = ntfy_topic()
    if not topic:
        return
    try:
        subprocess.run(["curl", "-s", "--max-time", "20", "-o", os.devnull,
                        "-H", f"Title: {title}", "-H", f"Priority: {priority}", "-H", f"Tags: {tags}",
                        "-H", "Click: https://in.bookmyshow.com/movies/chennai/avengers-doomsday/ET00439706",
                        "--data-binary", "\n".join(lines)[:3800], f"https://ntfy.sh/{topic}"],
                       timeout=30)
    except Exception:
        pass


def main():
    keep_awake()
    max_runtime = int(os.environ.get("MAX_RUNTIME", "0"))  # seconds; 0 = forever
    started = time.time()
    seen = set(json.load(open(STATE))) if os.path.exists(STATE) else None
    fails = 0
    while True:
        shows, errors = snapshot()
        if errors and not shows:
            fails += 1
            if fails in (5, 30):
                print(f"WARN: {fails} checks in a row failed (site blocking or down)", flush=True)
                notify("Doomsday watcher problem", [f"{fails} checks in a row failed - sites may be blocking the watcher."],
                       tags="warning", priority="default")
        else:
            fails = 0
            if seen is None:
                print(f"BASELINE: {len(shows)} shows recorded", flush=True)
                seen = set()
            else:
                new = shows - seen
                groups = {}
                for s in new:
                    site, d, lang, venue, t = s.split("|")
                    groups.setdefault((site, d, lang, venue), []).append(t)
                lines = []
                for (site, d, lang, venue), ts in sorted(groups.items()):
                    site = "BookMyShow" if site == "BMS" else "District"
                    lg = "" if lang == "-" else f" {lang.title()}"
                    lines.append(f"{site}: {venue} | {d[6:]} Dec{lg} | {', '.join(sorted(ts))}")
                for line in lines:
                    print(f"NEW {line}", flush=True)
                if lines:
                    notify(f"Doomsday: {len(lines)} new show listing(s) in Chennai", lines)
            seen |= shows
            json.dump(sorted(seen), open(STATE, "w"))
        if max_runtime and time.time() - started > max_runtime:
            break
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
