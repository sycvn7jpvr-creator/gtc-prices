"""Automatic stop-loss / take-profit exits, the way TMSG handles them.

In the Bloomberg Trading Challenge (TMSG) a stop loss / target is an auto-close trigger: once the price reaches
the level, the position is closed "using the same costing method that was used when the position was opened"
(our opening trades used Next Tick). TMSG notices the trigger with a short delay, so the exit is modelled as
the first trade after the 5-minute bar in which the level was touched (= opening price of the next bar).
Real TMSG prices can be entered by hand ("manual": true) and are then kept.

Writes data/exits.json: {bbg: {"kind": "SL"|"TP", "ts", "date", "px", "lvl", "fx", "shares", "basis"}}.
An exit, once recorded, is never changed or removed: the position is over from that moment.
Fields added by hand are kept: "manual" (price corrected to the real TMSG fill) and "ack" (seen: hide from the list).
"""
import datetime
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
    model = {}  # what the model says for every position, also for exits already recorded (to compare with TMSG)
    for tk, b in buys.items():
        lv = levels.get(tk) or {}
        stop, target = lv.get("stop") or 0, lv.get("target") or 0
        if not (stop or target) or not b["fill_ts"]:
            continue
        try:
            sym, ccy, scale = to_yahoo(tk)
            h = yf.Ticker(sym).history(period="5d", interval="5m", auto_adjust=False).dropna(subset=["Open", "Low", "High"])
        except Exception as e:
            print(f"exits {tk}: no data ({type(e).__name__})")
            continue
        rows = [(int(i.timestamp()), r["Open"] / scale, r["Low"] / scale, r["High"] / scale) for i, r in h.iterrows()]
        for k, (ts, o, lo, hi) in enumerate(rows):
            if ts + 300 <= b["fill_ts"]:  # bar ended before our fill
                continue
            # stop checked first: if both levels sit in one bar, assume the worse case
            kind = "SL" if stop and lo <= stop else "TP" if target and hi >= target else None
            if not kind:
                continue
            # TMSG closes with Next Tick, and it reacts with a short delay: the sale is the first trade AFTER
            # the 5-minute bar in which the level was touched (checked against ChipMOS 19 Oct: TMSG sold at
            # 102.39 TWD during the bounce right after a 99.90 stop was touched at the open)
            if k + 1 >= len(rows):
                print(f"exits {tk}: {kind} touched in the latest bar, waiting for the next trade")
                break
            nts, nopen = rows[k + 1][0], rows[k + 1][1]
            model[tk] = {"kind": kind, "ts": ts, "fill_ts": nts, "px": round(float(nopen), 6),
                         "lvl": stop if kind == "SL" else target, "fx": fx_at(ccy, nts, fx_cache), "shares": b["shares"]}
            if tk not in exits:
                m = model[tk]
                exits[tk] = {"kind": kind, "ts": ts, "date": datetime.datetime.fromtimestamp(nts, datetime.timezone.utc).strftime("%Y-%m-%d"),
                             "px": m["px"], "lvl": m["lvl"], "fx": m["fx"], "shares": b["shares"], "basis": "next tick after the trigger"}
                print(f"exits {tk}: {kind} touched {ts}, sold at {m['px']} (next tick)")
            break
    (DATA / "exits_model.json").write_text(json.dumps(model, separators=(",", ":")))
    (DATA / "exits.json").write_text(json.dumps(exits, separators=(",", ":")))
    print(f"exits: {len(exits)} recorded")


if __name__ == "__main__":
    main()
