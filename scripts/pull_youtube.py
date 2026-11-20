#!/usr/bin/env python3
"""Pull Riley Brown's public YouTube uploads + transcripts into work/yt/.

Needs: yt-dlp, and the Composio CLI logged in (Scrape Creators + YouTube toolkits).
Writes work/yt/index.json (metadata) and work/yt/<video_id>.json (raw transcript).
Re-runnable: skips videos already pulled.
"""
import json, os, subprocess, sys, time, urllib.parse
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "work", "yt")
os.makedirs(OUT, exist_ok=True)

CHANNELS = [
    ("rileybrownai", "videos"),
    ("rileybrownai", "shorts"),
    ("rileybrown-2", "videos"),
]
# Optional folder of transcripts pulled earlier (plain text, <video_id>.txt)
LOCAL_TXT = os.environ.get("TRANSCRIPT_CACHE_DIR", "")


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout


def composio_json(args):
    out = run(["composio"] + args)
    d = json.loads(out)
    if isinstance(d, dict) and d.get("storedInFile"):
        d = json.load(open(d["outputFilePath"]))
    return d


def list_uploads():
    items = []
    for handle, tab in CHANNELS:
        d = json.loads(run(["yt-dlp", "--flat-playlist", "-J", f"https://www.youtube.com/@{handle}/{tab}"]))
        for e in d.get("entries", []):
            items.append({"id": e["id"], "title": e.get("title"), "channel": "@" + handle,
                          "kind": "short" if tab == "shorts" else "video",
                          "duration": e.get("duration"),
                          "url": f"https://www.youtube.com/watch?v={e['id']}"})
    return items


def add_dates(items):
    """publishedAt + description via YouTube Data API (Composio proxy, public data)."""
    ids = [i["id"] for i in items]
    meta = {}
    for k in range(0, len(ids), 50):
        q = urllib.parse.urlencode({"part": "snippet,contentDetails", "id": ",".join(ids[k:k + 50])})
        try:
            d = composio_json(["proxy", "https://www.googleapis.com/youtube/v3/videos?" + q, "--toolkit", "youtube"])
            for v in d.get("items", []):
                meta[v["id"]] = v["snippet"]
        except Exception as ex:
            print("date lookup failed:", ex, file=sys.stderr)
    for i in items:
        s = meta.get(i["id"], {})
        i["date"] = (s.get("publishedAt") or "")[:10] or None
        i["description"] = s.get("description", "")
    return items


def fetch_transcript(item):
    path = os.path.join(OUT, item["id"] + ".json")
    if os.path.exists(path):
        return item["id"], "cached"
    local = os.path.join(LOCAL_TXT, item["id"] + ".txt")
    if LOCAL_TXT and os.path.exists(local):
        json.dump({"source": "local", "text": open(local).read()}, open(path, "w"))
        return item["id"], "local"
    for attempt in range(3):
        try:
            d = composio_json(["execute", "SCRAPECREATORS_GET_YOUTUBE_VIDEO_TRANSCRIPT",
                               "-d", json.dumps({"url": item["url"]})])
            data = d.get("data") or {}
            text = data.get("transcript_only_text") or ""
            if not text.strip():
                return item["id"], "empty"
            json.dump({"source": "scrapecreators", "language": data.get("language"), "text": text}, open(path, "w"))
            return item["id"], "ok"
        except Exception as ex:
            time.sleep(3 * (attempt + 1))
            err = str(ex)
    return item["id"], "fail " + err[:100]


if __name__ == "__main__":
    items = add_dates(list_uploads())
    json.dump(items, open(os.path.join(OUT, "index.json"), "w"), indent=1)
    print(len(items), "uploads listed")
    with ThreadPoolExecutor(6) as ex:
        for vid, status in ex.map(fetch_transcript, items):
            print(vid, status, flush=True)
