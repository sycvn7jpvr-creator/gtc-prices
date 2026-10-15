"""Pull prices for the GTC-2026 book from Yahoo Finance.

Runs in GitHub Actions every 30 minutes on weekdays. Reads Bloomberg-style
tickers from tickers.txt (e.g. "OERL SE", "9147 JT", "VAC UN"), maps them to
Yahoo symbols, and writes:
  data/latest.json   last price per ticker + FX (changes every run)
  data/history.json  completed daily bars (open, close) since HIST_START + FX closes
"""
import json
import datetime as dt
from pathlib import Path

import yfinance as yf

ROOT = Path(__file__).parent
OUT = ROOT / "data"
HIST_START = (dt.date.today() - dt.timedelta(days=380)).isoformat()

# Bloomberg exchange code -> (Yahoo suffix, currency)
EXCH = {
    "US": ("", "USD"), "UN": ("", "USD"), "UW": ("", "USD"), "UQ": ("", "USD"),
    "UR": ("", "USD"), "UA": ("", "USD"), "UP": ("", "USD"),
    "JT": (".T", "JPY"), "TT": (".TW", "TWD"), "TO": (".TWO", "TWD"),
    "SW": (".SW", "CHF"), "SE": (".SW", "CHF"), "VX": (".SW", "CHF"),
    "GY": (".DE", "EUR"), "GR": (".DE", "EUR"), "GF": (".F", "EUR"),
    "BB": (".BR", "EUR"), "NA": (".AS", "EUR"), "FP": (".PA", "EUR"),
    "IM": (".MI", "EUR"), "SM": (".MC", "EUR"), "ID": (".IR", "EUR"),
    "PL": (".LS", "EUR"), "AV": (".VI", "EUR"), "FH": (".HE", "EUR"),
    "SS": (".ST", "SEK"), "NO": (".OL", "NOK"), "DC": (".CO", "DKK"),
    "LN": (".L", "GBP"), "AU": (".AX", "AUD"), "AT": (".AX", "AUD"),
    "HK": (".HK", "HKD"), "CN": (".TO", "CAD"), "CT": (".TO", "CAD"),
    "KS": (".KS", "KRW"), "SP": (".SI", "SGD"), "TB": (".BK", "THB"),
    "SJ": (".JO", "ZAR"), "IJ": (".JK", "IDR"), "MK": (".KL", "MYR"),
}
# Yahoo quotes these in minor units; convert to major units.
MINOR = {".L": 100.0, ".JO": 100.0}


def to_yahoo(bbg):
    parts = bbg.upper().replace(" EQUITY", "").split()
    root, code = " ".join(parts[:-1]), parts[-1]
    suffix, ccy = EXCH[code]
    root = root.replace("/", "-")
    if suffix == ".HK":
        root = root.zfill(4)
    return root + suffix, ccy, MINOR.get(suffix, 1.0)


def r(x):
    return None if x is None or x != x else float(f"{x:.6g}")


def daily(sym, scale):
    h = yf.Ticker(sym).history(start=HIST_START, interval="1d", auto_adjust=False)
    h = h.dropna(subset=["Close"])
    return [[i.strftime("%Y-%m-%d"), r(o / scale), r(c / scale)] for i, o, c in zip(h.index, h["Open"], h["Close"])]


NOW_TS = dt.datetime.now(dt.timezone.utc).timestamp()


def series(sym, scale, period, interval, rule=None, since_hours=None):
    """Closes as [[unix_ts, price], ...], optionally resampled (e.g. '10min', '4h')."""
    h = yf.Ticker(sym).history(period=period, interval=interval, auto_adjust=False).dropna(subset=["Close"])
    c = h["Close"]
    if rule and not c.empty:
        c = c.resample(rule, origin="epoch", label="left", closed="left").last().dropna()
    if since_hours:
        c = c[[i.timestamp() >= NOW_TS - since_hours * 3600 for i in c.index]]
    return [[int(i.timestamp()), r(v / scale)] for i, v in c.items()], h


def last_bar(sym, scale, with_month=False):
    """Latest price plus chart series: i = 10-min (30 h), w = hourly (8 days), m = 4-hour (35 days)."""
    intra, raw = series(sym, scale, "5d", "5m", "10min", since_hours=30)
    if raw.empty:
        return None
    t = raw.index[-1]
    out = {"p": r(raw["Close"].iloc[-1] / scale), "d": t.strftime("%Y-%m-%d"),
           "t": t.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ"), "i": intra}
    out["w"], _ = series(sym, scale, "8d", "1h")
    if with_month:
        out["m"], _ = series(sym, scale, "3mo", "1h", "4h", since_hours=35 * 24)
    return out


def main():
    tickers = [l.strip() for l in (ROOT / "tickers.txt").read_text().splitlines() if l.strip() and not l.startswith("#")]
    now = dt.datetime.now(dt.timezone.utc)
    today = now.date().isoformat()
    latest = {"updated": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "px": {}, "fx": {}, "errors": []}
    hist = {"from": HIST_START, "px": {}, "fx": {}}
    ccys = set()

    for bbg in tickers:
        try:
            sym, ccy, scale = to_yahoo(bbg)
        except Exception:
            latest["errors"].append(f"{bbg}: unknown exchange code")
            continue
        ccys.add(ccy)
        try:
            rows = daily(sym, scale)
            lb = last_bar(sym, scale, with_month=True)
            if not rows and not lb:
                latest["errors"].append(f"{bbg} ({sym}): no data")
                continue
            if lb is None:
                lb = {"p": rows[-1][2], "d": rows[-1][0], "t": None}
            same_day = next((row for row in rows if row[0] == lb["d"]), None)
            if same_day and same_day[2]:  # daily close includes the closing auction
                lb["p"] = same_day[2]
            prev = next((row[2] for row in reversed(rows) if row[0] < lb["d"]), None)
            today_open = next((row[1] for row in rows if row[0] == lb["d"]), None)
            latest["px"][bbg] = dict(lb, y=sym, ccy=ccy, prev=prev, o=today_open)
            hist["px"][bbg] = [row for row in rows if row[0] < today]  # completed days only
        except Exception as e:  # keep going for the other names
            latest["errors"].append(f"{bbg} ({sym}): {type(e).__name__}: {e}"[:200])

    for ccy in sorted(ccys - {"USD"}):
        sym = f"{ccy}USD=X"
        try:
            rows = daily(sym, 1.0)
            lb = last_bar(sym, 1.0) or {"p": rows[-1][2], "d": rows[-1][0], "t": None}
            latest["fx"][ccy] = {"v": lb["p"], "d": lb["d"], "t": lb["t"], "i": lb.get("i", []), "w": lb.get("w", [])}
            hist["fx"][ccy] = [[d, c] for d, o, c in rows if d < today]
        except Exception as e:
            latest["errors"].append(f"FX {ccy}: {type(e).__name__}: {e}"[:200])

    OUT.mkdir(exist_ok=True)
    (OUT / "latest.json").write_text(json.dumps(latest, separators=(",", ":")))
    new_hist = json.dumps(hist, separators=(",", ":"))
    hp = OUT / "history.json"
    if not hp.exists() or hp.read_text() != new_hist:
        hp.write_text(new_hist)
    print(f"{len(latest['px'])}/{len(tickers)} tickers, {len(latest['fx'])} FX, errors: {latest['errors']}")


if __name__ == "__main__":
    main()
