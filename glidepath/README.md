# Glidepath

A personal finance dashboard in one static file: portfolio tracking, a stock screener, and a Monte Carlo retirement planner.

Run `python glidepath/server.py` and open http://127.0.0.1:8000. To put it online behind a password with your data synced across devices, follow [DEPLOY.md](DEPLOY.md).

- **Portfolio**: holdings, prices you enter, unrealized gain, allocation, and value-vs-cost-basis history from snapshots you record.
- **Screener**: all ~500 S&P 500 stocks, filtered by market cap, P/E, forward P/E, yield, year-over-year revenue growth, beta and net margin. Fundamentals live in `sp500.js`, built from Yahoo Finance by `python glidepath/sp500.py` (about 25 seconds); the server rebuilds it once a day. You can also load your own CSV.
- **Retirement**: 2,000 simulated market paths in today's dollars, with a glide path that de-risks returns and volatility before retirement. Reports success rate and the spending level sustainable at 85% confidence.

Your data is saved by the server (a local file, or Postgres when `DATABASE_URL` is set) and cached in the browser. Opened straight from disk, it stays in that browser only. Use **Back up / restore** for an extra copy.

## Live prices

```
python glidepath/server.py          # then open http://127.0.0.1:8000
```

The server uses only the Python standard library. It serves the app and proxies quotes from Yahoo Finance's public chart endpoint, caching each symbol for 60 seconds. Once it is running, the portfolio refreshes every 5 minutes while the tab is open, shows each holding's last-session change, and saves one value snapshot per day automatically. The screener gains Price and Day columns.

Holdings with asset class **Cash** are never quoted. If you open `index.html` directly from disk, or view the hosted preview, prices stay manual.

Yahoo's endpoint is unofficial and undocumented. It can change or rate-limit without notice, so treat it as a convenience, not something to build a business on.
