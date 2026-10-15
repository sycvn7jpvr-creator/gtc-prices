"""Upcoming company events for every stock the team holds (Yahoo Finance).

Writes data/events.json:
  {"updated": iso, "items": {bbg: [{"type", "date", "ts", "timeKnown", "estimate", "title", "detail"}, ...]}}
type: "earnings" | "exdiv" | "divpay". date is YYYY-MM-DD (exchange-local day), ts is unix seconds when the
exact time is known. Runs with every price run; if Yahoo fails for a stock, its last known events are kept.
"""
import datetime as dt
import json
import math
from pathlib import Path

import yfinance as yf

from fetch_prices import to_yahoo

ROOT = Path(__file__).parent
OUT = ROOT / "data" / "events.json"
HORIZON_DAYS = 120
TODAY = dt.datetime.now(dt.timezone.utc).date()


def tickers():
    return [l.strip() for l in (ROOT / "tickers.txt").read_text().splitlines() if l.strip() and not l.startswith("#")]


def num(x):
    try:
        x = float(x)
        return None if math.isnan(x) else x
    except Exception:
        return None


def as_date(x):
    """date from a date / datetime / pandas Timestamp / unix seconds."""
    if x is None:
        return None
    if isinstance(x, (int, float)):
        if not x or (isinstance(x, float) and math.isnan(x)):
            return None
        return dt.datetime.fromtimestamp(x, dt.timezone.utc).date()
    if hasattr(x, "date") and callable(x.date):
        return x.date()
    if isinstance(x, dt.date):
        return x
    try:
        return dt.date.fromisoformat(str(x)[:10])
    except Exception:
        return None


def in_window(d):
    return d is not None and TODAY - dt.timedelta(days=1) <= d <= TODAY + dt.timedelta(days=HORIZON_DAYS)


def events_for(sym, dbg):
    t = yf.Ticker(sym)
    out = []
    info, cal, ed = {}, {}, None
    try:
        info = t.info or {}
    except Exception as e:
        dbg["info_error"] = f"{type(e).__name__}: {e}"[:160]
    try:
        cal = t.calendar or {}
        if not isinstance(cal, dict):  # older yfinance returned a DataFrame
            cal = {k: list(v.values) if len(v.values) > 1 else v.values[0] for k, v in cal.iterrows()} if hasattr(cal, "iterrows") else {}
    except Exception as e:
        dbg["calendar_error"] = f"{type(e).__name__}: {e}"[:160]
    try:
        ed = t.get_earnings_dates(limit=8)
    except Exception as e:
        dbg["earnings_dates_error"] = f"{type(e).__name__}: {e}"[:160]

    dbg["info"] = {k: info.get(k) for k in ("earningsTimestamp", "earningsTimestampStart", "earningsTimestampEnd",
                                             "isEarningsDateEstimate", "earningsCallTimestampStart", "exDividendDate",
                                             "dividendDate", "lastDividendValue", "dividendRate", "currency")}
    dbg["calendar"] = {k: str(v)[:80] for k, v in cal.items()}

    ccy = info.get("currency") or ""

    # ---- earnings: exact time from the earnings table when Yahoo has it, else the calendar day(s)
    e_date, e_ts, e_time, e_est, e_eps, e_range = None, None, False, None, None, None
    if ed is not None and len(ed):
        fut = [(i, r) for i, r in ed.iterrows() if as_date(i) and as_date(i) >= TODAY - dt.timedelta(days=1)]
        dbg["earnings_dates_future"] = [str(i) for i, _ in fut][:4]
        if fut:
            i, r = sorted(fut, key=lambda x: x[0])[0]
            e_date = as_date(i)
            ts = int(i.timestamp())
            local = i.to_pydatetime()
            if local.hour or local.minute:
                e_ts, e_time = ts, True
            e_eps = num(r.get("EPS Estimate"))
    # Yahoo keeps the last reported date in earningsTimestamp; the next one is in Start/End
    cands = [x for x in (info.get("earningsTimestamp"), info.get("earningsTimestampStart"), info.get("earningsTimestampEnd"))
             if x and as_date(x) and as_date(x) >= TODAY - dt.timedelta(days=1)]
    its = min(cands) if cands else None
    if its:
        d = as_date(its)
        if e_date is None or abs((d - e_date).days) <= 1:
            e_date = e_date or d
            if not e_time:
                ts_dt = dt.datetime.fromtimestamp(its, dt.timezone.utc)
                if not (ts_dt.hour == 0 and ts_dt.minute == 0) and info.get("isEarningsDateEstimate") is False:
                    e_ts, e_time = int(its), True
        e_est = info.get("isEarningsDateEstimate")
    cal_dates = [as_date(x) for x in (cal.get("Earnings Date") or [])] if isinstance(cal.get("Earnings Date"), (list, tuple)) else [as_date(cal.get("Earnings Date"))]
    cal_dates = sorted(d for d in cal_dates if d)
    if cal_dates and cal_dates[-1] >= TODAY - dt.timedelta(days=1):
        if e_date is None:
            e_date = cal_dates[0]
        if len(cal_dates) > 1 and cal_dates[0] != cal_dates[-1]:
            e_range = [cal_dates[0].isoformat(), cal_dates[-1].isoformat()]
            if e_est is None:
                e_est = True
    if e_eps is None:
        e_eps = num(cal.get("Earnings Average"))
    if in_window(e_date):
        out.append({"type": "earnings", "date": e_date.isoformat(), "ts": e_ts, "timeKnown": e_time,
                    "estimate": bool(e_est), "range": e_range, "eps": e_eps, "ccy": ccy,
                    "rev": num(cal.get("Revenue Average"))})

    # ---- dividends: Yahoo keeps the last ex-date, so only future ones count
    ex = as_date(cal.get("Ex-Dividend Date")) or as_date(info.get("exDividendDate"))
    last_div = num(info.get("lastDividendValue"))
    if in_window(ex) and ex >= TODAY:
        out.append({"type": "exdiv", "date": ex.isoformat(), "amount": last_div, "ccy": ccy})
    pay = as_date(cal.get("Dividend Date")) or as_date(info.get("dividendDate"))
    if in_window(pay) and pay >= TODAY:
        out.append({"type": "divpay", "date": pay.isoformat(), "amount": last_div, "ccy": ccy})
    return sorted(out, key=lambda e: e["date"])


def extra_events():
    """Company-confirmed dates Yahoo misses (events_extra.json, edited by hand)."""
    p = ROOT / "events_extra.json"
    try:
        return {k: v for k, v in json.loads(p.read_text()).items() if not k.startswith("_")} if p.exists() else {}
    except Exception:
        return {}


def merge(ev, extra):
    for x in extra:
        d = as_date(x.get("date"))
        if not in_window(d) or d < TODAY:
            continue
        clash = [e for e in ev if e["type"] == x["type"] and abs((as_date(e["date"]) - d).days) <= 3]
        if clash:  # the company's date wins over Yahoo's
            for e in clash:
                ev.remove(e)
        ev.append(dict({"ts": None, "timeKnown": False, "estimate": False}, **x))
    return sorted(ev, key=lambda e: e["date"])


def main():
    extras = extra_events()
    prev = {}
    if OUT.exists():
        try:
            prev = json.loads(OUT.read_text()).get("items", {})
        except Exception:
            prev = {}
    out = {"updated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "items": {}, "debug": {}}
    for bbg in tickers():
        dbg = {}
        try:
            sym = to_yahoo(bbg)[0]
            dbg["sym"] = sym
            ev = events_for(sym, dbg)
            failed = "info_error" in dbg and "calendar_error" in dbg and "earnings_dates_error" in dbg
            # keep the last good list if Yahoo failed completely this time; drop events that are now in the past
            if failed and bbg in prev:
                ev = [e for e in prev[bbg] if e["date"] >= (TODAY - dt.timedelta(days=1)).isoformat()]
                dbg["kept_previous"] = True
            out["items"][bbg] = merge(ev, extras.get(bbg, []))
        except Exception as e:
            dbg["error"] = f"{type(e).__name__}: {e}"[:200]
            out["items"][bbg] = prev.get(bbg, [])
        out["debug"][bbg] = dbg
        print(f"events {bbg}: {[(e['type'], e['date']) for e in out['items'][bbg]]}")
    OUT.write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
