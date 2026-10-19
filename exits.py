"""Automatic stop-loss / take-profit exits, the way TMSG handles them.

In the Bloomberg Trading Challenge (TMSG) a stop loss / target is an auto-close trigger: once the price reaches
the level, the position is closed "using the same costing method that was used when the position was opened"
(our opening trades used Next Tick). So the exit is the next trade after the level is touched:
  - level crossed inside a 5-minute bar  -> filled at the level itself
  - price gapped through the level       -> filled at the bar's opening price (first trade after the gap)

Writes data/exits.json: {bbg: {"kind": "SL"|"TP", "ts", "date", "px", "lvl", "fx", "shares", "basis"}}.
An exit, once recorded, is never changed or removed: the position is over from that moment.
Fields added by hand are kept: "manual" (price corrected to the real TMSG fill) and "ack" (seen: hide from the list).
"""
import json
from pathlib import Path

import yfinance as yf

from fetch_prices import to_yahoo

ROOT = Path(__file__).parent
DATA = ROOT / "data"
OUT = DATA / "exits.json"


def load(name, default):
    try:
        return json.loads((DATA / name).read_text())
    except Exception:
        return default


def fx_at(ccy, ts, cache):
    """USD per 1 unit of ccy at time ts (5-minute FX bars, last bar at or before ts)."""
    if ccy == "USD":
        return 1.0
    if ccy not in cache:
        try:
            cache[ccy] = yf.Ticker(f"{ccy}USD=X").history(period="5d", interval="5m").dropna(subset=["Close"])
        except Exception:
            cache[ccy] = None
    h = cache[ccy]
    if h is not None and not h.empty:
        before = h[[i.timestamp() <= ts for i in h.index]]
        row = before.iloc[-1] if not before.empty else h.iloc[0]
        return float(row["Close"])
    fx = (load("latest.json", {}).get("fx", {}).get(ccy) or {}).get("v")
    return float(fx) if fx else None


def main():
    trades = load("trades.json", [])
    levels = load("levels.json", {})
    exits = load("exits.json", {})
    fx_cache = {}
    buys = {}
    for t in trades:
        if t.get("side") == "BUY" and (t.get("price") or 0) > 0:
            fx = 1.0 if t.get("ccy") == "USD" else (t.get("fx") or 0)
            if fx <= 0:
                continue
            b = buys.setdefault(t["ticker"], {"shares": 0.0, "fill_ts": None, "ccy": t.get("ccy")})
            b["shares"] += (t.get("usd") or 0) / (t["price"] * fx)
            ts = t.get("fillTs")
            b["fill_ts"] = ts if b["fill_ts"] is None else min(b["fill_ts"], ts or b["fill_ts"])
    for tk, b in buys.items():
        lv = levels.get(tk) or {}
        stop, target = lv.get("stop") or 0, lv.get("target") or 0
        if tk in exits or not (stop or target) or not b["fill_ts"]:
            continue
        try:
            sym, ccy, scale = to_yahoo(tk)
            h = yf.Ticker(sym).history(period="5d", interval="5m", auto_adjust=False).dropna(subset=["Low", "High"])
        except Exception as e:
            print(f"exits {tk}: no data ({type(e).__name__})")
            continue
        prev_day = None
        for idx, r in h.iterrows():
            ts = int(idx.timestamp())
            day = idx.strftime("%Y-%m-%d")
            first_bar = day != prev_day  # first 5-minute bar of a trading day (opening auction)
            prev_day = day
            if ts + 300 <= b["fill_ts"]:  # bar ended before our fill
                continue
            o, lo, hi = r["Open"] / scale, r["Low"] / scale, r["High"] / scale
            hit = None
            # triggered at the open (first bar): TMSG fills at the opening price (seen with ChipMOS on 19 Oct:
            # TMSG cash implied 102.39 TWD vs a 99.90 stop); otherwise at the level, or the open on a gap
            if stop and lo <= stop:  # stop checked first: if both levels sit in one bar, assume the worse case
                hit = ("SL", stop, o, "opening price") if first_bar else ("SL", stop, o if o <= stop else stop, "gap open" if o <= stop else "stop level")
            elif target and hi >= target:
                hit = ("TP", target, o, "opening price") if first_bar else ("TP", target, o if o >= target else target, "gap open" if o >= target else "target level")
            if hit:
                kind, lvl, px, basis = hit
                exits[tk] = {"kind": kind, "ts": ts, "date": idx.strftime("%Y-%m-%d"), "px": round(float(px), 6),
                             "lvl": lvl, "fx": fx_at(ccy, ts, fx_cache), "shares": b["shares"], "basis": basis}
                print(f"exits {tk}: {kind} at {px} ({basis}) {idx}")
                break
    (DATA / "exits.json").write_text(json.dumps(exits, separators=(",", ":")))
    print(f"exits: {len(exits)} recorded")


if __name__ == "__main__":
    main()
