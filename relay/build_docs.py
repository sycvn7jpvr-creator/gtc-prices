"""Turn the feed files into the tracker's two database documents.

usage: python3 build_docs.py <repo_dir> <out_dir> [<trades_dump_dir>]
Writes <out_dir>/latest.json and <out_dir>/history.json (market/latest, market/history).
If a trades dump is given, appends tickers the team trades but the feed lacks to
tickers.txt and prints NEW_TICKERS so the caller can commit them.
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
if len(sys.argv) > 3:
    tf = repo / "tickers.txt"
    have = {l.strip() for l in tf.read_text().splitlines() if l.strip() and not l.startswith("#")}
    traded = set()
    for f in Path(sys.argv[3]).glob("*.json"):
        t = json.loads(f.read_text()).get("ticker", "")
        if t:
            traded.add(" ".join(t.upper().replace(" EQUITY", "").split()))
    new = sorted(traded - have)
    if new:
        with tf.open("a") as fh:
            fh.write("".join(t + "\n" for t in new))

print(f"feed updated {latest.get('updated')} | {len(latest.get('px', {}))} prices, {len(latest.get('fx', {}))} FX"
      f" | errors: {len(latest.get('errors', []))}")
print("NEW_TICKERS: " + (", ".join(new) if new else "none"))
