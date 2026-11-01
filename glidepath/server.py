#!/usr/bin/env python3
"""Glidepath server: serves the app, proxies live stock quotes and stores your data.

Usage:  python glidepath/server.py [--port 8000] [--host 127.0.0.1]

Hosting: set GLIDEPATH_PASSWORD (required online), GLIDEPATH_SECRET and
DATABASE_URL (see storage.py). PORT is read from the environment if set.

Quotes come from Yahoo Finance's public chart endpoint. It is unofficial and
undocumented, so it can change or rate-limit without notice. Uses only the
Python standard library.
"""
import argparse
import html
import json
import os
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
MAX_SYMBOLS = 150
MODEL_SIZE, LOOKBACK = "small", 120  # set from backtest results; see forecast_backtest.json
SYMBOL_RE = re.compile(r"^[A-Z0-9.\-^=]{1,15}$")

_cache = {}
_lock = threading.Lock()

import forecast as kronos_forecast  # noqa: E402
import sp500  # noqa: E402
import yahoo  # noqa: E402  (stdlib-only at import; heavy deps load on first forecast)
import auth  # noqa: E402
import storage  # noqa: E402

STORE = None  # set in main()
STATIC = {"/": "index.html", "/index.html": "index.html", "/sp500.js": "sp500.js"}
MAX_BODY = 2 * 1024 * 1024
LOGIN_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Sign in · Glidepath</title>
<style>
:root{--bg:#F6F5F2;--surface:#fff;--ink:#1E2027;--muted:#6C717D;--line:#EAE7E1;--btn:#D9581F;--down:#DC3F45;--accent-soft:#FCEEE6}
@media (prefers-color-scheme:dark){:root{--bg:#0D1220;--surface:#161C2C;--ink:#EDEFF5;--muted:#959CAD;--line:#252D40;--btn:#E8622C;--down:#FF6D72;--accent-soft:#33221F;color-scheme:dark}}
body{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;padding:16px}
form{background:var(--surface);border:1px solid var(--line);border-radius:18px;padding:28px;width:min(360px,100%);display:grid;gap:14px;box-shadow:0 4px 14px rgba(0,0,0,.05)}
h1{margin:0;font-size:22px}p{margin:0;color:var(--muted);font-size:14px}
input{font:inherit;color:var(--ink);background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:10px 12px}
input:focus{outline:none;border-color:var(--btn);box-shadow:0 0 0 3px var(--accent-soft)}
button{font:inherit;font-weight:600;border:0;border-radius:10px;padding:10px;background:var(--btn);color:#fff;cursor:pointer}
.err{color:var(--down);font-size:14px}
</style></head><body><form method="post" action="login">
<h1>Glidepath</h1><p>Enter your password to open your portfolio.</p>
<input type="password" name="password" autocomplete="current-password" aria-label="Password" required autofocus>
{error}<button type="submit">Sign in</button></form></body></html>"""


FORECAST_OK = kronos_forecast.dependencies_available()
_forecaster = None
_fc_cache = {}  # (symbol, as-of date) -> result
_fc_lock = threading.Lock()
BACKTEST_FILE = ROOT / "forecast_backtest.json"


def get_forecast(sym, size):
    global _forecaster
    df = kronos_forecast.fetch_daily(sym)
    key = (sym, str(df["timestamps"].iloc[-1].date()))
    with _fc_lock:
        if key in _fc_cache:
            return _fc_cache[key]
        if _forecaster is None:
            _forecaster = kronos_forecast.Forecaster(size)
    future, closes = _forecaster.paths(df, horizon=20, samples=16, lookback=LOOKBACK)
    out = kronos_forecast.summarize(sym, df, future, closes)
    out["model"] = f"Kronos-{size}, {LOOKBACK}-day lookback"
    with _fc_lock:
        _fc_cache[key] = out
    return out


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


def get_quotes(syms):
    """Batch quotes through Yahoo's quote endpoint; per-symbol chart lookups for anything it misses."""
    now = time.time()
    with _lock:
        out = {s: _cache[s][1] for s in syms if s in _cache and now - _cache[s][0] < CACHE_TTL}
    todo = [s for s in syms if s not in out]
    if todo:
        try:
            got = yahoo.quotes(todo)
        except Exception:
            got = {}
        with _lock:
            for s, q in got.items():
                _cache[s] = (now, q)
        out.update(got)
        missing = [s for s in todo if s not in got]
        if missing:
            with ThreadPoolExecutor(max_workers=6) as ex:
                out.update(zip(missing, ex.map(fetch_quote, missing)))
    return out


def refresh_sp500_if_stale():
    path = ROOT / "sp500.js"
    if path.exists() and time.time() - path.stat().st_mtime < 86400:
        return
    try:
        sp500.build(verbose=False)
        print("Refreshed S&P 500 fundamentals")
    except Exception as e:
        print("S&P 500 refresh skipped:", e)


def sp500_refresher():
    while True:
        refresh_sp500_if_stale()
        time.sleep(6 * 3600)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(ROOT), **kw)

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("X-Frame-Options", "DENY")
        super().end_headers()

    def do_HEAD(self):  # the base class would serve any file here; refuse instead
        self.send_response(405)
        self.send_header("Allow", "GET, POST, PUT")
        self.send_header("Content-Length", "0")
        self.end_headers()

    # --- helpers -------------------------------------------------------
    def _secure(self):
        return self.headers.get("X-Forwarded-Proto", "").lower() == "https"

    def _client(self):
        fwd = self.headers.get("X-Forwarded-For", "")
        return fwd.split(",")[0].strip() or self.client_address[0]

    def _authed(self):
        return auth.valid_session(self.headers.get("Cookie"))

    def _redirect(self, where, cookie=None):
        self.send_response(303)
        self.send_header("Location", where)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _html(self, code, body):
        raw = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            raise ValueError("too large")
        return self.rfile.read(n) if n else b""

    def _login_page(self, code=200, error=""):
        err = f'<p class="err" role="alert">{html.escape(error)}</p>' if error else ""
        return self._html(code, LOGIN_PAGE.replace("{error}", err))

    # --- sign in / out and stored state ---------------------------------
    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        if u.path == "/login":
            if not auth.enabled:
                return self._redirect("./")
            client = self._client()
            if auth.locked_out(client):
                return self._login_page(429, "Too many wrong attempts. Wait 15 minutes and try again.")
            try:
                form = urllib.parse.parse_qs(self._body().decode("utf-8", "replace"))
            except ValueError:
                return self._login_page(413, "Request too large.")
            if auth.check_password(client, form.get("password", [""])[0]):
                return self._redirect("./", auth.make_cookie(self._secure()))
            return self._login_page(401, "That password isn't right.")
        if u.path == "/logout":
            return self._redirect("login", auth.clear_cookie(self._secure()))
        return self._json(404, {"error": "Not found"})

    def do_PUT(self):
        u = urllib.parse.urlparse(self.path)
        if u.path != "/api/state":
            return self._json(404, {"error": "Not found"})
        if not self._authed():
            return self._json(401, {"error": "Sign in first."})
        if "application/json" not in self.headers.get("Content-Type", ""):
            return self._json(415, {"error": "Send JSON."})
        try:
            doc = json.loads(self._body())
            state, base = doc["state"], int(doc.get("base") or 0)
            if not isinstance(state, dict):
                raise ValueError
        except (ValueError, KeyError, TypeError):
            return self._json(400, {"error": "Send {state, base}."})
        try:
            at = STORE.put(state, base)
        except storage.Conflict as c:
            return self._json(409, {"state": c.state, "updatedAt": c.updated_at})
        except Exception as e:
            print("Save failed:", e)
            return self._json(503, {"error": "Couldn't save right now."})
        return self._json(200, {"updatedAt": at})

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
        if u.path == "/healthz":
            return self._json(200, {"ok": True})
        if u.path == "/login":
            return self._redirect("./") if (auth.enabled and self._authed()) or not auth.enabled else self._login_page()
        if not self._authed():
            if u.path.startswith("/api/"):
                return self._json(401, {"error": "Sign in first."})
            return self._redirect("login")
        if u.path == "/api/health":
            return self._json(200, {"ok": True, "source": "Yahoo Finance (unofficial)", "forecast": FORECAST_OK,
                                    "sync": STORE is not None, "store": STORE.kind if STORE else None, "auth": auth.enabled})
        if u.path == "/api/state":
            try:
                state, at = STORE.get()
            except Exception as e:
                print("Load failed:", e)
                return self._json(503, {"error": "Couldn't load your data right now."})
            return self._json(200, {"state": state, "updatedAt": at})
        if u.path == "/api/forecast":
            sym = urllib.parse.parse_qs(u.query).get("symbol", [""])[0].strip().upper()
            if not SYMBOL_RE.match(sym):
                return self._json(400, {"error": "Pass one valid symbol."})
            if not FORECAST_OK:
                return self._json(503, {"error": "Forecasts need the Kronos dependencies: pip install -r requirements.txt"})
            try:
                out = get_forecast(sym, MODEL_SIZE)
            except urllib.error.HTTPError as e:
                return self._json(404 if e.code == 404 else 502, {"error": "Unknown symbol" if e.code == 404 else "Price history unavailable"})
            except (ValueError, urllib.error.URLError) as e:
                return self._json(422, {"error": str(getattr(e, "reason", e))[:160]})
            try:
                out = {**out, "trackRecord": json.loads(BACKTEST_FILE.read_text()).get("chosen")}
            except (OSError, ValueError):
                pass
            return self._json(200, out)
        if u.path == "/api/quotes":
            q = urllib.parse.parse_qs(u.query).get("symbols", [""])[0]
            syms = list(dict.fromkeys(s.strip().upper() for s in q.split(",") if s.strip()))
            bad = [s for s in syms if not SYMBOL_RE.match(s)]
            if not syms or bad or len(syms) > MAX_SYMBOLS:
                return self._json(400, {"error": f"Pass 1-{MAX_SYMBOLS} valid symbols, comma-separated."})
            quotes = get_quotes(syms)
            return self._json(200, {"quotes": quotes, "asOf": int(time.time())})
        if u.path not in STATIC:  # serve only the app itself, never source or data files
            return self._json(404, {"error": "Not found"})
        self.path = "/" + STATIC[u.path]
        return super().do_GET()


def main():
    ap = argparse.ArgumentParser(description="Run Glidepath with live quotes.")
    ap.add_argument("--host", default="0.0.0.0" if os.environ.get("PORT") else "127.0.0.1")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT") or 8000))
    ap.add_argument("--model", choices=["mini", "small", "base"], default=None, help="Kronos model size for forecasts")
    ap.add_argument("--lookback", type=int, default=None, help="Trading days of history fed to Kronos")
    a = ap.parse_args()
    global MODEL_SIZE, LOOKBACK
    MODEL_SIZE = a.model or MODEL_SIZE
    LOOKBACK = a.lookback or LOOKBACK
    print("Forecasts:", f"Kronos-{MODEL_SIZE}, {LOOKBACK}-day lookback" if FORECAST_OK else "off (pip install -r requirements.txt to enable)")
    global STORE
    STORE = storage.make_store(ROOT)
    print("Data stored in:", STORE.kind)
    if a.host not in ("127.0.0.1", "localhost") and not auth.enabled:
        print("WARNING: reachable from other devices with no password. Set GLIDEPATH_PASSWORD before putting this online.")
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    threading.Thread(target=sp500_refresher, daemon=True).start()
    print(f"Glidepath running at http://{a.host}:{a.port}  (Ctrl+C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
