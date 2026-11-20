#!/usr/bin/env python3
"""Clean the pulled public content and build the dataset.

Inputs  (from the pull_* scripts): work/yt/, work/x/posts.json, work/newsletter/posts.json
Outputs: raw/<source>/<id>.txt + <id>.json, data/corpus.jsonl, data/sft.jsonl, data/test.jsonl, data/stats.json

Fails (exit 1) if any email address, phone number or API key/token pattern survives cleaning.
"""
import hashlib, json, os, re, shutil, sys
from email.utils import parsedate_to_datetime
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
W = os.path.join(ROOT, "work")
RAW = os.path.join(ROOT, "raw")
DATA = os.path.join(ROOT, "data")

SYSTEM = ("You are Riley Brown (@rileybrownai on YouTube, @rileybrown on X), a creator who teaches people how to "
          "build with AI: Claude, Claude Code, Codex, Cursor, agents and vibe coding. Write in Riley's own voice: "
          "casual, high-energy, practical, first person, no corporate filler.")

# Videos that are mainly a conversation with a guest. Kept in raw/ and corpus.jsonl (tagged), left out of SFT
# because the transcripts carry no speaker labels.
MULTI_SPEAKER = {"NR_GMq2lDCE", "HQXi4snP36I", "Hxv3MmXLaW0", "ue3DYCOe-xw", "P1KpxzLVg7c", "Fv0XfyLT3xU"}

# ----------------------------------------------------------------------------- privacy scrub
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
SPOKEN_EMAIL = re.compile(r"\b[\w.]+ at [\w-]+ dot (?:com|inc|co|io|ai|net|org)\b", re.I)
PHONE = re.compile(r"(?<![\w$.,])(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}(?![\w])")
KEYS = re.compile(r"\b(?:sk-(?:ant-|proj-)?[A-Za-z0-9_-]{16,}|gh[opsur]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
                  r"|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{35}|xox[abprs]-[A-Za-z0-9-]{10,}|eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}"
                  r"|(?:pk|sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}|hf_[A-Za-z0-9]{30,}|r8_[A-Za-z0-9]{30,})")
# Extra private strings (e.g. how a speech-to-text engine misspells a business email) live in an
# untracked file so the list itself is never published. One regex per line.
_PT = os.path.join(ROOT, "work", "private_tokens.txt")
_pt = [l.strip() for l in open(_PT)] if os.path.exists(_PT) else []
PRIVATE_TOKENS = re.compile(r"\b(?:" + "|".join(p for p in _pt if p) + r")\b(?:\.com)?" if _pt else r"(?!x)x", re.I)


def scrub(t):
    t = EMAIL.sub("[email]", t)
    t = SPOKEN_EMAIL.sub("[email]", t)
    t = PRIVATE_TOKENS.sub("[email]", t)
    t = re.sub(r"[\w.+-]+@\s*\[email\]", "[email]", t)
    t = PHONE.sub("[phone]", t)
    t = KEYS.sub("[redacted]", t)
    return t


def leaks(t):
    return [m.group(0) for rx in (EMAIL, PHONE, KEYS) for m in rx.finditer(t)]


# ----------------------------------------------------------------------------- text cleaning
URL = re.compile(r"https?://\S+|\bwww\.\S+")
TRACKING = re.compile(r"https?://(?:t\.co|bit\.ly|tinyurl\.com|lnkd\.in|geni\.us|amzn\.to|buff\.ly|ow\.ly|dub\.sh)/\S+")
FILLER = re.compile(r"(?<![\w'])(?:um+|uh+|uhm|erm|hmm+)(?![\w'])[,.]?\s*", re.I)
TAGS = re.compile(r"\[(?:music|applause|laughter|snorts|clears throat|inaudible|__|\xa0__\xa0)[^\]]*\]|>>", re.I)
TIMESTAMP = re.compile(r"^\s*\(?\d{1,2}:\d{2}(?::\d{2})?\)?\s*[-–:]?\s*", re.M)

SPONSOR_START = re.compile(
    r"(?<!not )(?:sponsor of (?:this|today's|the) (?:part of the )?video|(?:this|today's) (?:part of the )?video is sponsored by"
    r"|(?:this|today's) video is brought to you by|brought to you by|they're sponsoring this|quick (?:word|break) from (?:our|today's) sponsor"
    r"|quick break because|an update by the sponsor)", re.I)
SPONSOR_LINE = re.compile(r"(?:thanks? (?:so much )?to \w[\w ]{0,30} for sponsoring|thank you so much for sponsoring|for sponsoring (?:this|today's)"
                          r"|promo code|use (?:my )?code \w+|link (?:is )?(?:in|down) (?:the |in the )?description|first link in the description)", re.I)
SPONSOR_END = re.compile(r"(?:back to (?:the|our|my) (?:video|tutorial|build|app)|let's get back|anyway[s]?,|now back to|so now let's)", re.I)


def clean_url(u):
    u = re.sub(r"[?&](?:utm_[a-z]+|ref|ref_src|si|s|t|fbclid|gclid|igshid)=[^&\s]*", "", u)
    return u.rstrip("?&")


def strip_urls(t):
    t = TRACKING.sub("", t)
    return URL.sub(lambda m: clean_url(m.group(0)), t)


def sentences(t):
    out = []
    for s in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])", t):
        w = s.split()
        if len(w) > 80:   # old auto-captions have no punctuation: fall back to ~40-word pieces
            out += [" ".join(w[i:i + 40]) for i in range(0, len(w), 40)]
        elif w:
            out.append(s.strip())
    return out


def drop_sponsor_reads(sents):
    out, skipping, skipped = [], False, 0
    for s in sents:
        if SPONSOR_START.search(s) and "not sponsored" not in s.lower():
            skipping, skipped = True, 0
            continue
        if skipping:
            skipped += len(s.split())
            if SPONSOR_END.search(s) or skipped > 220:
                skipping = False
            continue
        if SPONSOR_LINE.search(s):
            continue
        out.append(s)
    return out


def clean_transcript(t):
    t = TAGS.sub(" ", t)
    t = re.sub(r"\s+", " ", t)                     # fix the double spaces + mid-sentence line breaks
    t = FILLER.sub("", t)
    t = re.sub(r"\b(\w+)( \1\b)+", r"\1", t)        # stutters: "the the" -> "the"
    t = strip_urls(t)
    sents = drop_sponsor_reads(sentences(t))
    t = " ".join(s[0].upper() + s[1:] if s[0].islower() else s for s in sents)
    t = re.sub(r"\s+([,.!?])", r"\1", t)
    t = re.sub(r"\s{2,}", " ", t).strip()
    # paragraphs of ~6 sentences so raw/ files are readable
    ss = sentences(t)
    return "\n\n".join(" ".join(ss[i:i + 6]) for i in range(0, len(ss), 6))


def clean_tweet(t):
    t = strip_urls(t)
    t = re.sub(r"^(?:@\w+\s*)+", "", t)            # leading reply mentions
    t = t.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


NEWSLETTER_BOILER = re.compile(r"(?im)^.*(?:thanks for reading|subscribe for free|share this post|leave a comment|"
                               r"check out .{0,40}channel:|upgrade to paid|^\s*share\s*$).*$")


def clean_newsletter(t):
    t = TIMESTAMP.sub("", t)
    t = NEWSLETTER_BOILER.sub("", t)
    t = strip_urls(t)
    lines = [l.rstrip() for l in t.split("\n")]
    t = "\n".join(lines)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


# ----------------------------------------------------------------------------- helpers
def words(t):
    return len(t.split())


def norm_key(t):
    return re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()


def chunk_sents(t, target=320, min_words=120):
    ss, chunks, cur = sentences(t.replace("\n\n", " ")), [], []
    for s in ss:
        cur.append(s)
        if words(" ".join(cur)) >= target:
            chunks.append(" ".join(cur))
            cur = []
    if cur:
        if chunks and words(" ".join(cur)) < min_words:
            chunks[-1] += " " + " ".join(cur)
        else:
            chunks.append(" ".join(cur))
    return chunks


TOPIC_WORDS = ["Claude Code", "Claude", "Codex", "Cursor", "ChatGPT", "GPT", "OpenAI", "Anthropic", "Gemini", "Grok",
               "GrokBot", "DeepSeek", "n8n", "MCP", "Replit", "Lovable", "Bolt", "v0", "Windsurf", "vibe coding", "vibecode",
               "agents", "AI agents", "Opus", "Sonnet", "Fable", "Midjourney", "Sora", "Veo", "Perplexity", "Notion",
               "Blender", "iOS app", "mobile app", "app", "website", "startup", "YouTube", "content", "Expo", "Convex",
               "Supabase", "Vercel", "skills", "subagents", "automation", "AI video", "AI music", "o1", "o3"]


def topics(t):
    found = []
    for w in TOPIC_WORDS:
        if re.search(r"(?<!\w)" + re.escape(w) + r"(?!\w)", t, re.I) and not any(w.lower() in f.lower() for f in found):
            found.append(w)
        if len(found) == 3:
            break
    return found


def h(s):
    return int(hashlib.sha1(s.encode()).hexdigest(), 16)


def write_raw(source, item_id, text, meta):
    d = os.path.join(RAW, source)
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, f"{item_id}.txt"), "w").write(text + "\n")
    json.dump(meta, open(os.path.join(d, f"{item_id}.json"), "w"), indent=1, ensure_ascii=False)


# ----------------------------------------------------------------------------- load + clean
def load_youtube():
    items = []
    index = json.load(open(os.path.join(W, "yt", "index.json")))
    for v in index:
        p = os.path.join(W, "yt", v["id"] + ".json")
        if not os.path.exists(p):
            continue
        title = v.get("title") or ""
        if re.search(r"#\w*partner\b|#ad\b|\(sponsored\)", title, re.I):   # fully sponsored shorts
            continue
        text = clean_transcript(json.load(open(p))["text"])
        if words(text) < 60:
            continue
        items.append({"source": "youtube_short" if v["kind"] == "short" else "youtube", "id": v["id"], "text": text,
                      "url": v["url"], "date": v.get("date"), "title": title, "channel": v["channel"],
                      "multi_speaker": v["id"] in MULTI_SPEAKER})
    return items


def load_x():
    items = []
    for p in json.load(open(os.path.join(W, "x", "posts.json"))):
        text = clean_tweet(p["text"])
        if words(text) < 4:
            continue
        try:
            date = datetime.strptime(p["created_at"], "%a %b %d %H:%M:%S %z %Y").date().isoformat()
        except Exception:
            date = None
        items.append({"source": "x", "id": p["id"], "text": text, "url": p["url"], "date": date,
                      "title": None, "is_reply": bool(p.get("reply_to")) and p.get("reply_to", "").lower() != "rileybrown",
                      "reply_to": p.get("reply_to") if p.get("reply_to", "") and p["reply_to"].lower() != "rileybrown" else None})
    return items


def load_newsletter():
    items = []
    for p in json.load(open(os.path.join(W, "newsletter", "posts.json"))):
        text = clean_newsletter(p["text"])
        if words(text) < 60:
            continue
        slug = p["url"].rstrip("/").split("/")[-1]
        items.append({"source": "newsletter", "id": slug, "text": text, "url": p["url"],
                      "date": parsedate_to_datetime(p["date"]).date().isoformat(), "title": p["title"]})
    return items


def dedupe(items):
    seen, out = set(), []
    for it in items:
        k = norm_key(it["text"])[:400]
        if k in seen:
            continue
        seen.add(k)
        out.append(it)
    return out


# ----------------------------------------------------------------------------- SFT pairs
def sft_pairs(it):
    title, src, t = it.get("title"), it["source"], it["text"]
    pairs = []
    if src == "x":
        if words(t) < 6:      # media-only one-liners carry little voice; they stay in corpus.jsonl
            return []
        tp = topics(t)
        about = ", ".join(tp) if tp else "AI and building things with it"
        if it.get("is_reply"):
            user = f"Write a short reply on X, in your voice, to someone talking about {about}."
        elif words(t) > 60:
            user = f"Write an X post (long-form / thread style) about {about}."
        else:
            user = f"Write a tweet about {about}."
        pairs.append((user, t))
    elif src == "youtube_short":
        pairs.append((f"Write the script for a short-form vertical video titled \"{title}\".", t))
    else:
        kind = "YouTube video" if src == "youtube" else "newsletter post"
        chunks = chunk_sents(t) if src == "youtube" else [c for c in re.split(r"\n\n(?=\S)", t)]
        if src == "newsletter":   # regroup paragraphs into ~300-word sections
            groups, cur = [], ""
            for c in chunks:
                cur = (cur + "\n\n" + c).strip()
                if words(cur) >= 300:
                    groups.append(cur)
                    cur = ""
            if cur:
                groups.append(cur) if not groups or words(cur) > 80 else groups.__setitem__(-1, groups[-1] + "\n\n" + cur)
            chunks = groups
        for k, c in enumerate(chunks):
            if k == 0:
                user = f"Write the opening of your {kind} titled \"{title}\"."
            else:
                prev_tail = " ".join(chunks[k - 1].split()[-25:])
                tp = topics(c)
                hint = f" This part covers {', '.join(tp)}." if tp else ""
                user = (f"Continue your {kind} \"{title}\" (part {k + 1} of {len(chunks)}).{hint} "
                        f"Pick up right after: \"...{prev_tail}\"")
            pairs.append((user, c))
    return [{"messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": u},
                          {"role": "assistant", "content": a}],
             "source": src, "url": it["url"]} for u, a in pairs]


# ----------------------------------------------------------------------------- main
def main():
    for d in (RAW, DATA):
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)

    items = dedupe(load_youtube() + load_newsletter() + load_x())
    for it in items:
        it["text"] = scrub(it["text"])
        if it.get("title"):
            it["title"] = scrub(it["title"])

    # raw/
    for it in items:
        meta = {k: v for k, v in it.items() if k != "text"}
        meta["words"] = words(it["text"])
        write_raw({"youtube_short": "youtube", "x": "x"}.get(it["source"], it["source"]), it["id"], it["text"], meta)

    # corpus.jsonl
    with open(os.path.join(DATA, "corpus.jsonl"), "w") as f:
        for it in sorted(items, key=lambda x: x.get("date") or ""):
            row = {"text": it["text"], "source": it["source"], "url": it["url"], "date": it.get("date")}
            if it.get("title"):
                row["title"] = it["title"]
            if it.get("multi_speaker"):
                row["multi_speaker"] = True
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    # sft + held-out test (whole items held out, so no chunk of a test video/post is in train)
    eligible = [it for it in items if not it.get("multi_speaker")]
    by_item = [(it, sft_pairs(it)) for it in eligible]
    test_ids, test_rows = set(), []
    for want_src, n_pairs in (("x", 20), ("youtube_short", 5), ("youtube", 25)):
        pool = sorted([b for b in by_item if b[0]["source"] == want_src], key=lambda b: h(b[0]["id"]))
        got = 0
        for it, rows in pool:
            if got >= n_pairs:
                break
            if want_src == "youtube" and len(rows) > 15:
                continue
            test_ids.add(it["id"])
            test_rows += rows
            got += len(rows)
    train_rows = [r for it, rows in by_item if it["id"] not in test_ids for r in rows]
    for name, rows in (("sft.jsonl", train_rows), ("test.jsonl", test_rows)):
        with open(os.path.join(DATA, name), "w") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # stats
    stats = {"built": datetime.now().date().isoformat(), "items": {}, "words": {}, "sft_pairs": len(train_rows),
             "test_pairs": len(test_rows), "test_items_held_out": len(test_ids)}
    for it in items:
        stats["items"][it["source"]] = stats["items"].get(it["source"], 0) + 1
        stats["words"][it["source"]] = stats["words"].get(it["source"], 0) + words(it["text"])
    stats["items_total"] = len(items)
    stats["words_total"] = sum(stats["words"].values())
    dates = sorted(it["date"] for it in items if it.get("date"))
    stats["date_range"] = [dates[0], dates[-1]]
    json.dump(stats, open(os.path.join(DATA, "stats.json"), "w"), indent=1)
    print(json.dumps(stats, indent=1))

    # leak check over everything we are about to publish
    bad = []
    for base in (RAW, DATA):
        for dp, _, fs in os.walk(base):
            for fn in fs:
                t = open(os.path.join(dp, fn)).read()
                bad += [(fn, x) for x in leaks(t) + [m.group(0) for m in PRIVATE_TOKENS.finditer(t)]]
    if bad:
        print("LEAK CHECK FAILED:", bad[:20], file=sys.stderr)
        sys.exit(1)
    print("leak check: clean")


if __name__ == "__main__":
    main()
