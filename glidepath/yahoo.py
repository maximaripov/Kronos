"""Minimal Yahoo Finance client for Glidepath (standard library only).

Yahoo's quote and fundamentals endpoints need a session cookie plus a "crumb"
token. These endpoints are unofficial and undocumented: they can change or
rate-limit without notice.
"""
import http.cookiejar
import json
import re
import threading
import urllib.error
import urllib.parse
import urllib.request

UA = "Mozilla/5.0"  # a full browser string without browser headers gets rate-limited
_lock = threading.Lock()
_opener = None
_crumb = None


def yahoo_symbol(sym):
    """Class shares use a dash on Yahoo: BRK.B -> BRK-B. Index (^GSPC) and FX (EURUSD=X) symbols pass through."""
    return sym.replace(".", "-") if re.fullmatch(r"[A-Z]+\.[A-Z]", sym) else sym


def _session(refresh=False):
    global _opener, _crumb
    with _lock:
        if _opener is None or refresh:
            jar = http.cookiejar.CookieJar()
            opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
            opener.addheaders = [("User-Agent", UA)]
            try:
                opener.open("https://fc.yahoo.com", timeout=10)
            except urllib.error.HTTPError:
                pass  # answers 404 but still sets the session cookie
            crumb = opener.open("https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=10).read().decode().strip()
            if not crumb or "<" in crumb:
                raise RuntimeError("Yahoo did not issue a session token")
            _opener, _crumb = opener, crumb
        return _opener, _crumb


def get_json(url, params=None, auth=True):
    """GET JSON; with auth, add the crumb and retry once with a fresh session on 401."""
    for attempt in (0, 1):
        opener, crumb = _session(refresh=attempt == 1) if auth else (urllib.request.build_opener(), None)
        q = dict(params or {})
        if auth:
            q["crumb"] = crumb
        full = url + ("?" + urllib.parse.urlencode(q) if q else "")
        req = urllib.request.Request(full, headers={"User-Agent": UA})
        try:
            with opener.open(req, timeout=15) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 401 and auth and attempt == 0:
                continue
            raise


def quotes(symbols):
    """Batch quotes: {symbol: {price, prevClose, name, currency, time}} for the symbols Yahoo knows."""
    out = {}
    for i in range(0, len(symbols), 150):
        chunk = symbols[i:i + 150]
        ymap = {yahoo_symbol(s): s for s in chunk}
        data = get_json("https://query1.finance.yahoo.com/v7/finance/quote", {"symbols": ",".join(ymap)})
        for q in (data.get("quoteResponse") or {}).get("result") or []:
            sym = ymap.get(q.get("symbol"))
            if sym and q.get("regularMarketPrice") is not None:
                out[sym] = {
                    "price": q["regularMarketPrice"],
                    "prevClose": q.get("regularMarketPreviousClose"),
                    "name": q.get("longName") or q.get("shortName"),
                    "currency": q.get("currency"),
                    "time": q.get("regularMarketTime"),
                }
    return out


def chart_quote(sym):
    """Fallback single-symbol quote from the chart endpoint (needs no session)."""
    data = get_json(f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(yahoo_symbol(sym))}",
                    {"range": "5d", "interval": "1d"}, auth=False)
    res = (data.get("chart") or {}).get("result") or []
    if not res or res[0]["meta"].get("regularMarketPrice") is None:
        raise ValueError("No data")
    meta = res[0]["meta"]
    closes = [c for c in ((res[0].get("indicators") or {}).get("quote") or [{}])[0].get("close") or [] if c is not None]
    return {
        "price": meta["regularMarketPrice"],
        "prevClose": closes[-2] if len(closes) >= 2 else meta.get("chartPreviousClose"),
        "name": meta.get("longName") or meta.get("shortName"),
        "currency": meta.get("currency"),
        "time": meta.get("regularMarketTime"),
    }


def fundamentals(sym):
    """Market cap, valuation, income, growth, risk and margin figures for one stock."""
    mods = "price,summaryDetail,defaultKeyStatistics,financialData"
    data = get_json(f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{urllib.parse.quote(yahoo_symbol(sym))}", {"modules": mods})
    r = ((data.get("quoteSummary") or {}).get("result") or [None])[0]
    if not r:
        raise ValueError("No fundamentals")
    g = lambda mod, k: ((r.get(mod) or {}).get(k) or {}).get("raw")
    return {
        "mcap": g("price", "marketCap"),
        "pe": g("summaryDetail", "trailingPE"),
        "fpe": g("summaryDetail", "forwardPE"),
        "yield": g("summaryDetail", "dividendYield"),
        "growth": g("financialData", "revenueGrowth"),
        "beta": g("summaryDetail", "beta"),
        "margin": g("financialData", "profitMargins"),
        "price": g("price", "regularMarketPrice"),
    }
