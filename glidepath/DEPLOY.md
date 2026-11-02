# Put Glidepath online

## Public (anyone can use it): what `render.yaml` sets up
About 10 minutes, one free account. No sign-in for visitors, and nothing about their portfolios
is stored on the server: each visitor's data stays in their own browser.

1. Sign up at https://render.com with your GitHub account and allow it to see the `kronos` repository.
2. Choose **New → Blueprint**, pick the `kronos` repository and the **`glidepath`** branch.
3. Choose **Apply**. The first build takes a few minutes. Your public address appears at the top,
   like `https://glidepath-xxxx.onrender.com`. Share that link.

### What visitors should know (shown on every page)
- Not investment, tax or retirement advice; projections are estimates.
- Market data from Yahoo Finance may be delayed, incomplete or unavailable.
- Their portfolio lives in their browser only. Clearing browser data erases it; **Back up / restore**
  copies it out, and is also how they move it to another device.

### Risks you have accepted
- **Yahoo Finance terms.** Glidepath uses Yahoo's unofficial endpoints. Showing that data in a public
  app is likely against Yahoo's terms. If that matters (for example, the site grows or makes money),
  switch to a licensed data provider; only `yahoo.py` and the quote code in `server.py` need to change.
- **One shared connection to Yahoo.** Every visitor's prices come through this server. Quotes are cached
  for 60 seconds and API calls are rate-limited (90 a minute per visitor, 1,500 a minute overall), so
  Yahoo sees roughly the same traffic whether 1 or 1,000 people are using it. If Yahoo still blocks the
  server, live prices stop for everyone; the app keeps working with typed-in prices and the last daily
  S&P 500 snapshot.
- **Free plan.** Sleeps after about 15 idle minutes (the next visit takes up to a minute) and has 512 MB
  of memory. Fine for light use; upgrade the plan if it gets real traffic.
- **Forecasts are off** in public mode.

### Watch it
Render → your service → **Logs** shows errors, and **Metrics** shows traffic. If you see many
"429" responses, real visitors are hitting the rate limit and the limits in `server.py` can be raised.

## Private (just you, synced across your devices)
Instead of `GLIDEPATH_PUBLIC`, set these in Render → **Environment**:
- `GLIDEPATH_PASSWORD`: your password.
- `GLIDEPATH_SECRET`: any long random text (signs your login cookie).
- `DATABASE_URL`: a free Postgres connection string, e.g. from https://neon.tech, so data survives restarts.
