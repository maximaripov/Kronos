# Put Glidepath online (Render free tier + Neon free database)

About 15 minutes, no credit card expected for either free plan (check when you sign up; plans change).
You get a private web address that works on your phone and computer, behind your password,
with the same portfolio on every device.

## 1. Create the database (Neon)
1. Sign up at https://neon.tech (signing in with GitHub is quickest).
2. Create a project. Any name and region close to you.
3. On the project dashboard choose **Connect**, and copy the **connection string**.
   It looks like `postgresql://user:password@ep-something.neon.tech/neondb?sslmode=require`.
   Keep it private: it is the key to your data.

## 2. Create the website (Render)
1. Sign up at https://render.com with your GitHub account, and allow it to see the `kronos` repository.
2. Choose **New → Blueprint**, pick the `kronos` repository and the **`glidepath`** branch.
   Render reads `render.yaml` and sets everything up.
3. It asks for two values:
   - **GLIDEPATH_PASSWORD**: the password you'll type to open Glidepath. Use a long one you don't use anywhere else.
   - **DATABASE_URL**: paste the Neon connection string from step 1.
4. Choose **Apply**. The first build takes a few minutes. Your address appears at the top, like
   `https://glidepath-xxxx.onrender.com`.

## 3. Use it
- Open the address, sign in, and you stay signed in for 30 days on that device.
- On your phone: Share → **Add to Home Screen** to open it like an app.
- The example portfolio appears first. Choose **Clear examples and start mine**, then add your holdings once.
  Every device you sign in on shows the same data.

## Free-tier behaviour to expect
- **It sleeps after about 15 minutes without visits.** The next visit takes up to a minute to wake it.
- **Forecasts are off online.** The forecasting model needs more memory than the free plan has.
- **Live prices depend on Yahoo Finance,** an unofficial source. If Yahoo blocks Render's servers, prices
  fall back to what you type in; everything else keeps working.
- **Keep an occasional backup** (Back up / restore) in case either free service changes its terms.

## Changing things later
- New password: Render → your service → **Environment** → edit `GLIDEPATH_PASSWORD` → Save (it redeploys).
- Sign out every device: change `GLIDEPATH_SECRET` there to any new random text.
- Updates you push to the `glidepath` branch deploy automatically.
