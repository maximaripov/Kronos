#!/usr/bin/env python3
"""Glidepath local server: serves the app and proxies live stock quotes.

Usage:  python glidepath/server.py [--port 8000] [--host 127.0.0.1]

Quotes come from Yahoo Finance's public chart endpoint. It is unofficial and
undocumented, so it can change or rate-limit without notice. Uses only the
Python standard library.
"""
import argparse
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{}?range=5d&interval=1d"
UA = "Mozilla/5.0 (Glidepath personal portfolio tracker)"
CACHE_TTL = 60  # seconds
MAX_SYMBOLS = 100
SYMBOL_RE = re.compile(r"^[A-Z0-9.\-^=]{1,15}$")

_cache = {}
_lock = threading.Lock()


def yahoo_symbol(sym):
    # Class shares: BRK.B -> BRK-B. Leave index (^GSPC) and FX (EURUSD=X) symbols alone.
    return sym.replace(".", "-") if re.fullmatch(r"[A-Z]+\.[A-Z]", sym) else sym


def fetch_quote(sym):
    now = time.time()
    with _lock:
        hit = _cache.get(sym)
        if hit and now - hit[0] < CACHE_TTL:
            return hit[1]
    url = YAHOO.format(urllib.parse.quote(yahoo_symbol(sym)))
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.load(r)
        res = (data.get("chart") or {}).get("result") or []
        if not res:
            err = ((data.get("chart") or {}).get("error") or {}).get("description")
            raise ValueError(err or "No data")
        meta = res[0]["meta"]
        price = meta.get("regularMarketPrice")
        if price is None:
            raise ValueError("No price")
        closes = [c for c in ((res[0].get("indicators") or {}).get("quote") or [{}])[0].get("close") or [] if c is not None]
        # previous session's close: second-to-last daily close if today's bar exists, else chartPreviousClose
        prev = closes[-2] if len(closes) >= 2 else meta.get("chartPreviousClose")
        out = {
            "price": price,
            "prevClose": prev,
            "currency": meta.get("currency"),
            "name": meta.get("longName") or meta.get("shortName"),
            "time": meta.get("regularMarketTime"),
        }
    except (urllib.error.URLError, ValueError, KeyError, IndexError, TimeoutError) as e:
        reason = getattr(e, "reason", None) or str(e)
        if isinstance(e, urllib.error.HTTPError) and e.code == 404:
            reason = "Unknown symbol"
        return {"error": str(reason)[:120]}
    with _lock:
        _cache[sym] = (now, out)
    return out


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(ROOT), **kw)

    def log_message(self, fmt, *args):
        if "/api/" in (args[0] if args else ""):
            return
        super().log_message(fmt, *args)

    def _json(self, code, body):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path == "/api/health":
            return self._json(200, {"ok": True, "source": "Yahoo Finance (unofficial)"})
        if u.path == "/api/quotes":
            q = urllib.parse.parse_qs(u.query).get("symbols", [""])[0]
            syms = list(dict.fromkeys(s.strip().upper() for s in q.split(",") if s.strip()))
            bad = [s for s in syms if not SYMBOL_RE.match(s)]
            if not syms or bad or len(syms) > MAX_SYMBOLS:
                return self._json(400, {"error": f"Pass 1-{MAX_SYMBOLS} valid symbols, comma-separated."})
            with ThreadPoolExecutor(max_workers=8) as ex:
                quotes = dict(zip(syms, ex.map(fetch_quote, syms)))
            return self._json(200, {"quotes": quotes, "asOf": int(time.time())})
        if u.path == "/":
            self.path = "/index.html"
        return super().do_GET()


def main():
    ap = argparse.ArgumentParser(description="Run Glidepath with live quotes.")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    a = ap.parse_args()
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print(f"Glidepath running at http://{a.host}:{a.port}  (Ctrl+C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
