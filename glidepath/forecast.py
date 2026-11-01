"""Kronos price forecasts for Glidepath.

Wraps this repository's Kronos model (model/kronos.py). Instead of the stock
`predict()`, which averages its samples into a single line, it draws several
independent sample paths so the app can show a range, not just a point.

Requires the repo's model dependencies:  pip install -r requirements.txt
"""
import json
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
UA = "Mozilla/5.0 (Glidepath personal portfolio tracker)"
MODELS = {
    "mini": ("NeoQuasar/Kronos-mini", "NeoQuasar/Kronos-Tokenizer-2k", 2048),
    "small": ("NeoQuasar/Kronos-small", "NeoQuasar/Kronos-Tokenizer-base", 512),
    "base": ("NeoQuasar/Kronos-base", "NeoQuasar/Kronos-Tokenizer-base", 512),
}


def dependencies_available():
    try:
        import importlib.util
        return all(importlib.util.find_spec(m) for m in ("torch", "pandas", "numpy", "einops", "huggingface_hub", "safetensors"))
    except Exception:
        return False


def yahoo_symbol(sym):
    import re
    return sym.replace(".", "-") if re.fullmatch(r"[A-Z]+\.[A-Z]", sym) else sym


def fetch_daily(sym, range_="2y"):
    """Daily OHLCV bars from Yahoo Finance's public chart endpoint, oldest first."""
    import pandas as pd
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(yahoo_symbol(sym))}?range={range_}&interval=1d"
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=10) as r:
        data = json.load(r)
    res = (data.get("chart") or {}).get("result") or []
    if not res or not res[0].get("timestamp"):
        raise ValueError("No price history for this symbol")
    q = res[0]["indicators"]["quote"][0]
    df = pd.DataFrame({
        "timestamps": pd.to_datetime(res[0]["timestamp"], unit="s").normalize(),
        "open": q["open"], "high": q["high"], "low": q["low"], "close": q["close"], "volume": q["volume"],
    }).dropna()
    df = df[df["close"] > 0].drop_duplicates("timestamps", keep="last").reset_index(drop=True)
    return df


class Forecaster:
    def __init__(self, size="small", device=None):
        self.size = size
        self.device = device
        self._predictor = None
        self._lock = threading.Lock()  # one inference at a time: CPU-bound and memory-heavy

    def _load(self):
        if self._predictor is None:
            if str(REPO) not in sys.path:
                sys.path.insert(0, str(REPO))
            from model import Kronos, KronosTokenizer, KronosPredictor
            model_id, tok_id, ctx = MODELS[self.size]
            tok = KronosTokenizer.from_pretrained(tok_id)
            mdl = Kronos.from_pretrained(model_id)
            tok.eval(); mdl.eval()
            self._predictor = KronosPredictor(mdl, tok, device=self.device, max_context=ctx)
        return self._predictor

    def paths(self, df, horizon=20, samples=16, lookback=400, seed=0):
        """Return (future_dates, closes[samples, horizon]) for the bars in df."""
        import numpy as np
        import pandas as pd
        import torch
        hist = df.tail(lookback).reset_index(drop=True)
        if len(hist) < 60:
            raise ValueError("Not enough price history (need at least 60 trading days)")
        last = hist["timestamps"].iloc[-1]
        future = pd.Series(pd.bdate_range(last + pd.Timedelta(days=1), periods=horizon))
        x = hist[["open", "high", "low", "close", "volume"]].astype(float)
        with self._lock:
            pred = self._load()
            torch.manual_seed(seed)
            out = pred.predict_batch(
                [x] * samples, [hist["timestamps"]] * samples, [future] * samples,
                pred_len=horizon, T=1.0, top_p=0.9, sample_count=1, verbose=False,
            )
        closes = np.stack([o["close"].to_numpy() for o in out])
        return future, closes


def summarize(sym, df, future, closes, history_days=120):
    import numpy as np
    last = float(df["close"].iloc[-1])
    final = closes[:, -1]
    pctl = lambda q: np.percentile(closes, q, axis=0).round(4).tolist()
    tail = df.tail(history_days)
    return {
        "symbol": sym,
        "asOf": str(df["timestamps"].iloc[-1].date()),
        "last": last,
        "history": {"dates": [str(d.date()) for d in tail["timestamps"]], "close": tail["close"].round(4).tolist()},
        "forecast": {"dates": [str(d.date()) for d in future], "p10": pctl(10), "p50": pctl(50), "p90": pctl(90)},
        "summary": {
            "horizonDays": int(closes.shape[1]),
            "samples": int(closes.shape[0]),
            "medianReturn": float(np.median(final) / last - 1),
            "p10Return": float(np.percentile(final, 10) / last - 1),
            "p90Return": float(np.percentile(final, 90) / last - 1),
            "probUp": float((final > last).mean()),
        },
        "generatedAt": int(time.time()),
    }
