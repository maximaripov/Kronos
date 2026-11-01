# Glidepath

A personal finance dashboard in one static file: portfolio tracking, a stock screener, and a Monte Carlo retirement planner.

Open `index.html` in a browser, or serve the folder: `python -m http.server -d glidepath 8000`.

- **Portfolio**: holdings, prices you enter, unrealized gain, allocation, and value-vs-cost-basis history from snapshots you record.
- **Screener**: filter by market cap, P/E, yield, revenue growth, beta and margin. Ships with approximate sample figures; load your own CSV for real data.
- **Retirement**: 2,000 simulated market paths in today's dollars, with a glide path that de-risks returns and volatility before retirement. Reports success rate and the spending level sustainable at 85% confidence.

Data stays in the browser (`localStorage`). Use **Back up / restore** to copy it out. There is no live market data feed yet.
