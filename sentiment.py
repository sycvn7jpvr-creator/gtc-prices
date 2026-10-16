"""News mood: FinBERT (ProsusAI/finbert) labels each headline positive / negative / neutral.

Reads data/news.json, scores only headlines it has not seen before and writes data/sentiment.json:
  {"updated": iso, "model": "ProsusAI/finbert", "items": {key: {"l": "positive"|"negative"|"neutral", "p": 0.93}}}
key = first 16 hex chars of sha1(normalised title), so the website can look a headline up by its title.

Every run also copies the label into data/news.json ("mood": {"l": ..., "p": ...} on each headline) so the
website needs no extra lookup; that part needs no model, so it runs even when nothing new came in.
`python sentiment.py --need` exits 0 when there are unscored headlines (the workflow only installs the model then).
"""
import datetime as dt
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent
NEWS = ROOT / "data" / "news.json"
OUT = ROOT / "data" / "sentiment.json"
MODEL = "ProsusAI/finbert"


def key(title):
    t = re.sub(r"\s+", " ", (title or "").strip().lower())
    return hashlib.sha1(t.encode("utf-8")).hexdigest()[:16]


def titles():
    try:
        items = json.loads(NEWS.read_text()).get("items", {})
    except Exception:
        return {}
    return {key(n["title"]): n["title"] for lst in items.values() for n in lst if n.get("title")}


def load():
    try:
        return json.loads(OUT.read_text()).get("items", {})
    except Exception:
        return {}


def main():
    want, have = titles(), load()
    todo = {k: t for k, t in want.items() if k not in have}
    if "--need" in sys.argv:
        sys.exit(0 if todo else 1)
    if todo:
        try:
            from transformers import pipeline
        except ImportError:  # model not installed in this run: just annotate what is known
            todo = {}
    if todo:  # heavy import only when there is something to score

        clf = pipeline("text-classification", model=MODEL, top_k=None, truncation=True)
        keys, texts = list(todo), list(todo.values())
        for i in range(0, len(texts), 16):
            for k, scores in zip(keys[i:i + 16], clf(texts[i:i + 16])):
                best = max(scores, key=lambda s: s["score"])
                have[k] = {"l": best["label"].lower(), "p": round(float(best["score"]), 3)}
    keep = {k: v for k, v in have.items() if k in want}  # drop headlines that left the news feed
    try:
        news = json.loads(NEWS.read_text())
        for lst in news.get("items", {}).values():
            for n in lst:
                m = keep.get(key(n.get("title")))
                if m:
                    n["mood"] = m
        NEWS.write_text(json.dumps(news, separators=(",", ":"), ensure_ascii=False))
    except Exception as e:
        print(f"could not annotate news.json: {e}")
    OUT.write_text(json.dumps({"updated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                               "model": MODEL, "items": keep}, separators=(",", ":")))
    print(f"sentiment: {len(todo)} new, {len(keep)} kept")


if __name__ == "__main__":
    main()
