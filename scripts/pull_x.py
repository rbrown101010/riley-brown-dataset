#!/usr/bin/env python3
"""Pull @rileybrown's public X posts into work/x/posts.json.

Route: Scrape Creators through the Composio CLI (public data only):
  1. SCRAPECREATORS_LIST_TWITTER_USER_TWEETS  -> his ~100 most popular posts
  2. SCRAPECREATORS_SEARCH_TWITTER_POSTS      -> Google-indexed posts, many topic queries,
                                                 up to 11 pages each, filtered to author == rileybrown
(The official X API route, TWITTER_GET_USER_POSTS, returned 402 "credits depleted" on 2026-11-20.)
Retweets are dropped. His own replies are kept (leading @mentions are stripped later in clean).
Re-runnable: merges with any existing posts.json.
"""
import json, os, subprocess, sys
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "work", "x")
os.makedirs(OUT, exist_ok=True)
HANDLE = "rileybrown"

TOPICS = ["", "claude", "claude code", "cursor", "vibe coding", "app", "AI", "agents", "openai", "gpt",
          "chatgpt", "replit", "n8n", "mcp", "codex", "gemini", "video", "youtube", "build", "startup",
          "tutorial", "prompt", "code", "iphone", "mobile app", "vibecode", "deepseek", "grok", "anthropic",
          "model", "tiktok", "content", "creator", "founder", "agent native", "fable", "opus", "sonnet",
          "midjourney", "automation", "website", "learn", "future", "people", "today", "best", "new",
          "thread", "free", "money", "design", "blender", "3d", "voice", "elevenlabs", "workflow", "google",
          "apple", "skills", "lovable", "bolt", "v0", "perplexity", "o1", "o3", "llama", "notion"]


def sc(slug, args):
    out = subprocess.run(["composio", "execute", slug, "-d", json.dumps(args)],
                         capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout
    d = json.loads(out)
    if d.get("storedInFile"):
        d = json.load(open(d["outputFilePath"]))
    return d.get("data") or {}


def author(t):
    u = (((t.get("core") or {}).get("user_results") or {}).get("result") or {})
    return ((u.get("core") or {}).get("screen_name") or (u.get("legacy") or {}).get("screen_name") or "").lower()


def norm(t):
    if t.get("__typename") == "TweetWithVisibilityResults":
        t = t.get("tweet", t)
    lg = t.get("legacy") or {}
    if author(t) != HANDLE or lg.get("retweeted_status_result") or (lg.get("full_text") or "").startswith("RT @"):
        return None
    note = (((t.get("note_tweet") or {}).get("note_tweet_results") or {}).get("result") or {}).get("text")
    tid = t.get("rest_id") or lg.get("id_str")
    if not tid:
        return None
    urls = [u.get("expanded_url") for u in (lg.get("entities") or {}).get("urls", []) if u.get("expanded_url")]
    return {"id": tid, "text": note or lg.get("full_text") or "", "created_at": lg.get("created_at"),
            "reply_to": lg.get("in_reply_to_screen_name"), "is_quote": bool(lg.get("is_quote_status")),
            "likes": lg.get("favorite_count"), "expanded_urls": urls,
            "url": f"https://x.com/{HANDLE}/status/{tid}"}


def search(topic):
    q = f"from:{HANDLE} {topic}".strip()
    found, cursor = [], None
    for _ in range(11):
        args = {"query": q}
        if cursor:
            args["cursor"] = cursor
        try:
            d = sc("SCRAPECREATORS_SEARCH_TWITTER_POSTS", args)
        except Exception as ex:
            print("search error", q, ex, file=sys.stderr)
            break
        found += d.get("posts") or []
        cursor = d.get("cursor")
        if not cursor:
            break
    return topic, found


def main():
    path = os.path.join(OUT, "posts.json")
    posts = {p["id"]: p for p in json.load(open(path))} if os.path.exists(path) else {}

    def add(items):
        n = 0
        for t in items:
            p = norm(t)
            if p and p["id"] not in posts:
                posts[p["id"]] = p
                n += 1
        return n

    print("popular:", add(sc("SCRAPECREATORS_LIST_TWITTER_USER_TWEETS", {"handle": HANDLE, "trim": False}).get("items") or []))
    with ThreadPoolExecutor(6) as ex:
        for topic, items in ex.map(search, TOPICS):
            print(f"search {topic!r}: +{add(items)} (total {len(posts)})", flush=True)
    json.dump(list(posts.values()), open(path, "w"), indent=1)
    print("total", len(posts))


if __name__ == "__main__":
    main()
