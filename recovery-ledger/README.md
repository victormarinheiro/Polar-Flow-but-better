# Recovery Ledger

Personal Polar health dashboard — PWA frontend, FastAPI backend, Render Free web service, and Render Free Postgres.

## What it syncs

- Sleep Plus Stages from Polar AccessLink `/v3/users/sleep`
- Nightly Recharge from `/v3/users/nightly-recharge`
- Daily activity summaries from `/v3/users/activities/{date}`
- Continuous heart-rate samples from `/v3/users/continuous-heart-rate/{date}` when the device/account exposes them
- Cardio Load / Polar strain from `/v3/users/cardio-load/date`

Raw Polar JSON is stored in Postgres beside parsed dashboard columns so parsing can be corrected later without re-authorising Polar.

## Important limits

- This is a single-user app.
- It does not write anything back to Polar.
- WHOOP strain is not recreated. The dashboard uses Polar Cardio Load strain when available.
- Wake episodes are computed from hypnogram transitions into WAKE because Polar exposes interruption durations, not a native wake episode count.
- Recovery percentage is a display rescale of Polar Nightly Recharge status, not a native Polar 0–100 score.

## Render deployment

### 1. Push to GitHub

```bash
git add .
git commit -m "Update Polar AccessLink integration"
git push
```

### 2. Create services on Render

Create a Web Service from this repo with:

```txt
Root Directory: recovery-ledger
Build Command: pip install -r requirements.txt
Start Command: uvicorn main:app --host 0.0.0.0 --port $PORT
Instance Type: Free
```

Create a Render PostgreSQL database on the Free plan and add its Internal Database URL as `DATABASE_URL` in the web service environment.

### 3. Environment variables

```txt
POLAR_CLIENT_ID=your Polar client id
POLAR_CLIENT_SECRET=your Polar client secret
POLAR_REDIRECT_URI=https://your-render-service.onrender.com/oauth/callback
DATABASE_URL=your Render Postgres internal database URL
APP_USERNAME=your private username
APP_PASSWORD=your long random password
ENABLE_DEBUG_RAW=0
```

`APP_USERNAME` and `APP_PASSWORD` are strongly recommended. Without them, anyone who knows the Render URL can open your dashboard after you have connected Polar.

### 4. Polar redirect URL

In the Polar AccessLink admin dashboard, set the authorization redirect URL exactly to:

```txt
https://your-render-service.onrender.com/oauth/callback
```

No trailing slash.

## Local development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# fill .env, then export it using your preferred shell method
uvicorn main:app --reload
```

For local OAuth, temporarily set:

```txt
POLAR_REDIRECT_URI=http://localhost:8000/oauth/callback
```

and add that exact URL in Polar AccessLink admin.

## Debugging raw Polar payloads

Set this only temporarily:

```txt
ENABLE_DEBUG_RAW=1
```

Then open:

```txt
/api/debug/raw
```

Turn it off afterwards. Raw health payloads are sensitive.
