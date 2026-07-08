"""
Sync engine: pulls Polar AccessLink data and writes it to Postgres.
Called when the dashboard opens or when the user taps Sync.
"""
import statistics
from datetime import date, timedelta
from typing import Optional

from database import Database
from polar_client import PolarClient


def _compute_resting_hr(samples: list[int]) -> Optional[float]:
    """Mean of the lowest 30 valid daily HR samples. Transparent proxy, not Polar RHR."""
    valid = sorted(s for s in samples if s >= 40)
    if not valid:
        return None
    lowest = valid[:30]
    return round(statistics.mean(lowest), 1)


def _extract_hr_samples(hr_data: dict) -> list[int]:
    """
    Current AccessLink continuous HR payload uses:
      heart_rate_samples: [{heart_rate: 63, sample_time: "00:02:08"}, ...]
    Keep fallbacks for older Claude-generated assumptions so existing data/debugging does not break.
    """
    if not hr_data:
        return []

    samples = []

    if isinstance(hr_data.get("heart_rate_samples"), list):
        for sample in hr_data["heart_rate_samples"]:
            value = sample.get("heart_rate")
            if value is not None:
                samples.append(int(value))

    if isinstance(hr_data.get("samples"), list):
        for sample in hr_data["samples"]:
            value = sample.get("heartRate") or sample.get("heart_rate")
            if value is not None:
                samples.append(int(value))

    for day in hr_data.get("deviceDays", []):
        for sample in day.get("samples", []):
            value = sample.get("heartRate") or sample.get("heart_rate")
            if value is not None:
                samples.append(int(value))

    return samples


def sync_user_data(
    polar: PolarClient,
    access_token: str,
    polar_user_id: int,
    db: Database,
    days_back: int = 30,
) -> dict:
    today = date.today()
    from_date = today - timedelta(days=days_back)
    result = {"sleep": 0, "recovery": 0, "activity": 0, "hr": 0, "cardio_load": 0, "errors": []}

    # Sleep: official /users/sleep + per-date backfill.
    try:
        nights = polar.get_sleep_range(access_token, polar_user_id, from_date, today)
        for night in nights:
            db.upsert_sleep_night(night)
            result["sleep"] += 1
    except Exception as e:
        result["errors"].append(f"sleep: {e}")

    # Nightly Recharge: official /users/nightly-recharge + per-date backfill.
    try:
        recharges = polar.get_nightly_recharge_range(access_token, polar_user_id, from_date, today)
        for rec in recharges:
            day_key = rec.get("date")
            if day_key:
                db.upsert_recovery(day_key, rec)
                result["recovery"] += 1
    except Exception as e:
        result["errors"].append(f"recovery: {e}")

    # Daily activity: non-deprecated daily endpoint, not the old transaction model.
    try:
        activities = polar.get_activity_range(access_token, polar_user_id, from_date, today)
        for act in activities:
            db.upsert_activity(act)
            result["activity"] += 1
    except Exception as e:
        result["errors"].append(f"activity: {e}")

    # Cardio load / Polar strain.
    try:
        loads = polar.get_cardio_load_range(access_token, from_date, today)
        for load in loads:
            db.upsert_cardio_load(load)
            result["cardio_load"] += 1
    except Exception as e:
        result["errors"].append(f"cardio_load: {e}")

    # Continuous HR.
    cursor = from_date
    while cursor <= today:
        try:
            hr_data = polar.get_continuous_hr(access_token, polar_user_id, cursor)
            if hr_data:
                samples = _extract_hr_samples(hr_data)
                valid = [s for s in samples if s >= 40]
                resting = _compute_resting_hr(valid)
                avg = round(statistics.mean(valid), 1) if valid else None
                if resting is not None or avg is not None:
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
