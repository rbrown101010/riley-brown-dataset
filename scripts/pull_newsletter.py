#!/usr/bin/env python3
"""Pull Riley Brown's public newsletter posts (learn.agentnative.inc RSS) into work/newsletter/posts.json.
Only free, public posts bylined to Riley Brown are kept."""
import html, json, os, re, subprocess
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "work", "newsletter")
os.makedirs(OUT, exist_ok=True)
FEED = "https://learn.agentnative.inc/feed"
NS = {"content": "http://purl.org/rss/1.0/modules/content/", "dc": "http://purl.org/dc/elements/1.1/"}


class Text(HTMLParser):
    BLOCK = {"p", "h1", "h2", "h3", "h4", "li", "blockquote", "br", "div", "pre"}

    def __init__(self):
        super().__init__()
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "figure", "figcaption", "button"):
            self.skip += 1
        if tag in self.BLOCK:
            self.out.append("\n")
        if tag == "li":
            self.out.append("- ")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "figure", "figcaption", "button") and self.skip:
            self.skip -= 1
        if tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, d):
        if not self.skip:
            self.out.append(d)


def to_text(h):
    p = Text()
    p.feed(h or "")
    t = html.unescape("".join(p.out))
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", t)).strip()


if __name__ == "__main__":
    root = ET.fromstring(subprocess.run(["curl", "-sL", "-m", "60", FEED], capture_output=True).stdout)
    posts = []
    for it in root.find("channel").findall("item"):
        if (it.findtext("dc:creator", namespaces=NS) or "").strip() != "Riley Brown":
            continue
        posts.append({"title": it.findtext("title"), "url": it.findtext("link"),
                      "date": it.findtext("pubDate"),
                      "text": to_text(it.findtext("content:encoded", namespaces=NS))})
    json.dump(posts, open(os.path.join(OUT, "posts.json"), "w"), indent=1)
    print(len(posts), "newsletter posts")
