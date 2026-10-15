"""AI news brief per stock, written by a GitHub Models LLM from the collected headlines.

Reads data/news.json (+ data/latest.json for today's move), writes data/summaries.json:
  {"updated": iso, "items": {bbg: {"text", "n", "hash", "ts", "model"}}, "status": {...}}
A stock is only re-summarised when its headlines changed, and a daily cap keeps us well
inside the free GitHub Models limits. Uses the job's GITHUB_TOKEN (needs `models: read`).
"""
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent
NEWS = ROOT / "data" / "news.json"
LATEST = ROOT / "data" / "latest.json"
OUT = ROOT / "data" / "summaries.json"
ENDPOINT = "https://models.github.ai/inference/chat/completions"
MODEL = "openai/gpt-4.1-mini"
MAX_PER_RUN = 10
MAX_PER_DAY = 120

SYSTEM = (
    "You write short, factual news briefs about listed companies for a student investment team. "
    "Use ONLY the headlines provided; do not add outside facts or numbers that are not in them. "
    "Write 2 to 4 plain English sentences: what happened recently and, if the headlines say so, why the stock moved. "
    "Ignore headlines that are clearly not about the company (for example sports at a stadium named after it, "
    "or generic market lists). If there is nothing substantive, say that recent headlines are mostly routine "
    "price updates. No investment advice, no hype, no bullet points."
)


def load(p, default):
    try:
        return json.loads(p.read_text())
    except Exception:
        return default


ENDPOINTS = [  # (url, model, accept) tried in order; the first that answers is used for the rest of the run
    ("https://models.github.ai/inference/chat/completions", "openai/gpt-4.1-mini", "application/vnd.github+json"),
    ("https://models.github.ai/inference/chat/completions", "openai/gpt-4o-mini", "application/vnd.github+json"),
    ("https://models.github.ai/inference/chat/completions", "openai/gpt-4.1-mini", "application/json"),
]
_working = []


def _post(url, model, accept, prompt, token):
    body = json.dumps({"model": model, "temperature": 0.2, "max_tokens": 220,
                       "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": accept,
        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "gtc-prices-bot"})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            raw, code = r.read(), r.status
    except urllib.error.HTTPError as e:
        raw, code = e.read(), e.code
    try:
        return json.loads(raw)["choices"][0]["message"]["content"].strip()
    except Exception:
        raise RuntimeError(f"{model} [{accept}]: HTTP {code}: {raw[:160]!r}")


def call_model(prompt):
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("GITHUB_TOKEN missing")
    errors = []
    for url, model, accept in (_working or ENDPOINTS):
        try:
            text = _post(url, model, accept, prompt, token)
            _working[:] = [(url, model, accept)]
            return text, model
        except Exception as e:
            errors.append(str(e))
    raise RuntimeError(" || ".join(errors)[:600])


def probe_catalog():
    try:
        req = urllib.request.Request("https://models.github.ai/catalog/models", headers={
            "Authorization": f"Bearer {os.environ.get('GITHUB_TOKEN', '')}", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "gtc-prices-bot"})
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read()
        try:
            ids = [m.get("id") for m in json.loads(raw)][:15]
            return f"catalog ok: {ids}"
        except Exception:
            return f"catalog HTTP {r.status}: {raw[:120]!r}"
    except urllib.error.HTTPError as e:
        return f"catalog HTTP {e.code}: {e.read()[:120]!r}"
    except Exception as e:
        return f"catalog {type(e).__name__}: {e}"[:200]


def main():
    news = load(NEWS, {})
    latest = load(LATEST, {})
    out = load(OUT, {"items": {}, "status": {}})
    out.setdefault("items", {})
    st = out.setdefault("status", {})
    day = time.strftime("%Y-%m-%d", time.gmtime())
    if st.get("day") != day:
        st.update(day=day, calls=0)
    if st.get("error") or not out["items"]:
        st["catalog"] = probe_catalog()
    names = {t["ticker"]: t.get("name", t["ticker"]) for t in load(ROOT / "data" / "trades.json", [])}
    done = 0
    for bbg, items in (news.get("items") or {}).items():
        top = items[:8]
        if not top:
            continue
        h = hashlib.sha1("|".join(n["title"] for n in top).encode()).hexdigest()[:12]
        if out["items"].get(bbg, {}).get("hash") == h:
            continue
        if done >= MAX_PER_RUN or st["calls"] >= MAX_PER_DAY:
            break
        px = (latest.get("px") or {}).get(bbg, {})
        move = ""
        if px.get("p") and px.get("prev"):
            move = f"Latest price {px['p']} {px.get('ccy', '')}, {100 * (px['p'] / px['prev'] - 1):+.1f}% vs previous close ({px.get('d')}).\n"
        lines = "\n".join(f"- {time.strftime('%d %b', time.gmtime(n['ts']))} | {n.get('source', '')} | {n['title']}" for n in top)
        prompt = f"Company: {names.get(bbg, bbg)} (Bloomberg ticker {bbg}).\n{move}Recent headlines, newest first:\n{lines}"
        try:
            text, used = call_model(prompt)
            st["calls"] += 1
            out["items"][bbg] = {"text": text, "n": len(top), "hash": h, "ts": int(time.time()), "model": used}
            st.pop("error", None)
            done += 1
        except Exception as e:
            st["error"] = f"{type(e).__name__}: {e}"[:700]
            print("summary error:", st["error"])
            break
    out["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    OUT.write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False))
    print(f"summaries: {done} new, {st['calls']} calls today", st.get("error", ""))


if __name__ == "__main__":
    main()
