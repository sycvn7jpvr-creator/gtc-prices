"""Collect recent headlines for every stock the team holds (Yahoo Finance + Google News RSS).

Writes data/news.json: {"updated": iso, "items": {bbg_ticker: [{title, url, source, ts}, ...]}}
Only headline, source, time and link are stored; the article itself stays on the publisher's site.
Runs with every price run (every 6 minutes).
"""
import json
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from pathlib import Path

import yfinance as yf

from fetch_prices import to_yahoo

ROOT = Path(__file__).parent
OUT = ROOT / "data" / "news.json"
MAX_AGE_DAYS = 14
PER_STOCK = 8


def fresh_enough():
    if not OUT.exists():
        return False
    try:
        upd = json.loads(OUT.read_text()).get("updated_ts", 0)
    except Exception:
        return False
    return time.time() - upd < 50 * 60


def names_by_ticker():
    trades = json.loads((ROOT / "data" / "trades.json").read_text()) if (ROOT / "data" / "trades.json").exists() else []
    out = {}
    for t in trades:
        out[t["ticker"]] = t.get("name") or t["ticker"]
    for l in (ROOT / "tickers.txt").read_text().splitlines():
        l = l.strip()
        if l and not l.startswith("#"):
            out.setdefault(l, l)
    return out


def yahoo_news(sym):
    items = []
    for n in (yf.Ticker(sym).news or [])[:15]:
        c = n.get("content") or n
        title = c.get("title")
        url = ((c.get("canonicalUrl") or {}).get("url") or (c.get("clickThroughUrl") or {}).get("url") or c.get("link"))
        src = (c.get("provider") or {}).get("displayName") or c.get("publisher") or "Yahoo Finance"
        ts = c.get("providerPublishTime")
        if not ts and c.get("pubDate"):
            ts = int(time.mktime(time.strptime(c["pubDate"][:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone)
        if title and url and ts:
            items.append({"title": title, "url": url, "source": src, "ts": int(ts)})
    return items


def google_news(name):
    variants = [v.strip(" )") for v in name.split(" (")]
    # articles usually use the short name: "Bravida", "SMA Solar", "Oerlikon"
    short = [re.sub(r"\s+(Holdings?|Technologies|Technology|AG|SA|NV|plc|Inc\.?)$", "", v) for v in variants]
    short = [re.sub(r"^OC\s+", "", v) for v in short]
    variants = list(dict.fromkeys(v for v in variants + short if len(v) >= 4))
    q = " OR ".join(f'"{v}"' for v in variants if v)
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({"q": q + " when:14d", "hl": "en-US", "gl": "US", "ceid": "US:en"})
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (gtc-prices news bot)"})
    root = ET.fromstring(urllib.request.urlopen(req, timeout=20).read())
    items = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        src = (it.findtext("source") or "").strip()
        if src and title.endswith(" - " + src):
            title = title[: -len(" - " + src)]
        try:
            ts = int(parsedate_to_datetime(it.findtext("pubDate")).timestamp())
        except Exception:
            continue
        if title and it.findtext("link"):
            items.append({"title": title, "url": it.findtext("link"), "source": src or "Google News", "ts": ts})
        if len(items) >= 15:
            break
    return items


JUNK = re.compile(r"stock price,? (news|quote)|quote & history|share price (&|and) news|stock quote", re.I)
# sources that match a company name but are never about the company (e.g. football at "Bravida Arena")
BLOCKED_SOURCES = {"transfermarkt"}


def norm(t):
    return re.sub(r"[^a-z0-9]", "", t.lower())[:60]


def main():
    cutoff = time.time() - MAX_AGE_DAYS * 86400
    out = {"updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "updated_ts": int(time.time()), "items": {}}
    for bbg, name in names_by_ticker().items():
        found = []
        try:
            found += yahoo_news(to_yahoo(bbg)[0])
        except Exception as e:
            print(f"yahoo news {bbg}: {e}")
        try:
            found += google_news(name)
        except Exception as e:
            print(f"google news {bbg}: {e}")
        seen, keep = set(), []
        for n in sorted(found, key=lambda x: -x["ts"]):
            k = norm(n["title"])
            if n["ts"] < cutoff or k in seen or JUNK.search(n["title"]) or n["source"].strip().lower() in BLOCKED_SOURCES:
                continue
            seen.add(k)
            keep.append(n)
        out["items"][bbg] = keep[:PER_STOCK]
        print(f"news {bbg}: {len(keep[:PER_STOCK])}")
    OUT.write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False))


if __name__ == "__main__":
    main()
