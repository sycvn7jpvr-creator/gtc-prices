"""Wrap the tracker page (site/page.html) into a standalone index.html for GitHub Pages."""
from pathlib import Path

root = Path(__file__).resolve().parent.parent
page = (root / "site" / "page.html").read_text()
head = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
        '<style>:root{color-scheme:light;padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}'
        'body{margin:0;font:14px system-ui,-apple-system,sans-serif}img{max-width:100%}[hidden]{display:none!important}</style>'
        '</head><body>')
(root / "index.html").write_text(head + page + "</body></html>")
print("index.html written")
