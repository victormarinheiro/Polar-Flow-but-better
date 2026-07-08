"""
Recovery Ledger — FastAPI backend.
Serves the PWA and exposes the data API.
"""
import hmac
import math
import os
from datetime import date

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles

from database import Database
from polar_client import PolarClient
from sync import sync_user_data

# ── App setup ──────────────────────────────────────────────────────────────

app = FastAPI(title="Recovery Ledger")
app.mount("/static", StaticFiles(directory="static"), name="static")

security = HTTPBasic(auto_error=False)
app_username = os.environ.get("APP_USERNAME")
app_password = os.environ.get("APP_PASSWORD")


def require_app_auth(credentials: HTTPBasicCredentials | None = Depends(security)):
    """
    Optional single-user HTTP Basic guard.
    Set APP_USERNAME and APP_PASSWORD in Render to protect the public URL.
    """
    if not app_username or not app_password:
        return True
    if not credentials:
        raise HTTPException(status_code=401, detail="Authentication required", headers={"WWW-Authenticate": "Basic"})
    ok_user = hmac.compare_digest(credentials.username, app_username)
    ok_pass = hmac.compare_digest(credentials.password, app_password)
    if not (ok_user and ok_pass):
        raise HTTPException(status_code=401, detail="Invalid credentials", headers={"WWW-Authenticate": "Basic"})
    return True


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
async def index(_: bool = Depends(require_app_auth)):
    return FileResponse("static/index.html")

@app.get("/manifest.json")
async def manifest(_: bool = Depends(require_app_auth)):
    return FileResponse("static/manifest.json")

@app.get("/sw.js")
async def service_worker(_: bool = Depends(require_app_auth)):
    return FileResponse("static/sw.js", media_type="application/javascript")

# ── Auth ───────────────────────────────────────────────────────────────────

@app.get("/auth")
async def auth_start(_: bool = Depends(require_app_auth)):
    return RedirectResponse(polar.get_auth_url())

@app.get("/oauth/callback")
async def oauth_callback(code: str = None, error: str = None, _: bool = Depends(require_app_auth)):
    if error or not code:
        raise HTTPException(400, f"OAuth error: {error or 'no code'}")

    token_data = polar.exchange_code(code)
    access_token = token_data["access_token"]
    polar_user_id = token_data.get("x_user_id") or token_data.get("user_id")

    if not polar_user_id:
        raise HTTPException(500, "Could not determine Polar user ID from token response")

    polar.register_user(access_token, int(polar_user_id))
    db.save_token(access_token, int(polar_user_id))

    return RedirectResponse("/?syncing=1")

@app.get("/auth/status")
async def auth_status(_: bool = Depends(require_app_auth)):
    token = db.get_token()
    return {"authenticated": token is not None, "last_sync": db.last_sync()}

@app.post("/auth/logout")
async def logout(_: bool = Depends(require_app_auth)):
    db.clear_token()
    return {"ok": True}

# ── Sync ───────────────────────────────────────────────────────────────────

@app.post("/api/sync")
async def api_sync(days: int = 30, _: bool = Depends(require_app_auth)):
    token = db.get_token()
    if not token:
        raise HTTPException(401, "Not authenticated — visit /auth first")

    days = max(1, min(days, 365))
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
    """
    Polar exposes Nightly Recharge status as an ordinal value, not a 0-100 score.
    This app rescales it only for display continuity. It is not Polar's own score.
    """
    if indicator is None:
        return None
    try:
        indicator = float(indicator)
    except (TypeError, ValueError):
        return None
    sub_level = 50 if sub_level is None else float(sub_level)
    band = 100 / 6
    return round((indicator - 1) * band + (sub_level / 100) * band, 1)


def _strain_display(cardio_strain) -> float | None:
    """Use Polar Cardio Load strain directly. Do not invent WHOOP-like strain here."""
    if cardio_strain is None:
        return None
    try:
        return round(float(cardio_strain), 1)
    except (TypeError, ValueError):
        return None


@app.get("/api/metrics")
async def api_metrics(days: int = 30, _: bool = Depends(require_app_auth)):
    token = db.get_token()
    if not token:
        return JSONResponse({"authenticated": False, "nights": []})

    days = max(1, min(days, 365))
    nights_raw = db.get_nights(days=days)
    if not nights_raw:
        return JSONResponse({"authenticated": True, "nights": [], "last_sync": db.last_sync()})

    nights = []
    for n in nights_raw:
        nights.append({
            "date":                    n["date"],
            "sleep_score":             n["sleep_score"],
            "continuity_score":        n["continuity_score"],
            "efficiency_score":        n["efficiency_score"],
            "rem_score":               n["rem_score"],
            "n3_score":                n["n3_score"],
            "sleep_span_sec":          n["sleep_span_sec"],
            "asleep_sec":              n["asleep_sec"],
            "efficiency_pct":          n["efficiency_pct"],
            "interruptions_total":     n["interruptions_total"],
            "interruptions_long":      n["interruptions_long"],
            "total_interruption_sec":  n.get("total_interruption_sec"),
            "short_interruption_sec":  n.get("short_interruption_sec"),
            "long_interruption_sec":   n.get("long_interruption_sec"),
            "wake_count":              n.get("wake_count"),
            "sleep_cycles":            n.get("sleep_cycles"),
            "rem_pct":                 n["rem_pct"],
            "deep_pct":                n["deep_pct"],
            "rmssd":                   n["rmssd"],
            "recovery_pct":            _recovery_pct(n.get("recovery_indicator"), n.get("recovery_sub_level")),
            "nightly_recharge_status": n.get("recovery_indicator"),
            "ans_status":              n["ans_status"],
            "resp_rate_bpm":           n.get("resp_rate_bpm"),
            "steps":                   n["steps"],
            "calories":                n["calories"],
            "active_calories":         n.get("active_calories"),
            "daily_activity":          n.get("daily_activity"),
            "met_minutes":             n["met_minutes"],
            "cardio_load_status":      n.get("cardio_load_status"),
            "cardio_load":             n.get("cardio_load"),
            "cardio_strain":           n.get("cardio_strain"),
            "tolerance":               n.get("tolerance"),
            "cardio_load_ratio":       n.get("cardio_load_ratio"),
            "resting_hr":              n["resting_hr"],
            "avg_hr":                  n["avg_hr"],
            "strain_proxy":            _strain_display(n.get("cardio_strain")),
        })

    return JSONResponse({
        "authenticated": True,
        "nights": nights,
        "last_sync": db.last_sync(),
        "notes": {
            "recovery_pct": "Rescaled from Polar nightly_recharge_status for display; not a native Polar 0-100 score.",
            "strain_proxy": "Uses Polar Cardio Load strain directly when available; no WHOOP strain clone is computed.",
            "wake_count": "Computed as transitions into WAKE in Polar hypnogram because Polar exposes interruption durations, not a wake episode count.",
        }
    })


@app.get("/api/hypnogram")
async def api_hypnogram(days: int = 30, _: bool = Depends(require_app_auth)):
    token = db.get_token()
    if not token:
        raise HTTPException(401, "Not authenticated")
    return db.get_hypnogram_events(days=max(1, min(days, 365)))


@app.get("/api/debug/raw")
async def debug_raw(_: bool = Depends(require_app_auth)):
    if os.environ.get("ENABLE_DEBUG_RAW") != "1":
        raise HTTPException(404, "Debug endpoint disabled")
    token = db.get_token()
    if not token:
        raise HTTPException(401, "Not authenticated")
    return db.get_latest_raw_samples()

# ── Health check ───────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "date": date.today().isoformat()}
