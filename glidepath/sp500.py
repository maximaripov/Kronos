#!/usr/bin/env python3
"""Build glidepath/sp500.js: every S&P 500 constituent with screener fundamentals.

Constituents come from the datasets/s-and-p-500-companies list (maintained from
Wikipedia); fundamentals from Yahoo Finance. The output is a plain script
(window.GLIDEPATH_SP500 = {...}) so the app can load it even when opened
straight from disk. The Glidepath server reruns this daily in the background.

Usage:  python glidepath/sp500.py
"""
import csv
import io
import json
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import yahoo

CONSTITUENTS = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"
OUT = Path(__file__).resolve().parent / "sp500.js"


def constituents():
    req = urllib.request.Request(CONSTITUENTS, headers={"User-Agent": yahoo.UA})
    text = urllib.request.urlopen(req, timeout=20).read().decode()
    tidy = lambda n: "The " + n[:-6] if n.endswith(" (The)") else n
    rows = [(r["Symbol"].strip().upper(), tidy(r["Security"].strip()), r["GICS Sector"].strip()) for r in csv.DictReader(io.StringIO(text))]
    if len(rows) < 480:
        raise RuntimeError(f"Constituent list looks incomplete ({len(rows)} rows)")
    return rows


def _one(row):
    sym, name, sector = row
    for attempt in range(3):
        try:
            f = yahoo.fundamentals(sym)
            break
        except Exception:
            if attempt == 2:
                f = {}
            time.sleep(1.5 * (attempt + 1))
    r = lambda v, k=1, d=2: None if v is None else round(v * k, d)
    # columns: ticker, name, sector, mcap $B, P/E, forward P/E, yield %, revenue growth % (YoY), beta, net margin %
    return [sym, name, sector, r(f.get("mcap"), 1e-9, 1), r(f.get("pe"), 1, 1), r(f.get("fpe"), 1, 1),
            r(f.get("yield"), 100), r(f.get("growth"), 100, 1), r(f.get("beta")), r(f.get("margin"), 100, 1)]


def build(verbose=True):
    rows = constituents()
    with ThreadPoolExecutor(max_workers=4) as ex:
        out = list(ex.map(_one, rows))
    missing = sum(1 for r in out if r[3] is None)
    if missing > len(out) * 0.2:
        raise RuntimeError(f"Fundamentals missing for {missing} of {len(out)} stocks; keeping the previous file")
    payload = {
        "asOf": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "source": "S&P 500 list: datasets/s-and-p-500-companies (from Wikipedia). Figures: Yahoo Finance.",
        "columns": ["ticker", "name", "sector", "mcap", "pe", "fpe", "yield", "growth", "beta", "margin"],
        "rows": out,
    }
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text("window.GLIDEPATH_SP500 = " + json.dumps(payload, separators=(",", ":")) + ";\n")
    tmp.replace(OUT)
    if verbose:
        print(f"Wrote {OUT.name}: {len(out)} stocks, {missing} without fundamentals")
    return payload


if __name__ == "__main__":
    try:
        build()
    except Exception as e:
        sys.exit(f"S&P 500 build failed: {e}")
