#!/usr/bin/env python3
"""Measure how Glidepath's Kronos forecasts would have done.

Hindcasts 20-trading-day forecasts from past dates for a fixed set of stocks and
ETFs, compares them with what actually happened and with a "no change" guess,
and writes glidepath/forecast_backtest.json. The app shows the chosen
configuration's numbers next to every forecast.

Usage:  python glidepath/backtest.py [--configs small:120,mini:120] [--origins 12]
Takes several minutes on a CPU per configuration.
"""
import argparse
import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import torch

import forecast as F

TICKERS = ["AAPL", "MSFT", "NVDA", "AMZN", "JPM", "JNJ", "KO", "XOM", "HD", "CAT", "NEE", "VTI", "VXUS", "BND"]
H, SAMPLES, STEP = 20, 8, 20
OUT = Path(__file__).resolve().parent / "forecast_backtest.json"


def run_config(data, size, lookback, origins):
    pred = F.Forecaster(size)._load()
    rows = []
    for o in origins:
        hists = [data[t].iloc[o - lookback:o].reset_index(drop=True) for t in TICKERS]
        fut = pd.Series(pd.bdate_range(hists[0].timestamps.iloc[-1] + pd.Timedelta(days=1), periods=H))
        xs = [h[["open", "high", "low", "close", "volume"]].astype(float) for h in hists]
        torch.manual_seed(o)
        out = pred.predict_batch(
            [x for x in xs for _ in range(SAMPLES)], [h.timestamps for h in hists for _ in range(SAMPLES)],
            [fut] * (len(xs) * SAMPLES), pred_len=H, T=1.0, top_p=0.9, sample_count=1, verbose=False)
        for i, t in enumerate(TICKERS):
            final = np.array([out[i * SAMPLES + j]["close"].iloc[-1] for j in range(SAMPLES)])
            last = hists[i].close.iloc[-1]
            rows.append(dict(origin=o, t=t, med=np.median(final) / last - 1, lo=np.percentile(final, 10) / last - 1,
                             hi=np.percentile(final, 90) / last - 1, real=data[t].close.iloc[o + H - 1] / last - 1))
    r = pd.DataFrame(rows)
    ic = r.groupby("origin").apply(lambda g: g.med.rank().corr(g.real.rank())).dropna()
    return dict(
        model=f"Kronos-{size}", lookback=lookback, n=len(r),
        mae_model=float((r.med - r.real).abs().mean()), mae_nochange=float(r.real.abs().mean()),
        dir_acc=float((np.sign(r.med) == np.sign(r.real)).mean()), base_up=float((r.real > 0).mean()),
        coverage_p10_p90=float(((r.real >= r.lo) & (r.real <= r.hi)).mean()),
        mean_pred=float(r.med.mean()), mean_real=float(r.real.mean()),
        rank_ic_mean=float(ic.mean()), rank_ic_t=float(ic.mean() / (ic.std() / np.sqrt(len(ic)))), ic_periods=int(len(ic)),
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--configs", default="small:120,small:400,mini:120,mini:400")
    ap.add_argument("--origins", type=int, default=12)
    a = ap.parse_args()
    data = {t: F.fetch_daily(t, "5y") for t in TICKERS}
    n = min(len(d) for d in data.values())
    data = {t: d.tail(n).reset_index(drop=True) for t, d in data.items()}
    origins = [n - H - k * STEP for k in range(a.origins)][::-1]
    results = {}
    for cfg in a.configs.split(","):
        size, lb = cfg.split(":")
        res = run_config(data, size, int(lb), origins)
        results[cfg] = res
        print(cfg, json.dumps({k: (round(v, 3) if isinstance(v, float) else v) for k, v in res.items()}), flush=True)
    key = min(results, key=lambda k: results[k]["mae_model"])
    first = data[TICKERS[0]].timestamps.iloc[origins[0] - 1].date()
    last = data[TICKERS[0]].timestamps.iloc[origins[-1] + H - 1].date()
    chosen = dict(results[key], description=(
        f"{results[key]['model']}, {results[key]['lookback']}-day lookback. {results[key]['n']} forecasts: "
        f"{len(TICKERS)} stocks and ETFs from {len(origins)} start dates between {first} and {last}, "
        f"each {H} trading days ahead. Run {date.today()}."))
    OUT.write_text(json.dumps({"tickers": TICKERS, "horizon": H, "samples": SAMPLES, "configs": results, "chosenKey": key, "chosen": chosen}, indent=1))
    print("Wrote", OUT, "| chosen:", key)


if __name__ == "__main__":
    main()
