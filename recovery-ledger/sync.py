"""
Sync engine: pulls all available data from Polar AccessLink and writes to SQLite.
Called on-demand from the dashboard or on a schedule.
"""
import statistics
from datetime import date, timedelta
from typing import Optional
from polar_client import PolarClient
from database import Database


def _compute_resting_hr(samples: list) -> Optional[float]:
    """Mean of the lowest 30 valid HR samples (filters dropout artifacts < 40 bpm)."""
    valid = sorted(s for s in samples if s >= 40)
    if not valid:
        return None
    lowest = valid[:30]
    return round(statistics.mean(lowest), 1)


def sync_user_data(
    polar: PolarClient,
    access_token: str,
    polar_user_id: int,
    db: Database,
    days_back: int = 30,
) -> dict:
    today = date.today()
    from_date = today - timedelta(days=days_back)
    result = {"sleep": 0, "recovery": 0, "activity": 0, "hr": 0, "errors": []}

    # ── Sleep (date range query) ───────────────────────────────────────────
    try:
        nights = polar.get_sleep_range(access_token, polar_user_id, from_date, today)
        for night in nights:
            db.upsert_sleep_night(night)
            result["sleep"] += 1
    except Exception as e:
        result["errors"].append(f"sleep: {e}")

    # ── Nightly Recharge (per-day query) ──────────────────────────────────
    cursor = from_date
    while cursor <= today:
        try:
            rec = polar.get_nightly_recharge(access_token, polar_user_id, cursor)
            if rec:
                # API returns date inside the object, but use cursor as the key
                day_key = rec.get("date") or cursor.isoformat()
                db.upsert_recovery(day_key, rec)
                result["recovery"] += 1
        except Exception as e:
            result["errors"].append(f"recovery {cursor}: {e}")
        cursor += timedelta(days=1)

    # ── Activity (transaction model — new data since last sync) ───────────
    try:
        activities = polar.get_activity_transaction(access_token, polar_user_id)
        if activities:
            for act in activities:
                db.upsert_activity(act)
                result["activity"] += 1
    except Exception as e:
        result["errors"].append(f"activity: {e}")

    # ── Continuous HR (per-day query) ─────────────────────────────────────
    cursor = from_date
    while cursor <= today:
        try:
            hr_data = polar.get_continuous_hr(access_token, polar_user_id, cursor)
            if hr_data:
                samples = [
                    s.get("heartRate", 0)
                    for day in hr_data.get("deviceDays", [hr_data])
                    for s in day.get("samples", [])
                ]
                resting = _compute_resting_hr(samples)
                valid = [s for s in samples if s >= 40]
                avg = round(statistics.mean(valid), 1) if valid else None
                if resting:
                    db.upsert_hr_day(cursor.isoformat(), resting, avg, hr_data)
                    result["hr"] += 1
        except Exception as e:
            result["errors"].append(f"hr {cursor}: {e}")
        cursor += timedelta(days=1)

    db.log_sync(
        nights_added=result["sleep"],
        status="ok" if not result["errors"] else "partial",
        message="; ".join(result["errors"]) if result["errors"] else None,
    )
    return result
