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
PER_STOCK = 12


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


def _yahoo_item(n):
    c = n.get("content") or n
    title = c.get("title")
    url = ((c.get("canonicalUrl") or {}).get("url") or (c.get("clickThroughUrl") or {}).get("url") or c.get("link"))
    src = (c.get("provider") or {}).get("displayName") or c.get("publisher") or "Yahoo Finance"
    ts = c.get("providerPublishTime")
    if not ts and c.get("pubDate"):
        try:
            ts = int(time.mktime(time.strptime(c["pubDate"][:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone)
        except Exception:
            ts = None
    return {"title": title, "url": url, "source": src, "ts": int(ts)} if title and url and ts else None


def short_names(name):
    variants = [v.strip(" )") for v in name.split(" (")]
    short = [re.sub(r"\s+(Holdings?|Technologies|Technology|AG|SA|NV|plc|Inc\.?)$", "", v) for v in variants]
    short = [re.sub(r"^OC\s+", "", v) for v in short]
    return list(dict.fromkeys(v for v in variants + short if len(v) >= 4))


def yahoo_news(sym, dbg, name=""):
    """Yahoo Finance headlines: the search endpoint (what finance.yahoo.com uses) by symbol and by
    company name, keeping name hits only when Yahoo tags them with the stock or the title names it."""
    raw = []
    try:
        res = yf.Search(sym, news_count=15, max_results=1)
        raw += list(res.news or [])
        dbg["yahoo_search_raw"] = len(res.news or [])
    except Exception as e:
        dbg["yahoo_search_error"] = f"{type(e).__name__}: {e}"[:160]
    names = short_names(name) if name else []
    for q in names[-1:]:  # the short name, e.g. "SMA Solar"
        try:
            hits = yf.Search(q, news_count=20, max_results=1).news or []
            ok = [n for n in hits if sym in (n.get("relatedTickers") or [])
                  or any(v.lower() in (n.get("title") or "").lower() for v in names)]
            dbg["yahoo_name_raw"] = len(hits)
            dbg["yahoo_name_kept"] = len(ok)
            raw += ok
        except Exception as e:
            dbg["yahoo_name_error"] = f"{type(e).__name__}: {e}"[:160]
    try:
        tn = yf.Ticker(sym).news or []
        dbg["yahoo_ticker_raw"] = len(tn)
        if tn and not raw:
            dbg["yahoo_ticker_keys"] = sorted((tn[0].get("content") or tn[0]).keys())[:12]
        raw += list(tn)
    except Exception as e:
        dbg["yahoo_ticker_error"] = f"{type(e).__name__}: {e}"[:160]
    return [x for x in (_yahoo_item(n) for n in raw) if x]


def google_news(name):
    variants = short_names(name)  # articles usually use the short name: "Bravida", "SMA Solar", "Oerlikon"
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
    out = {"updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "updated_ts": int(time.time()), "items": {}, "debug": {}}
    for bbg, name in names_by_ticker().items():
        found, dbg = [], {}
        try:
            y = yahoo_news(to_yahoo(bbg)[0], dbg, name)
            dbg["yahoo"] = len(y)
            found += y
        except Exception as e:
            dbg["yahoo_error"] = f"{type(e).__name__}: {e}"[:200]
        try:
            g = google_news(name)
            dbg["google"] = len(g)
            found += g
        except Exception as e:
            dbg["google_error"] = f"{type(e).__name__}: {e}"[:200]
        out["debug"][bbg] = dbg
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
