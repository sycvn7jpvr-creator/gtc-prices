"""Turn the feed files into the tracker's two database documents.

usage: python3 build_docs.py <repo_dir> <out_dir> [<dump_dir>]
Writes <out_dir>/latest.json and <out_dir>/history.json (market/latest, market/history).
<dump_dir> holds the page's own data as saved by ArtifactData list (trades/, settings/, prices/).
With it, the script also
  - appends tickers the team trades but the feed lacks to tickers.txt,
  - writes data/trades.json, data/settings.json, data/prices.json for the public site,
and prints REPO_CHANGED: yes when any of those files changed, so the caller commits them.
"""
import json
import sys
from pathlib import Path

repo, out = Path(sys.argv[1]), Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
latest = json.loads((repo / "data" / "latest.json").read_text())
hist = json.loads((repo / "data" / "history.json").read_text())

# keep the history document under the 256 KiB limit by trimming its oldest days
limit = 240_000
while len(json.dumps(hist, separators=(",", ":"))) > limit:
    for k in hist["px"]:
        hist["px"][k] = hist["px"][k][10:]
    for k in hist["fx"]:
        hist["fx"][k] = hist["fx"][k][10:]

(out / "latest.json").write_text(json.dumps(latest, separators=(",", ":")))
(out / "history.json").write_text(json.dumps(hist, separators=(",", ":")))

new = []
changed = False


def write_if_changed(path, obj):
    global changed
    txt = json.dumps(obj, separators=(",", ":"), sort_keys=True)
    if not path.exists() or path.read_text() != txt:
        path.write_text(txt)
        changed = True


if len(sys.argv) > 3:
    dump = Path(sys.argv[3])
    tdir = dump / "trades" if (dump / "trades").is_dir() else dump
    trades = []
    for f in sorted(tdir.glob("*.json")):
        t = json.loads(f.read_text())
        t["id"] = f.stem
        t.pop("by", None)  # keep account ids off the public site
        trades.append(t)
    tf = repo / "tickers.txt"
    have = {l.strip() for l in tf.read_text().splitlines() if l.strip() and not l.startswith("#")}
    traded = {" ".join(t.get("ticker", "").upper().replace(" EQUITY", "").split()) for t in trades} - {""}
    new = sorted(traded - have)
    if new:
        with tf.open("a") as fh:
            fh.write("".join(t + "\n" for t in new))
        changed = True
    write_if_changed(repo / "data" / "trades.json", trades)
    sf = dump / "settings" / "main.json"
    if sf.exists():
        write_if_changed(repo / "data" / "settings.json", json.loads(sf.read_text()))
    prices = [dict(json.loads(f.read_text()), date=f.stem) for f in sorted((dump / "prices").glob("*.json"))] if (dump / "prices").is_dir() else []
    write_if_changed(repo / "data" / "prices.json", prices)

print(f"feed updated {latest.get('updated')} | {len(latest.get('px', {}))} prices, {len(latest.get('fx', {}))} FX"
      f" | errors: {len(latest.get('errors', []))}")
print("NEW_TICKERS: " + (", ".join(new) if new else "none"))
print("REPO_CHANGED: " + ("yes" if changed else "no"))
