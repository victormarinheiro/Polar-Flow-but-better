"""
Recovery Ledger — FastAPI backend.
Serves the PWA and exposes the data API.
"""
import math
import os
import statistics
from datetime import date, timedelta

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from database import Database
from polar_client import PolarClient
from sync import sync_user_data

# ── App setup ──────────────────────────────────────────────────────────────

app = FastAPI(title="Recovery Ledger")
app.mount("/static", StaticFiles(directory="static"), name="static")

database_url = os.environ.get("DATABASE_URL")
if not database_url:
    raise RuntimeError("DATABASE_URL is required. Create a Postgres database and expose its connection string.")

db = Database(database_url)

polar = PolarClient(
    client_id=os.environ["POLAR_CLIENT_ID"],
    client_secret=os.environ["POLAR_CLIENT_SECRET"],
    redirect_uri=os.environ["POLAR_REDIRECT_URI"],
)

# ── Frontend ───────────────────────────────────────────────────────────────

@app.get("/")
async def index():
    return FileResponse("static/index.html")

@app.get("/manifest.json")
async def manifest():
    return FileResponse("static/manifest.json")

@app.get("/sw.js")
async def service_worker():
    return FileResponse("static/sw.js", media_type="application/javascript")

# ── Auth ───────────────────────────────────────────────────────────────────

@app.get("/auth")
async def auth_start():
    """Redirect user to Polar OAuth consent screen."""
    return RedirectResponse(polar.get_auth_url())

@app.get("/oauth/callback")
async def oauth_callback(code: str = None, error: str = None):
    if error or not code:
        raise HTTPException(400, f"OAuth error: {error or 'no code'}")

    # 1. Exchange code for token
    token_data = polar.exchange_code(code)
    access_token = token_data["access_token"]
    polar_user_id = token_data.get("x_user_id") or token_data.get("user_id")

    if not polar_user_id:
        raise HTTPException(500, "Could not determine Polar user ID from token response")

    # 2. Register user with AccessLink (required before any data calls)
    polar.register_user(access_token, int(polar_user_id))

    # 3. Persist token
    db.save_token(access_token, int(polar_user_id))

    # 4. Kick off initial sync in background-ish (fast enough for a redirect)
    return RedirectResponse("/?syncing=1")

@app.get("/auth/status")
async def auth_status():
    token = db.get_token()
    return {"authenticated": token is not None, "last_sync": db.last_sync()}

@app.post("/auth/logout")
async def logout():
    db.clear_token()
    return {"ok": True}

# ── Sync ───────────────────────────────────────────────────────────────────

@app.post("/api/sync")
async def api_sync(days: int = 30):
    token = db.get_token()
    if not token:
        raise HTTPException(401, "Not authenticated — visit /auth first")

    result = sync_user_data(
        polar=polar,
        access_token=token["access_token"],
        polar_user_id=token["polar_user_id"],
        db=db,
        days_back=days,
    )
    return result

# ── Metrics API ────────────────────────────────────────────────────────────

def _recovery_pct(indicator, sub_level) -> float | None:
    if indicator is None or sub_level is None:
        return None
    band = 100 / 6
    return round((indicator - 1) * band + (sub_level / 100) * band, 1)


def _strain_proxy(met_minutes, max_met) -> float | None:
    if not met_minutes or not max_met:
        return None
    return round(21 * (math.log(met_minutes + 1) / math.log(max_met + 1)), 1)


@app.get("/api/metrics")
async def api_metrics(days: int = 30):
    token = db.get_token()
    if not token:
        return JSONResponse({"authenticated": False, "nights": []})

    nights_raw = db.get_nights(days=days)
    if not nights_raw:
        return JSONResponse({"authenticated": True, "nights": [], "last_sync": db.last_sync()})

    met_values = [n["met_minutes"] for n in nights_raw if n.get("met_minutes")]
    max_met = max(met_values) if met_values else 1

    nights = []
    for n in nights_raw:
        nights.append({
            "date":               n["date"],
            "sleep_score":        n["sleep_score"],
            "continuity_score":   n["continuity_score"],
            "efficiency_score":   n["efficiency_score"],
            "rem_score":          n["rem_score"],
            "n3_score":           n["n3_score"],
            "sleep_span_sec":     n["sleep_span_sec"],
            "asleep_sec":         n["asleep_sec"],
            "efficiency_pct":     n["efficiency_pct"],
            "interruptions_total":n["interruptions_total"],
            "interruptions_long": n["interruptions_long"],
            "rem_pct":            n["rem_pct"],
            "deep_pct":           n["deep_pct"],
            "rmssd":              n["rmssd"],
            "recovery_pct":       _recovery_pct(n.get("recovery_indicator"), n.get("recovery_sub_level")),
            "ans_status":         n["ans_status"],
            "resp_interval_ms":   n["resp_interval_ms"],
            "steps":              n["steps"],
            "calories":           n["calories"],
            "met_minutes":        n["met_minutes"],
            "resting_hr":         n["resting_hr"],
            "avg_hr":             n["avg_hr"],
            "strain_proxy":       _strain_proxy(n.get("met_minutes"), max_met),
        })

    return JSONResponse({
        "authenticated": True,
        "nights": nights,
        "last_sync": db.last_sync(),
    })


@app.get("/api/hypnogram")
async def api_hypnogram(days: int = 30):
    """Returns raw hypnogram events for interruption time-of-night analysis."""
    token = db.get_token()
    if not token:
        raise HTTPException(401, "Not authenticated")
    return db.get_hypnogram_events(days=days)


# ── Health check (keeps Render free tier alive) ────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "date": date.today().isoformat()}
