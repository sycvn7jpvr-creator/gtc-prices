"""Earnings numbers right after the release.

Yahoo's earnings table (get_earnings_dates) often fills in the reported EPS hours after the release. This module
looks in faster places, in this order:
  1. Yahoo's earnings history (quoteSummary), which is sometimes updated earlier
  2. headlines with both numbers (TheFly, Benzinga, Zacks, MarketBeat, Investing.com style), from Yahoo
     and Google News, published after the release time
  3. a results press release alone ("released": numbers follow)
Each headline source compares the actual EPS with its own consensus, so the pair is kept together.
"""
import datetime as dt
import math
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

NUM = r"\(?-?\$?\(?-?\d+(?:\.\d+)?\)?c?\)?"


def val(s):
    """'21c' -> 0.21, '(5c)' -> -0.05, '$(0.05)' -> -0.05, '-$1.20' -> -1.2"""
    if s is None:
        return None
    s = s.strip()
    neg = s.startswith("-") or "(" in s or "-" in s
    cents = s.lower().endswith("c") or s.lower().endswith("c)")
    m = re.search(r"\d+(?:\.\d+)?", s)
    if not m:
        return None
    v = float(m.group())
    if cents:
        v /= 100
    return -v if neg else v


PAIR = [
    # TheFly: "reports Q3 EPS 21c, consensus 22c" / "Q3 adjusted EPS (5c), two estimates (8c)"
    re.compile(r"\bQ[1-4]\s+(?:adj(?:usted)?\.?\s+|core\s+|non-gaap\s+)?EPS\s+(?P<a>" + NUM + r"),?\s+(?:vs\.?\s+)?(?:the\s+)?(?:consensus|(?:\w+\s+)?estimates?)\s+(?P<e>" + NUM + ")", re.I),
    # Benzinga: "Q3 EPS $0.21 Misses $0.22 Estimate" / "Q3 Adj. EPS $(0.05) Beats $(0.08) Estimate"
    re.compile(r"\bQ[1-4]\s+(?:adj(?:usted)?\.?\s+)?EPS\s+(?P<a>" + NUM + r")\s+(?:beats|misses|in[- ]line(?:\s+with)?|vs\.?)\s+(?P<e>" + NUM + r")\s+estimate", re.I),
    # Zacks: "quarterly loss of $0.05 per share versus the Zacks Consensus Estimate of a loss of $0.08"
    re.compile(r"quarterly (?P<sa>earnings|loss) of \$(?P<a>\d+(?:\.\d+)?) per share versus the Zacks Consensus Estimate of (?P<sl>a loss of )?\$(?P<e>\d+(?:\.\d+)?)", re.I),
    # MarketBeat: "reported $0.21 earnings per share for the quarter, beating ... consensus estimate of $0.18"
    re.compile(r"reported (?:earnings per share \(EPS\) of )?(?P<a>" + NUM + r")(?: earnings per share| EPS)? for the quarter,? (?:topping|beating|missing|meeting|hitting)[^$\d(]{0,60}(?P<e>" + NUM + ")", re.I),
]
# only a difference: "earnings beat by $0.03" (Investing.com), "Beats Estimates By $0.03 EPS" (MarketBeat)
DIFF = re.compile(r"\b(?P<d>beat|beats|missed|misses|matched|matches|tops|topped)\s+(?:(?:consensus\s+)?estimates?\s+)?by\s+(?P<v>" + NUM + ")", re.I)
# only the surprise in %: Zacks "delivered earnings and revenue surprises of +37.50% and ..."
SURP = re.compile(r"earnings and revenue surprises? of\s+(?P<s>[+-]?\d+(?:\.\d+)?)%", re.I)
# only the direction (Zacks titles): "Tops Q3 Earnings", "Lags Q3 Earnings Estimates", "Q3 Earnings Beat Estimates"
DIR = re.compile(r"\b(?:(?P<b>tops?|beats?|surpass(?:es)?)|(?P<m>lags?|miss(?:es)?|falls? short of)|(?P<i>in line with|meets?|matches?))\b[^.]{0,30}\bearnings\b|\bearnings\b[^.]{0,30}\b(?:(?P<b2>beat|top|surpass)|(?P<m2>miss|lag|fall short))", re.I)
# a results press release: "AMC Entertainment Holdings, Inc. Reports Third Quarter 2026 Results"
PR = re.compile(r"\b(reports?|announces?|releases?|posts?)\b.{0,80}\b(results|quarter)\b", re.I)
OPINION = re.compile(r"\b(preview|expect|ahead of|what to|to report|will report|to announce|scheduled|set to|look for|watch)\b", re.I)


def parse(text):
    """-> dict with reported/eps (+ surprise) or diff / surprise / dir, else None"""
    if not text:
        return None
    if OPINION.search(text):
        return None
    for rx in PAIR:
        m = rx.search(text)
        if m:
            a, e = val(m.group("a")), val(m.group("e"))
            gd = m.groupdict()
            if gd.get("sa"):
                a = -abs(a) if gd["sa"].lower() == "loss" else abs(a)
                e = -abs(e) if gd.get("sl") else abs(e)
            if a is not None and e is not None and abs(a) < 1000 and abs(e) < 1000:
                return {"reported": a, "eps": e}
    m = DIFF.search(text)
    if m:
        d = val(m.group("v"))
        if d is not None:
            sign = -1 if m.group("d").lower().startswith("miss") else (0 if m.group("d").lower().startswith("match") else 1)
            return {"diff": sign * abs(d)}
    m = SURP.search(text)
    if m:
        return {"surprise": float(m.group("s"))}
    m = DIR.search(text)
    if m:
        g = m.groupdict()
        return {"dir": "beat" if (g["b"] or g["b2"]) else "miss" if (g["m"] or g["m2"]) else "inline"}
    return None


def num(x):
    try:
        x = float(x)
        return None if math.isnan(x) else x
    except Exception:
        return None


def from_history(t, report_date, dbg):
    """Yahoo earnings history: the row for the quarter that ended 0-100 days before the report"""
    try:
        h = t.get_earnings_history() if hasattr(t, "get_earnings_history") else t.earnings_history
    except Exception as e:
        dbg["hist_error"] = f"{type(e).__name__}: {e}"[:120]
        return None
    if h is None or not len(h):
        return None
    for i, r in h.iterrows():
        q = getattr(i, "date", lambda: None)() if hasattr(i, "date") else None
        if q is None:
            try:
                q = dt.date.fromisoformat(str(i)[:10])
            except Exception:
                continue
        if report_date - dt.timedelta(days=100) < q <= report_date:
            a, e = num(r.get("epsActual")), num(r.get("epsEstimate"))
            if a is not None:
                dbg["hist_row"] = [str(q), a, e]
                return {"reported": a, "eps": e, "src": "Yahoo earnings history"}
    return None


def headlines(sym, names, since):
    """(ts, text, source) for headlines published after `since`: Yahoo search + ticker news, Google News"""
    import yfinance as yf
    out = []
    def add(c, src_default):
        c = c.get("content") or c
        ts = c.get("providerPublishTime")
        if not ts and c.get("pubDate"):
            try:
                ts = int(dt.datetime.fromisoformat(c["pubDate"].replace("Z", "+00:00")).timestamp())
            except Exception:
                ts = None
        text = " ".join(x for x in (c.get("title"), c.get("summary"), c.get("description")) if isinstance(x, str))
        src = (c.get("provider") or {}).get("displayName") or c.get("publisher") or src_default
        url = (c.get("canonicalUrl") or {}).get("url") or (c.get("clickThroughUrl") or {}).get("url") or c.get("link") or ""
        if ts and ts >= since and text:
            out.append((int(ts), text, src, url))
    try:
        for n in yf.Search(sym, news_count=25, max_results=1).news or []:
            add(n, "Yahoo")
    except Exception:
        pass
    try:
        for n in yf.Ticker(sym).news or []:
            add(n, "Yahoo")
    except Exception:
        pass
    for q in [f'"{v}" EPS' for v in names[:2]] + [f"{sym} earnings"]:
        try:
            url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({"q": q + " when:2d", "hl": "en-US", "gl": "US", "ceid": "US:en"})
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (gtc-prices news bot)"})
            root = ET.fromstring(urllib.request.urlopen(req, timeout=15).read())
            for it in root.iter("item"):
                try:
                    ts = int(parsedate_to_datetime(it.findtext("pubDate")).timestamp())
                except Exception:
                    continue
                if ts >= since:
                    out.append((ts, (it.findtext("title") or "").strip(), (it.findtext("source") or "Google News").strip(), ""))
        except Exception:
            pass
    return sorted(set(out))


def fast_result(t, sym, names, ev, dbg):
    """Numbers for an earnings event whose release time has passed; None if nothing found yet."""
    d = dt.date.fromisoformat(ev["date"])
    since = (ev.get("ts") or int(dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc).timestamp())) - 20 * 60
    r = from_history(t, d, dbg)
    if r and r.get("eps") is None:
        r["eps"] = ev.get("eps")
    if r:
        return r
    hs = headlines(sym, names, since)
    dbg["headlines"] = len(hs)
    best = {}
    name_rx = re.compile("|".join(re.escape(n) for n in names + [sym]), re.I) if names else None
    for ts, text, src, url in hs:
        if name_rx and not name_rx.search(text):
            continue  # headline about another company
        p = parse(text)
        if p:
            rank = 0 if "reported" in p else 1 if "diff" in p else 2 if "surprise" in p else 3
            if rank < best.get("rank", 9):
                best = dict(p, rank=rank, src=src, headline=text[:160], hts=ts)
        elif "released" not in best and PR.search(text) and not OPINION.search(text):
            best.setdefault("released", ts)
            best.setdefault("pr", text[:160])
            best.setdefault("pr_url", url)
    sec = edgar_release(sym, since, dbg) if "." not in sym else None  # also checked when a headline has it (keeps it tested)
    if "rank" in best:
        r = {k: best[k] for k in ("reported", "eps", "diff", "surprise", "dir") if k in best}
        if "diff" in r and ev.get("eps") is not None:  # actual = consensus + difference
            r["eps"], r["reported"] = ev["eps"], round(ev["eps"] + r.pop("diff"), 4)
        r.pop("diff", None)
        r["src"] = best["src"]
        dbg["fast_headline"] = best["headline"]
        return r
    # US companies file the press release with the SEC (8-K, item 2.02) within minutes: read EPS from there
    if sec:
        best.setdefault("released", sec[0])
        best.setdefault("pr", "SEC 8-K")
    if "released" in best:
        dbg["press_release"] = best["pr"]
        out = {"released": best["released"]}
        got = press_release_eps(sec[1], dbg) if sec else None
        if not got and best.get("pr_url"):  # read EPS from the press release page
            got = press_release_eps(best["pr_url"], dbg)
            if got:
                a, kind = got
                out.update(reported=a, eps=ev.get("eps"), src="press release" + ("" if kind == "adjusted" else ", GAAP"))
        return out
    return None


EPS_LINE = re.compile(
    r"(?P<adj>adjusted\s+|non-gaap\s+)?(?:diluted\s+)?(?:net\s+)?(?:earnings|income|loss|\(loss\)|earnings\s*\(loss\)|income\s*\(loss\)|\(loss\)\s*income)"
    r"\s+per\s+(?:diluted\s+)?(?:common\s+)?share(?:\s*\(\s*(?:diluted|eps)\s*\))?(?:,?\s*diluted)?"
    r"(?:\s+(?:was|were|of|totaled|amounted to|came in at))?\s*(?P<v>\(?-?\$\s?\(?\d+\.\d+\)?)", re.I)
EPS_SHORT = re.compile(r"(?P<adj>adjusted\s+|non-gaap\s+)?(?:diluted\s+)?EPS(?:\s+(?:was|were|of))?\s*(?P<v>\(?-?\$\s?\(?\d+\.\d+\)?)", re.I)


SEC_UA = "gtc-prices tracker sycvn7jpvr-creator@users.noreply.github.com"
_CIK = {}


def _get(url, ua=SEC_UA, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept-Encoding": "identity"})
    return urllib.request.urlopen(req, timeout=timeout).read()


def edgar_release(sym, since, dbg):
    """(filed_ts, exhibit_url) of an earnings 8-K (item 2.02) filed after `since`, else None"""
    import json
    try:
        if not _CIK:
            for r in json.loads(_get("https://www.sec.gov/files/company_tickers.json")).values():
                _CIK[r["ticker"].upper()] = int(r["cik_str"])
        cik = _CIK.get(sym.upper())
        if not cik:
            dbg["sec"] = "no CIK"
            return None
        sub = json.loads(_get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json"))
        rec = sub["filings"]["recent"]
        for i, form in enumerate(rec["form"][:40]):
            if form not in ("8-K", "6-K") or "2.02" not in (rec.get("items") or [""] * 40)[i]:
                continue
            ts = int(dt.datetime.fromisoformat(rec["acceptanceDateTime"][i].replace("Z", "+00:00")).timestamp())
            if ts < since:
                break
            acc = rec["accessionNumber"][i].replace("-", "")
            idx = json.loads(_get(f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/index.json"))
            files = [f["name"] for f in idx["directory"]["item"]]
            ex = [f for f in files if re.search(r"ex[-_]?99", f, re.I) and f.lower().endswith((".htm", ".html", ".txt"))]
            dbg["sec"] = {"filed": rec["acceptanceDateTime"][i], "files": ex[:3]}
            if ex:
                return ts, f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{sorted(ex)[0]}"
        dbg.setdefault("sec", "no new 8-K 2.02")
    except Exception as e:
        dbg["sec"] = f"{type(e).__name__}: {e}"[:120]
    return None


def press_release_eps(url, dbg):
    """(eps, 'adjusted'|'gaap') from the first EPS figure in the release text (its highlights = this quarter)"""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"})
        html = (_get(url) if "sec.gov" in url else urllib.request.urlopen(req, timeout=20).read()).decode("utf-8", "ignore")
    except Exception as e:
        dbg["pr_fetch"] = f"{type(e).__name__}: {e}"[:120]
        return None
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;|&#160;", " ", text)
    text = re.sub(r"\s+", " ", text)
    dbg["pr_chars"] = len(text)
    dbg["pr_url"] = url[:160]
    k = text.lower().find("per share")
    dbg["pr_snip"] = text[max(0, k - 250):k + 150] if k >= 0 else text[:300] + " … " + text[-300:]
    hits = []
    for rx in (EPS_LINE, EPS_SHORT):
        for m in rx.finditer(text[:20000]):
            loss = re.search(r"loss", m.group(0), re.I) and not re.search(r"earnings|income", m.group(0), re.I)
            v = val(m.group("v").replace(" ", ""))
            if v is None or abs(v) > 500:
                continue
            if loss and v > 0:
                v = -v
            hits.append((m.start(), bool(m.group("adj")), v, m.group(0)[:90]))
    if not hits:
        return None
    hits.sort()
    adj = [h for h in hits if h[1]]
    pick = adj[0] if adj else hits[0]
    dbg["pr_eps"] = [pick[3], pick[2]]
    return pick[2], "adjusted" if pick[1] else "gaap"
