# Recovery Ledger

Personal Polar health dashboard — PWA running on your iPhone, powered by Polar AccessLink API.

## Stack
- **Backend**: FastAPI (Python) + SQLite
- **Frontend**: PWA (installable on iPhone via Safari → Add to Home Screen)
- **Hosting**: Render (free tier)
- **Data**: Polar AccessLink API v3

## Deploy in 5 steps

### 1. Push to GitHub
```bash
git init
git add .
git commit -m "Initial commit"
# Create a new repo on github.com, then:
git remote add origin https://github.com/YOUR_USERNAME/recovery-ledger.git
git push -u origin main
```

### 2. Create a Render account
Go to render.com, sign up with GitHub.

### 3. Create a new Web Service on Render
- Click **New → Web Service**
- Connect your GitHub repo
- Render will detect `render.yaml` automatically
- Select **Free** tier

### 4. Set environment variables in Render dashboard
Under **Environment**, add:
```
POLAR_CLIENT_ID      = 96ac5b20-fa03-4b86-8e8d-3c7482438c1a
POLAR_CLIENT_SECRET  = a304b6e9-0750-45be-943c-38b506bb9a4a
POLAR_REDIRECT_URI   = https://recovery-ledger.onrender.com/oauth/callback
```
> **Note**: Replace `recovery-ledger` with whatever Render names your service.

### 5. Update the Polar redirect URL
Once Render gives you your real URL (e.g. `https://recovery-ledger-xxxx.onrender.com`):
- Go to **admin.polaraccesslink.com**
- Edit your app's Authorization redirect URL to match exactly

### 6. Connect on iPhone
1. Visit your Render URL in Safari
2. Tap the Share button → **Add to Home Screen**
3. Open the app → tap **Connect Polar**
4. Authorize → you'll be redirected back and synced automatically
5. From now on: open the app → tap **Sync** whenever you want fresh data

## Local development
```bash
pip install -r requirements.txt
cp .env.example .env   # fill in your credentials
source .env
uvicorn main:app --reload
# visit http://localhost:8000
# For OAuth to work locally, temporarily set POLAR_REDIRECT_URI=http://localhost:8000/oauth/callback
# and add that URL in admin.polaraccesslink.com
```

## Architecture notes
- **Polar tokens don't expire** — you only authorize once
- **Transaction model**: Activity data uses Polar's transaction API (new-since-last-sync only). Sleep and nightly recharge use date-range queries (full historical access)
- **Render free tier** spins down after 15 min of inactivity — first load after idle takes ~30s. This is normal
- **SQLite persistence**: Render's free tier includes a 1GB persistent disk (configured in `render.yaml`) — your data survives redeploys
