"""WLS index (Bloomberg World Large, Mid & Small Cap, BBG:WLS) from TradingView's chart data feed.

Runs after fetch_prices.py. When TradingView delivers, it replaces the ACWI stand-in in
data/latest.json ("bench") and data/history.json ("bench") with the real index, in the same format:
  latest.bench  = {p, d, t, i: [[ts, px]...] (last ~30 h), w: [[ts, px]...] (last 7 days, hourly), prev, sym, name}
  history.bench = {sym, rows: [[date, open, close], ...]}
If anything fails, nothing is changed and the site keeps the ACWI stand-in. What happened is written to
data/wls_tv.json (status, bar counts, last price) so it can be checked without the run log.
"""
import datetime as dt
import json
import random
import string
import time
from pathlib import Path

from zoneinfo import ZoneInfo

import websocket  # websocket-client

ROOT = Path(__file__).parent
DATA = ROOT / "data"
SYMBOL = "BBG:WLS"
NAME = "Bloomberg World Large, Mid & Small Cap Index (TradingView)"
URL = "wss://data.tradingview.com/socket.io/websocket?from=chart%2F&type=chart"
HEADERS = ["Origin: https://www.tradingview.com", "User-Agent: Mozilla/5.0"]


def _frame(func, params):
    body = json.dumps({"m": func, "p": params}, separators=(",", ":"))
    return f"~m~{len(body)}~m~{body}"


def _messages(raw):
    """Split one websocket payload into its ~m~len~m~ parts."""
    out, i = [], 0
    while raw.startswith("~m~", i):
        j = raw.index("~m~", i + 3)
        n = int(raw[i + 3:j])
        out.append(raw[j + 3:j + 3 + n])
        i = j + 3 + n
    return out


def _sid(prefix):
    return prefix + "".join(random.choice(string.ascii_lowercase) for _ in range(12))


def fetch(resolutions, timeout=40):
    """{resolution: [[ts, o, h, l, c], ...]} for BBG:WLS."""
    ws = websocket.create_connection(URL, header=HEADERS, timeout=timeout)
    cs = _sid("cs_")
    send = lambda f, p: ws.send(_frame(f, p))
    send("set_auth_token", ["unauthorized_user_token"])
    send("chart_create_session", [cs, ""])
    send("resolve_symbol", [cs, "sym_1", "=" + json.dumps({"symbol": SYMBOL, "adjustment": "splits", "session": "regular"})])
    series = {}
    for k, (res, n) in enumerate(resolutions.items(), 1):
        series[f"s{k}"] = res
        send("create_series", [cs, f"sds_{k}", f"s{k}", "sym_1", res, n, ""])
    bars, done, errors, info = {r: [] for r in resolutions}, 0, [], {}
    end = time.time() + timeout
    while time.time() < end and done < len(series):
        raw = ws.recv()
        for m in _messages(raw):
            if m.startswith("~h~"):  # heartbeat: echo it back
                ws.send(f"~m~{len(m)}~m~{m}")
                continue
            try:
                d = json.loads(m)
            except ValueError:
                continue
            kind = d.get("m")
            if kind == "symbol_resolved":
                info = d["p"][2] if len(d.get("p", [])) > 2 and isinstance(d["p"][2], dict) else {}
            elif kind == "timescale_update":  # full history of a series ("du" = live updates, ignored)
                for key, v in (d.get("p", [None, {}])[1] or {}).items():
                    k = key.replace("sds_", "s")
                    if k in series and isinstance(v, dict) and v.get("s"):
                        bars[series[k]] = [b["v"][:5] for b in v["s"]]
            elif kind == "series_completed":
                done += 1
            elif kind in ("symbol_error", "series_error", "critical_error", "protocol_error"):
                errors.append(m[:300])
                if kind != "series_error":
                    done = len(series)  # give up
                else:
                    done += 1
    ws.close()
    return bars, errors, info


def main():
    status = {"checked": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "symbol": SYMBOL}
    try:
        bars, errors, info = fetch({"1D": 600, "5": 400, "60": 200})
        tzname = info.get("timezone") or "Etc/UTC"
        status.update(errors=errors, name=info.get("description"), timezone=tzname, session=info.get("session"))
        try:
            tz = ZoneInfo(tzname)
        except Exception:
            tz = dt.timezone.utc
        status["bars"] = {r: len(b) for r, b in bars.items()}
        daily = sorted(bars["1D"], key=lambda b: b[0])
        if len(daily) < 20:
            raise RuntimeError(f"too little daily data ({len(daily)} bars)")
        # daily rows: TradingView stamps a daily bar at its session start, so the date is taken in the index's own time zone
        rows = [[dt.datetime.fromtimestamp(b[0], tz).strftime("%Y-%m-%d"), round(b[1], 4), round(b[4], 4)] for b in daily]
        status["daily_first_last_ts"] = [daily[0][0], daily[-1][0]]
        last_daily = rows[-1]
        intra = sorted(bars["5"] or [], key=lambda b: b[0])
        hourly = sorted(bars["60"] or [], key=lambda b: b[0])
        latest = json.loads((DATA / "latest.json").read_text())
        hist = json.loads((DATA / "history.json").read_text())
        old = latest.get("bench") or {}
        if intra:
            last_ts, last_px = int(intra[-1][0]), float(intra[-1][4])
        else:
            last_ts, last_px = int(daily[-1][0]), float(daily[-1][4])
        today = last_daily[0]
        prev_rows = [r for r in rows if r[0] < today]
        prev = prev_rows[-1][2] if prev_rows else None
        anchor = last_ts
        # same form as the Yahoo series: [bar start, close]; 30 h back from the last bar, hourly for 8 days
        i_pts = [[int(b[0]), round(float(b[4]), 4)] for b in intra if b[0] >= anchor - 30 * 3600]
        w_pts = [[int(b[0]), round(float(b[4]), 4)] for b in hourly if b[0] >= anchor - 8 * 86400]
        latest["bench_proxy"] = old if old.get("sym") != SYMBOL else latest.get("bench_proxy")  # keep the ACWI numbers for reference
        latest["bench"] = {"p": round(last_px, 4), "d": today, "t": dt.datetime.fromtimestamp(last_ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                           "i": i_pts or old.get("i", []), "w": w_pts or old.get("w", []), "prev": prev,
                           "sym": SYMBOL, "name": NAME, "src": "tradingview"}
        hist["bench"] = {"sym": SYMBOL, "rows": [r for r in rows if r[0] < today]}
        (DATA / "latest.json").write_text(json.dumps(latest, separators=(",", ":")))
        (DATA / "history.json").write_text(json.dumps(hist, separators=(",", ":")))
        status.update(ok=True, last=last_px, date=today, prev=prev)
        print(f"WLS from TradingView: {last_px} ({today}), prev close {prev}, bars {status['bars']}")
    except Exception as e:
        status.update(ok=False, error=f"{type(e).__name__}: {e}"[:300])
        print(f"WLS from TradingView failed, keeping ACWI: {status['error']}")
    (DATA / "wls_tv.json").write_text(json.dumps(status, separators=(",", ":")))


if __name__ == "__main__":
    main()
